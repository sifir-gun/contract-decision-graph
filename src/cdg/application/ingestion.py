"""Ingestion du corpus : lecture des fichiers (manifeste, textes, fiches), puis
construction des extraits embarqués par le port `Embedder`.

Le nettoyage et le découpage sont des fonctions pures de `domain/corpus.py`.
L'écriture en base est faite par l'adaptateur PostgreSQL, appelé par la CLI.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from cdg.domain.corpus import (
    Article,
    ChunkRow,
    Fiche,
    Manifest,
    Window,
    by_domain,
    check_chain,
    chunk,
    citations,
    claim_lines,
    embedded_hash,
    embedded_text,
    fiche_window,
    in_force,
    parse_eurlex,
    parse_legifrance,
)
from cdg.ports.embedder import Embedder

ROOT = Path(__file__).resolve().parents[3] / "data" / "corpus"
RAW, FICHES, MANIFEST = ROOT / "raw", ROOT / "fiches", ROOT / "manifest.yaml"


def load_manifest(path: Path | None = None) -> Manifest:
    text = (path or MANIFEST).read_text(encoding="utf-8")
    return Manifest(sources=yaml.safe_load(text)["sources"])


def articles(manifest: Manifest | None = None) -> Iterator[tuple[Article, list[str]]]:
    """Articles admis, nettoyés, avec les types de clause qu'ils peuvent justifier : une
    `Article` par version, dans l'ordre, toutes sous la même référence ; enchaînement des
    versions contrôlé (`check_chain`)."""
    manifest = manifest or load_manifest()
    parsers = {"legifrance": parse_legifrance, "eurlex": parse_eurlex}
    grouped: dict[tuple[str, str], list[str]] = {}
    for path, key in manifest.files():
        grouped.setdefault(key, []).append(path)
    for (source_id, number), paths in grouped.items():
        source = manifest.sources[source_id]
        # première version : date propre à l'article (ajouté plus tard), sinon celle de
        # la source ; version suivante : la sienne, obligatoire
        dates = [
            source.get("retrieved_at_overrides", {}).get(number, source["retrieved_at"])
        ]
        for path, later in zip(
            paths[1:], manifest.later(source_id, number), strict=True
        ):
            if "retrieved_at" not in later:
                raise ValueError(
                    f"{path} : date de récupération (retrieved_at) absente du manifeste"
                )
            dates.append(later["retrieved_at"])
        versions = []
        for path, retrieved_at in zip(paths, dates, strict=True):
            article = parsers[source["format"]](
                (RAW / path).read_text(encoding="utf-8")
            )
            if article.article != number:
                raise ValueError(
                    f"{path} : titre « Article {article.article} », attendu {number}"
                )
            versions.append(
                (
                    path,
                    replace(
                        article,
                        source_id=source_id,
                        reference=f"{source['citation']}, art. {number}",
                        retrieved_at=retrieved_at,
                    ),
                )
            )
        check_chain(versions)
        for _, article in versions:
            yield article, source["articles"][number]


def load_fiches(directory: Path | None = None) -> list[Fiche]:
    fiches = []
    for path in sorted((directory or FICHES).glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta = yaml.safe_load(front)
        fiches.append(
            Fiche(
                id=meta["id"],
                title=meta["titre"],
                kinds=meta["clauses"],
                body=body.strip(),
                # versions relues : {source: {article: [débuts de validité]}}
                verified={
                    (source_id, str(article)): list(starts)
                    for source_id, by_article in meta.get(
                        "versions_verifiees", {}
                    ).items()
                    for article, starts in by_article.items()
                },
            )
        )
    return fiches


def fiche_reference(fiche: Fiche) -> str:
    return f"Fiche projet : {fiche.title}"


@cache
def reference_texts(on: date | None) -> dict[str, dict[str, str]]:
    """Texte de chaque référence citable du corpus (article entier, ou corps de la fiche)
    et sa source, dans sa version en vigueur à la date d'analyse `on` : l'état d'un
    contrat ne garde que le nom des références retenues. Sans date (ancien fil), seules
    les références à une seule version : jamais une version choisie au hasard. Lus dans
    les fichiers du corpus au premier appel pour cette date, pour afficher un dossier."""
    by_reference: dict[str, list[Article]] = {}
    for article, _ in articles():
        by_reference.setdefault(article.reference, []).append(article)
    texts = {}
    for reference, found in by_reference.items():
        chosen = [
            a
            for a in found
            if (
                len(found) == 1
                if on is None
                else in_force(a.valid_from, a.valid_until, on)
            )
        ]
        if chosen:
            texts[reference] = {"source": chosen[0].source_id, "texte": chosen[0].text}
    versions = _article_versions()
    for fiche in load_fiches():
        start, end = _fiche_window(fiche, versions)
        if on is None or in_force(start, end, on):
            texts[fiche_reference(fiche)] = {"source": fiche.id, "texte": fiche.body}
    return texts


# --- Extraits à ingérer ------------------------------------------------------------------


Pending = list[tuple[dict[str, Any], str, list[str]]]


def pending_chunks(max_words: int) -> Pending:
    """Extraits des articles admis et des fiches, avant embedding : métadonnées, texte à
    embarquer, types de clause que la source peut justifier.

    Le texte embarqué est précédé d'un en-tête écrit par le code (`meta["header"]`) pour
    la recherche ; le texte stocké reste celui de l'article, cité tel quel. Une fiche vaut
    sur la période des versions qu'elle a relues (`fiche_window`) : elle les paraphrase,
    elle expire avec.
    """
    pending: Pending = []
    versions = _article_versions()
    for article, kinds in articles():
        header = article.reference + (
            f" — {article.heading}" if article.heading else ""
        )
        amendment = (
            f"{article.amendment[0]} : {article.amendment[1]}"
            if article.amendment
            else None
        )
        for index, text in enumerate(chunk(article.text, max_words)):
            meta = {
                "source_id": article.source_id,
                "reference": article.reference,
                "text": text,
                "header": header,
                "article": article.article,
                "chunk_index": index,
                "valid_from": article.valid_from,
                "valid_until": article.valid_until,
                "amendment": amendment,
                "note": article.note,
                "retrieved_at": article.retrieved_at,
            }
            pending.append((meta, embedded_text(header, text), kinds))
    for fiche in load_fiches():
        reference = fiche_reference(fiche)
        start, end = _fiche_window(fiche, versions)
        for index, text in enumerate(chunk(fiche.body, max_words)):
            meta = {
                "source_id": fiche.id,
                "reference": reference,
                "text": text,
                "header": reference,
                "chunk_index": index,
                "valid_from": start,
                "valid_until": end,
            }
            pending.append((meta, embedded_text(reference, text), fiche.kinds))
    return pending


def _article_versions() -> dict[tuple[str, str], list[Window]]:
    """Versions de chaque article admis, dans l'ordre : (début, fin) de validité."""
    versions: dict[tuple[str, str], list[Window]] = {}
    for a, _ in articles():
        versions.setdefault((a.source_id, a.article), []).append(
            (a.valid_from, a.valid_until)
        )
    return versions


def _fiche_window(
    fiche: Fiche, versions: dict[tuple[str, str], list[Window]]
) -> Window:
    """Période de validité d'une fiche, d'après les articles qu'elle cite et les
    versions qu'elle déclare avoir relues (`fiche_window`)."""
    cited = {c for line in claim_lines(fiche.body) for c in citations(line)}
    return fiche_window(fiche.id, cited, fiche.verified, versions)


def source_validities() -> dict[str, date | None]:
    """Fin de validité de chaque source du corpus, par référence : pour un article, la
    fin de sa dernière version ; pour une fiche, la fin de sa période de validité
    (contrôle des échéances, `scripts/echeances_corpus.py`)."""
    sources: dict[str, date | None] = {}
    for a, _ in articles():  # versions dans l'ordre : la dernière l'emporte
        sources[a.reference] = a.valid_until
    versions = _article_versions()
    for fiche in load_fiches():
        sources[fiche_reference(fiche)] = _fiche_window(fiche, versions)[1]
    return sources


def rows(embedder: Embedder, max_words: int, passage_prefix: str) -> list[ChunkRow]:
    """Extraits à ingérer, un par domaine d'indexation, embarqués par le port. Le préfixe
    de passage du modèle (configuration), ajouté par l'adaptateur, entre dans l'empreinte
    du texte embarqué : le changer réindexe tout le corpus."""
    pending = pending_chunks(max_words)
    vectors = embedder.embed_passages([embedded for _, embedded, _ in pending])
    return [
        # une ligne par domaine d'indexation, avec les types de clause de ce domaine que la
        # source déclare (manifeste, fiches) ; validés par ChunkRow
        ChunkRow.model_validate(
            {
                "domain": domain,
                "kinds": domain_kinds,
                "embedding_model": embedder.model,
                "embedding": vector,
                "embedded_hash": embedded_hash(passage_prefix, embedded),
                **meta,
            }
        )
        for (meta, embedded, kinds), vector in zip(pending, vectors, strict=True)
        for domain, domain_kinds in by_domain(kinds)
    ]

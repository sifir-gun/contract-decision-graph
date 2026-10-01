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
    by_domain,
    chunk,
    citations,
    claim_lines,
    context_header,
    embedded_hash,
    embedded_text,
    paragraph_marks,
    parse_eurlex,
    parse_legifrance,
)
from cdg.ports.embedder import Embedder

ROOT = Path(__file__).resolve().parents[3] / "data" / "corpus"
RAW, FICHES, MANIFEST = ROOT / "raw", ROOT / "fiches", ROOT / "manifest.yaml"


def load_manifest(path: Path = MANIFEST) -> Manifest:
    return Manifest(sources=yaml.safe_load(path.read_text(encoding="utf-8"))["sources"])


def articles(manifest: Manifest | None = None) -> Iterator[tuple[Article, list[str]]]:
    """Articles admis, nettoyés, avec les types de clause qu'ils peuvent justifier."""
    manifest = manifest or load_manifest()
    parsers = {"legifrance": parse_legifrance, "eurlex": parse_eurlex}
    for path, (source_id, number) in manifest.files():
        source = manifest.sources[source_id]
        article = parsers[source["format"]]((RAW / path).read_text(encoding="utf-8"))
        if article.article != number:
            raise ValueError(
                f"{path} : titre « Article {article.article} », attendu {number}"
            )
        # intitulés officiels de la hiérarchie (Légifrance, lus sur la page de l'article) ;
        # EUR-Lex donne l'intitulé de l'article dans le fichier
        levels = source.get("hierarchy", {}).get(number, {}).get("levels", [])
        if source["format"] == "legifrance" and not levels:
            raise ValueError(
                f"{source_id} : hiérarchie officielle absente du manifeste pour "
                f"l'article {number} (ADR 006)"
            )
        yield (
            replace(
                article,
                source_id=source_id,
                source_title=source["title"],
                hierarchy=tuple(levels),
                reference=f"{source['citation']}, art. {number}",
                # date propre à l'article (ajouté plus tard), sinon celle de la source
                retrieved_at=source.get("retrieved_at_overrides", {}).get(
                    number, source["retrieved_at"]
                ),
            ),
            source["articles"][number],
        )


def load_fiches(directory: Path = FICHES) -> list[Fiche]:
    fiches = []
    for path in sorted(directory.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta = yaml.safe_load(front)
        fiches.append(
            Fiche(
                id=meta["id"],
                title=meta["titre"],
                kinds=meta["clauses"],
                body=body.strip(),
            )
        )
    return fiches


def fiche_reference(fiche: Fiche) -> str:
    return f"Fiche projet : {fiche.title}"


@cache
def reference_texts() -> dict[str, dict[str, str]]:
    """Texte de chaque référence citable du corpus (article entier, ou corps de la fiche)
    et sa source : l'état d'un contrat ne garde que le nom des références retenues. Lus
    dans les fichiers du corpus au premier appel, pour afficher un dossier."""
    texts = {
        article.reference: {"source": article.source_id, "texte": article.text}
        for article, _ in articles()
    }
    for fiche in load_fiches():
        texts[fiche_reference(fiche)] = {"source": fiche.id, "texte": fiche.body}
    return texts


# --- Extraits à ingérer ------------------------------------------------------------------


Pending = list[tuple[dict[str, Any], str, list[str]]]


def pending_chunks(max_words: int) -> Pending:
    """Extraits des articles admis et des fiches, avant embedding : métadonnées, texte à
    embarquer, types de clause que la source peut justifier.

    Le texte embarqué est précédé d'un en-tête écrit par le code (`meta["header"]`) pour
    la recherche ; le texte stocké reste celui de l'article, cité tel quel. Une fiche prend la plus proche
    des fins de validité des articles qu'elle cite : elle les paraphrase, elle expire avec.
    """
    pending: Pending = []
    validity = _article_validity()
    for article, kinds in articles():
        amendment = (
            f"{article.amendment[0]} : {article.amendment[1]}"
            if article.amendment
            else None
        )
        texts = chunk(
            article.text,
            _room(
                max_words,
                context_header(
                    article.reference,
                    article.source_title,
                    hierarchy=article.hierarchy,
                    heading=article.heading,
                    **_LONGEST_POSITION,
                ),
            ),
        )
        for index, text in enumerate(texts):
            header = context_header(
                article.reference,
                article.source_title,
                hierarchy=article.hierarchy,
                heading=article.heading,
                paragraphs=paragraph_marks(text),
                index=index,
                count=len(texts),
            )
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
        texts = chunk(
            fiche.body,
            _room(max_words, context_header(reference, "", **_LONGEST_POSITION)),
        )
        for index, text in enumerate(texts):
            header = context_header(reference, "", index=index, count=len(texts))
            meta = {
                "source_id": fiche.id,
                "reference": reference,
                "text": text,
                "header": header,
                "chunk_index": index,
                "valid_until": _fiche_validity(fiche, validity),
            }
            pending.append((meta, embedded_text(header, text), fiche.kinds))
    return pending


# position la plus longue en mots dans un en-tête : « paragraphes … à … » (seuls le
# premier et le dernier sont nommés) et « extrait … sur … »
_LONGEST_POSITION: dict[str, Any] = {"paragraphs": ("0", "0"), "index": 0, "count": 2}


def _room(max_words: int, header: str) -> int:
    """Mots laissés au texte d'un extrait : l'en-tête et le texte embarqués ensemble
    tiennent dans `max_words`, la borne qui garde un extrait sous le contexte du
    modèle."""
    room = max_words - len(header.split())
    if room < 1:
        raise ValueError(
            f"en-tête de {len(header.split())} mots, plus long que le découpage "
            f"({max_words} mots) : {header!r}"
        )
    return room


def _article_validity() -> dict[tuple[str, str], date | None]:
    return {(a.source_id, a.article): a.valid_until for a, _ in articles()}


def _fiche_validity(
    fiche: Fiche, validity: dict[tuple[str, str], date | None]
) -> date | None:
    """Une fiche prend la plus proche des fins de validité des articles qu'elle cite :
    elle les paraphrase, elle expire avec."""
    cited = {c for line in claim_lines(fiche.body) for c in citations(line)}
    return min((end for c in cited if (end := validity[c]) is not None), default=None)


def source_validities() -> dict[str, date | None]:
    """Fin de validité de chaque source du corpus, par référence : articles du
    manifeste et fiches (contrôle des échéances, `scripts/echeances_corpus.py`)."""
    validity = _article_validity()
    sources: dict[str, date | None] = {
        a.reference: a.valid_until for a, _ in articles()
    }
    for fiche in load_fiches():
        sources[fiche_reference(fiche)] = _fiche_validity(fiche, validity)
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

"""Ingestion du corpus : lecture des fichiers (manifeste, textes, fiches), puis
construction des extraits embarqués par le port `Embedder`.

Le nettoyage et le découpage sont des fonctions pures de `domain/corpus.py`.
L'écriture en base est faite par l'adaptateur PostgreSQL, appelé par la CLI.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
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
        yield (
            replace(
                article,
                source_id=source_id,
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


# --- Extraits à ingérer ------------------------------------------------------------------


def rows(embedder: Embedder, max_words: int) -> list[ChunkRow]:
    """Extraits des articles admis et des fiches, un par domaine d'indexation.

    Le texte embarqué est précédé de la référence (et de l'intitulé) pour la recherche ;
    le texte stocké reste celui de l'article, cité tel quel. Une fiche prend la plus proche
    des fins de validité des articles qu'elle cite : elle les paraphrase, elle expire avec.
    """
    pending: list[tuple[dict[str, Any], str, list[str]]] = []
    validity: dict[tuple[str, str], date | None] = {}
    for article, kinds in articles():
        validity[(article.source_id, article.article)] = article.valid_until
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
                "article": article.article,
                "chunk_index": index,
                "valid_from": article.valid_from,
                "valid_until": article.valid_until,
                "amendment": amendment,
                "note": article.note,
                "retrieved_at": article.retrieved_at,
            }
            pending.append((meta, f"{header}\n{text}", kinds))
    for fiche in load_fiches():
        reference = f"Fiche projet : {fiche.title}"
        cited = {c for line in claim_lines(fiche.body) for c in citations(line)}
        ends = [end for c in cited if (end := validity[c]) is not None]
        for index, text in enumerate(chunk(fiche.body, max_words)):
            meta = {
                "source_id": fiche.id,
                "reference": reference,
                "text": text,
                "chunk_index": index,
                "valid_until": min(ends, default=None),
            }
            pending.append((meta, f"{reference}\n{text}", fiche.kinds))
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
                **meta,
            }
        )
        for (meta, _, kinds), vector in zip(pending, vectors, strict=True)
        for domain, domain_kinds in by_domain(kinds)
    ]

"""Ingestion du corpus : lecture des fichiers (manifeste, textes, fiches), puis
construction des extraits embarqués par le port `Embedder`.

Le nettoyage et le découpage sont des fonctions pures de `domain/corpus.py`.
L'écriture en base est faite par l'adaptateur PostgreSQL, appelé par la CLI.
"""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import yaml

from cdg.domain.corpus import (
    Article,
    ChunkRow,
    Fiche,
    Manifest,
    chunk,
    parse_eurlex,
    parse_legifrance,
)
from cdg.ports.embedder import Embedder

ROOT = Path(__file__).resolve().parents[3] / "data" / "corpus"
RAW, FICHES, MANIFEST = ROOT / "raw", ROOT / "fiches", ROOT / "manifest.yaml"


def load_manifest(path: Path = MANIFEST) -> Manifest:
    return Manifest(sources=yaml.safe_load(path.read_text(encoding="utf-8"))["sources"])


def articles(manifest: Manifest | None = None) -> Iterator[tuple[Article, list[str]]]:
    """Articles admis, nettoyés, avec leurs domaines d'indexation."""
    manifest = manifest or load_manifest()
    parsers = {"legifrance": parse_legifrance, "eurlex": parse_eurlex}
    for path, (source_id, number) in manifest.files():
        source = manifest.sources[source_id]
        article = parsers[source["format"]]((RAW / path).read_text(encoding="utf-8"))
        if article.article != number:
            raise ValueError(f"{path} : titre « Article {article.article} », attendu {number}")
        yield (
            replace(
                article,
                source_id=source_id,
                reference=f"{source['citation']}, art. {number}",
                retrieved_at=source["retrieved_at"],
            ),
            source["articles"][number],
        )


def load_fiches(directory: Path = FICHES) -> list[Fiche]:
    fiches = []
    for path in sorted(directory.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta = yaml.safe_load(front)
        fiches.append(
            Fiche(id=meta["id"], title=meta["titre"], domains=meta["domaines"], body=body.strip())
        )
    return fiches


# --- Extraits à ingérer ------------------------------------------------------------------


def rows(embedder: Embedder, max_words: int) -> list[ChunkRow]:
    """Extraits des articles admis et des fiches, un par domaine d'indexation.

    Le texte embarqué est précédé de la référence (et de l'intitulé) pour la recherche ;
    le texte stocké reste celui de l'article, cité tel quel.
    """
    pending: list[tuple[dict, str, list[str]]] = []
    for article, domains in articles():
        header = article.reference + (f" — {article.heading}" if article.heading else "")
        amendment = (
            f"{article.amendment[0]} : {article.amendment[1]}" if article.amendment else None
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
            pending.append((meta, f"{header}\n{text}", domains))
    for fiche in load_fiches():
        reference = f"Fiche projet : {fiche.title}"
        for index, text in enumerate(chunk(fiche.body, max_words)):
            meta = {
                "source_id": fiche.id,
                "reference": reference,
                "text": text,
                "chunk_index": index,
            }
            pending.append((meta, f"{reference}\n{text}", fiche.domains))
    vectors = embedder.embed_passages([embedded for _, embedded, _ in pending])
    return [
        ChunkRow(domain=domain, embedding_model=embedder.model, embedding=vector, **meta)
        for (meta, _, domains), vector in zip(pending, vectors, strict=True)
        for domain in domains
    ]

"""Corpus RAG dans PostgreSQL (table rag_chunks) : migrations, contrôle de dimension,
insertion, synchronisation et recherche exacte filtrée."""

from pathlib import Path

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from cdg.domain.corpus import ChunkRow
from cdg.domain.state import Domain
from cdg.ports.embedder import Embedder
from cdg.ports.retriever import Passage

MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
# 001 exige la variable psql `app_password` : appliquée seulement par l'init Docker
RAG_MIGRATIONS = sorted(p for p in MIGRATIONS.glob("0*.sql") if not p.name.startswith("001_"))


class RagStoreError(Exception):
    """Schéma du corpus incompatible avec la configuration."""


def column_dimension(conninfo: str) -> int:
    """Dimension déclarée de rag_chunks.embedding (atttypmod d'une colonne vector)."""
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'rag_chunks'::regclass AND attname = 'embedding'"
        ).fetchone()
    return row[0]


def setup(admin_conninfo: str, dimension: int) -> None:
    """Applique les migrations du corpus (002, 003…, idempotentes), puis vérifie la dimension."""
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        for migration in RAG_MIGRATIONS:  # plusieurs commandes par fichier, sans paramètre
            conn.execute(migration.read_text(encoding="utf-8"))
    actual = column_dimension(admin_conninfo)
    if actual != dimension:
        raise RagStoreError(
            f"rag_chunks.embedding est en dimension {actual}, la configuration attend "
            f"{dimension} : modèle d'embedding et migration à mettre en cohérence"
        )


def insert(admin_conninfo: str, rows: list[ChunkRow]) -> int:
    """Insère les extraits ; un extrait déjà présent (même hash, même modèle) est ignoré."""
    inserted = 0
    with psycopg.connect(admin_conninfo) as conn:
        register_vector(conn)
        for row in rows:
            cursor = conn.execute(
                "INSERT INTO rag_chunks (domain, source_id, reference, text, content_hash,"
                " embedding_model, embedding, article, chunk_index, valid_from, valid_until,"
                " amendment, note, retrieved_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (domain, content_hash, embedding_model) DO NOTHING",
                (
                    row.domain,
                    row.source_id,
                    row.reference,
                    row.text,
                    row.content_hash,
                    row.embedding_model,
                    Vector(row.embedding),
                    row.article,
                    row.chunk_index,
                    row.valid_from,
                    row.valid_until,
                    row.amendment,
                    row.note,
                    row.retrieved_at,
                ),
            )
            inserted += cursor.rowcount
    return inserted


def search(
    conninfo: str, domain: Domain, query: list[float], *, k: int, model: str
) -> list[Passage]:
    """Recherche exacte : filtre (domaine, modèle) AVANT l'ordre par distance cosinus."""
    with psycopg.connect(conninfo) as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT id, domain, source_id, reference, text, embedding <=> %s AS distance,"
            " valid_until, note"
            " FROM rag_chunks WHERE domain = %s AND embedding_model = %s"
            " ORDER BY distance, id LIMIT %s",
            (Vector(query), domain, model, k),
        ).fetchall()
    return [
        Passage(
            id=r[0],
            domain=r[1],
            source_id=r[2],
            reference=r[3],
            text=r[4],
            distance=float(r[5]),
            valid_until=r[6],
            note=r[7],
        )
        for r in rows
    ]


class PgvectorRetriever:
    """Adaptateur du port Retriever : vecteur de la requête par l'Embedder, puis recherche
    exacte filtrée sur le domaine et sur le modèle de cet Embedder."""

    def __init__(self, conninfo: str, embedder: Embedder):
        self._conninfo = conninfo
        self._embedder = embedder

    def search(self, domain: Domain, query: str, *, k: int) -> list[Passage]:
        vector = self._embedder.embed_query(query)
        return search(self._conninfo, domain, vector, k=k, model=self._embedder.model)


# métadonnées stockées avec le texte : si l'une change (fin de validité, note…),
# l'extrait est remplacé, même quand le texte, donc son empreinte, est identique
_METADATA = (
    "reference",
    "article",
    "chunk_index",
    "valid_from",
    "valid_until",
    "amendment",
    "note",
    "retrieved_at",
)


def sync(admin_conninfo: str, rows: list[ChunkRow], model: str) -> dict:
    """Aligne rag_chunks sur le corpus, source par source, pour un modèle d'embedding :
    supprime les extraits disparus (texte nettoyé autrement, article retiré) ou dont une
    métadonnée a changé (fin de validité…), insère les nouveaux. Rejouable : un second
    passage ne change rien."""
    wanted: dict[str, set[tuple]] = {}
    for row in rows:
        key = (row.domain, row.content_hash, *(getattr(row, f) for f in _METADATA))
        wanted.setdefault(row.source_id, set()).add(key)
    deleted = 0
    with psycopg.connect(admin_conninfo) as conn:
        existing = conn.execute(
            "SELECT id, source_id, domain, content_hash, " + ", ".join(_METADATA) + " FROM"
            " rag_chunks WHERE embedding_model = %s",
            (model,),
        ).fetchall()
        stale = [r[0] for r in existing if tuple(r[2:]) not in wanted.get(r[1], set())]
        if stale:
            deleted = conn.execute("DELETE FROM rag_chunks WHERE id = ANY(%s)", (stale,)).rowcount
    inserted = insert(admin_conninfo, rows)
    return {
        "inserted": inserted,
        "deleted": deleted,
        "unchanged": len(rows) - inserted,
        "chunks": len(rows),
    }

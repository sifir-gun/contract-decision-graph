"""Corpus RAG dans PostgreSQL (table rag_chunks) : migration 002 et contrôle de dimension."""

import hashlib
from pathlib import Path

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from cdg.state import Domain

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "002_rag.sql"


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
    """Applique 002_rag.sql (idempotente), puis vérifie la dimension attendue."""
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        conn.execute(MIGRATION.read_text(encoding="utf-8"))  # plusieurs commandes, sans paramètre
    actual = column_dimension(admin_conninfo)
    if actual != dimension:
        raise RagStoreError(
            f"rag_chunks.embedding est en dimension {actual}, la configuration attend "
            f"{dimension} : modèle d'embedding et migration à mettre en cohérence"
        )


class ChunkRow(BaseModel):
    """Extrait à ingérer (administrateur)."""

    domain: Domain
    source_id: str
    reference: str
    text: str
    embedding_model: str
    embedding: list[float]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class Chunk(BaseModel):
    """Extrait trouvé : référence citable, distance cosinus à la requête."""

    id: int
    domain: Domain
    source_id: str
    reference: str
    text: str
    distance: float


def insert(admin_conninfo: str, rows: list[ChunkRow]) -> int:
    """Insère les extraits ; un extrait déjà présent (même hash, même modèle) est ignoré."""
    inserted = 0
    with psycopg.connect(admin_conninfo) as conn:
        register_vector(conn)
        for row in rows:
            cursor = conn.execute(
                "INSERT INTO rag_chunks (domain, source_id, reference, text, content_hash,"
                " embedding_model, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (domain, content_hash, embedding_model) DO NOTHING",
                (
                    row.domain,
                    row.source_id,
                    row.reference,
                    row.text,
                    row.content_hash,
                    row.embedding_model,
                    Vector(row.embedding),
                ),
            )
            inserted += cursor.rowcount
    return inserted


def search(conninfo: str, domain: Domain, query: list[float], *, k: int, model: str) -> list[Chunk]:
    """Recherche exacte : filtre (domaine, modèle) AVANT l'ordre par distance cosinus."""
    with psycopg.connect(conninfo) as conn:
        register_vector(conn)
        rows = conn.execute(
            "SELECT id, domain, source_id, reference, text, embedding <=> %s AS distance"
            " FROM rag_chunks WHERE domain = %s AND embedding_model = %s"
            " ORDER BY distance, id LIMIT %s",
            (Vector(query), domain, model, k),
        ).fetchall()
    return [
        Chunk(id=r[0], domain=r[1], source_id=r[2], reference=r[3], text=r[4], distance=float(r[5]))
        for r in rows
    ]

"""Corpus RAG dans PostgreSQL (table rag_chunks) : migration 002 et contrôle de dimension."""

from pathlib import Path

import psycopg

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

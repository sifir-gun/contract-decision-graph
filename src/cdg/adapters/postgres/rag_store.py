"""Corpus RAG dans PostgreSQL (table rag_chunks) : contrôle de dimension, insertion,
synchronisation et recherche exacte filtrée. Les migrations sont dans `migrations.py`."""

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import tuple_row

from cdg.adapters.postgres.connexions import Source, connection
from cdg.domain.corpus import ChunkRow
from cdg.domain.models import Domain
from cdg.ports.embedder import Embedder
from cdg.ports.retriever import Passage


class RagStoreError(Exception):
    """Schéma du corpus incompatible avec la configuration."""


def column_dimension(conninfo: str) -> int:
    """Dimension déclarée de rag_chunks.embedding (atttypmod d'une colonne vector)."""
    with psycopg.connect(conninfo) as conn:
        row = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'rag_chunks'::regclass AND attname = 'embedding'"
        ).fetchone()
    if row is None:  # créée par la migration 002 : son absence est une erreur explicite
        raise RagStoreError(
            "colonne rag_chunks.embedding introuvable : lancer setup-db"
        )
    return row[0]


def check_dimension(admin_conninfo: str, dimension: int) -> None:
    """Vérifie que rag_chunks.embedding a la dimension de la configuration."""
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
                " amendment, note, retrieved_at, kinds)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
                    row.kinds,
                ),
            )
            inserted += cursor.rowcount
    return inserted


def search(
    source: Source, domain: Domain, query: list[float], *, kind: str, k: int, model: str
) -> list[Passage]:
    """Recherche exacte : filtre (domaine, type de clause, modèle) AVANT l'ordre par
    distance cosinus. Des extraits du modèle sans rattachement (indexés avant la migration
    005) sont une erreur explicite : ils ne sont jamais ignorés en silence."""
    with connection(source) as conn:
        register_vector(conn)
        cur = conn.cursor(row_factory=tuple_row)
        unattached = cur.execute(
            "SELECT count(*) FROM rag_chunks WHERE embedding_model = %s AND kinds IS NULL",
            (model,),
        ).fetchone()
        if unattached is not None and unattached[0]:
            raise RagStoreError(
                f"{unattached[0]} extrait(s) du modèle {model} sans rattachement aux types "
                "de clause (indexés avant la migration 005) : relancer ingest"
            )
        rows = cur.execute(
            "SELECT id, domain, source_id, reference, text, embedding <=> %s AS distance,"
            " valid_until, note, kinds"
            " FROM rag_chunks WHERE domain = %s AND %s = ANY(kinds)"
            " AND embedding_model = %s"
            " ORDER BY distance, id LIMIT %s",
            (Vector(query), domain, kind, model, k),
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
            kinds=r[8],
        )
        for r in rows
    ]


class PgvectorRetriever:
    """Adaptateur du port Retriever : vecteur de la requête par l'Embedder, puis recherche
    exacte filtrée sur le domaine, le type de clause et le modèle de cet Embedder."""

    def __init__(self, source: Source, embedder: Embedder):
        self._source = source
        self._embedder = embedder

    def search(self, domain: Domain, query: str, *, kind: str, k: int) -> list[Passage]:
        vector = self._embedder.embed_query(query)
        return search(
            self._source, domain, vector, kind=kind, k=k, model=self._embedder.model
        )


# métadonnées stockées avec le texte : si l'une change (fin de validité, note,
# rattachement…), l'extrait est remplacé, même quand le texte, donc son empreinte, est
# identique
_METADATA = (
    "kinds",
    "reference",
    "article",
    "chunk_index",
    "valid_from",
    "valid_until",
    "amendment",
    "note",
    "retrieved_at",
)


def _hashable(values: tuple) -> tuple:
    """Clé de comparaison : une liste (types de clause) devient un tuple ; NULL reste None,
    et diffère donc de tout rattachement déclaré."""
    return tuple(tuple(v) if isinstance(v, list) else v for v in values)


def sync(admin_conninfo: str, rows: list[ChunkRow], model: str) -> dict:
    """Aligne rag_chunks sur le corpus, source par source, pour un modèle d'embedding :
    supprime les extraits disparus (texte nettoyé autrement, article retiré) ou dont une
    métadonnée a changé (fin de validité…), insère les nouveaux. Rejouable : un second
    passage ne change rien."""
    wanted: dict[str, set[tuple]] = {}
    for row in rows:
        values = (row.domain, row.content_hash, *(getattr(row, f) for f in _METADATA))
        wanted.setdefault(row.source_id, set()).add(_hashable(values))
    deleted = 0
    with psycopg.connect(admin_conninfo) as conn:
        existing = conn.execute(
            "SELECT id, source_id, domain, content_hash, "
            + ", ".join(_METADATA)
            + " FROM"
            " rag_chunks WHERE embedding_model = %s",
            (model,),
        ).fetchall()
        stale = [
            r[0] for r in existing if _hashable(r[2:]) not in wanted.get(r[1], set())
        ]
        if stale:
            deleted = conn.execute(
                "DELETE FROM rag_chunks WHERE id = ANY(%s)", (stale,)
            ).rowcount
    inserted = insert(admin_conninfo, rows)
    return {
        "inserted": inserted,
        "deleted": deleted,
        "unchanged": len(rows) - inserted,
        "chunks": len(rows),
    }

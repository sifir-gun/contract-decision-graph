"""Corpus RAG dans PostgreSQL (table rag_chunks) : contrôle de dimension, insertion,
synchronisation, recherche exacte filtrée (CRAG) et sans filtre (mesure de la recherche).
Les migrations sont dans `migrations.py`."""

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import tuple_row

from cdg.adapters.postgres.connexions import Conninfo, Source, connection, resolve
from cdg.domain.config import SearchConfig
from cdg.domain.corpus import ChunkRow
from cdg.domain.models import Domain
from cdg.ports.embedder import Embedder
from cdg.ports.retriever import Passage


class RagStoreError(Exception):
    """Schéma du corpus incompatible avec la configuration."""


def column_dimension(conninfo: Conninfo) -> int:
    """Dimension déclarée de rag_chunks.embedding (atttypmod d'une colonne vector)."""
    with psycopg.connect(resolve(conninfo)) as conn:
        row = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'rag_chunks'::regclass AND attname = 'embedding'"
        ).fetchone()
    if row is None:  # créée par la migration 002 : son absence est une erreur explicite
        raise RagStoreError(
            "colonne rag_chunks.embedding introuvable : lancer setup-db"
        )
    return row[0]


def check_dimension(admin_conninfo: Conninfo, dimension: int) -> None:
    """Vérifie que rag_chunks.embedding a la dimension de la configuration."""
    actual = column_dimension(admin_conninfo)
    if actual != dimension:
        raise RagStoreError(
            f"rag_chunks.embedding est en dimension {actual}, la configuration attend "
            f"{dimension} : modèle d'embedding et migration à mettre en cohérence"
        )


def insert(admin_conninfo: Conninfo, rows: list[ChunkRow]) -> int:
    """Insère les extraits ; un extrait déjà présent (même hash, même modèle) est ignoré."""
    inserted = 0
    with psycopg.connect(resolve(admin_conninfo)) as conn:
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


# extrait gardé par groupe, avant l'ordre par distance (texte SQL fixe, jamais une entrée) :
# chaque extrait ; chaque extrait une fois (un extrait indexé dans plusieurs domaines a une
# ligne par domaine) ; le plus proche de chaque référence (ADR 006)
_EACH, _ONCE, _PER_REFERENCE = "id", "reference, content_hash", "reference"


def _ranked(
    cur: psycopg.Cursor,
    query: list[float],
    where: str,
    params: tuple,
    *,
    unique: str,
    k: int,
) -> list[Passage]:
    """Recherche exacte : filtre `where`, un extrait par groupe `unique` (le plus proche),
    puis les k plus proches, à distance égale par identifiant."""
    rows = cur.execute(
        "SELECT id, domain, source_id, reference, text, distance, valid_until, note,"
        " kinds FROM ("
        f" SELECT DISTINCT ON ({unique}) id, domain, source_id, reference, text,"
        " embedding <=> %s AS distance, valid_until, note, kinds"
        f" FROM rag_chunks WHERE {where}"
        f" ORDER BY {unique}, distance, id) AS candidates"
        " ORDER BY distance, id LIMIT %s",
        (Vector(query), *params, k),
    ).fetchall()
    return _passages(rows)


def search(
    source: Source,
    domain: Domain,
    query: list[float],
    *,
    kind: str,
    k: int,
    model: str,
    settings: SearchConfig,
) -> list[Passage]:
    """Recherche exacte : filtre (domaine, type de clause, modèle) AVANT l'ordre par
    distance cosinus ; un extrait par référence si la configuration le demande. Des
    extraits du modèle sans rattachement (indexés avant la migration 005) sont une erreur
    explicite : ils ne sont jamais ignorés en silence."""
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
        return _ranked(
            cur,
            query,
            "domain = %s AND %s = ANY(kinds) AND embedding_model = %s",
            (domain, kind, model),
            unique=_PER_REFERENCE if settings.distinct_references else _EACH,
            k=k,
        )


def search_unfiltered(
    source: Source,
    query: list[float],
    *,
    k: int,
    model: str,
    settings: SearchConfig,
) -> list[Passage]:
    """Recherche exacte sans filtre de domaine ni de clause, sur le seul modèle : réservée
    à la mesure de la recherche (ADR 006). Chaque extrait une fois (un extrait indexé dans
    plusieurs domaines a une ligne par domaine), ou un par référence, comme `search`."""
    with connection(source) as conn:
        register_vector(conn)
        cur = conn.cursor(row_factory=tuple_row)
        return _ranked(
            cur,
            query,
            "embedding_model = %s",
            (model,),
            unique=_PER_REFERENCE if settings.distinct_references else _ONCE,
            k=k,
        )


def indexed(source: Source, model: str) -> list[tuple[str, str]]:
    """Extraits indexés du modèle (référence, texte), chacun une fois."""
    with connection(source) as conn:
        rows = conn.cursor(row_factory=tuple_row).execute(
            "SELECT DISTINCT ON (reference, content_hash) reference, text"
            " FROM rag_chunks WHERE embedding_model = %s"
            " ORDER BY reference, content_hash",
            (model,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _passages(rows: list[tuple]) -> list[Passage]:
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
    """Adaptateur des ports Retriever et CorpusSearch : vecteur de la requête par
    l'Embedder, puis recherche exacte filtrée sur le domaine, le type de clause et le
    modèle de cet Embedder (CRAG), ou sur le seul modèle (mesure de la recherche), selon
    les réglages `crag.search` de la configuration."""

    def __init__(self, source: Source, embedder: Embedder, settings: SearchConfig):
        self._source = source
        self._embedder = embedder
        self._settings = settings  # crag.search de la configuration

    def search(self, domain: Domain, query: str, *, kind: str, k: int) -> list[Passage]:
        vector = self._embedder.embed_query(query)
        return search(
            self._source,
            domain,
            vector,
            kind=kind,
            k=k,
            model=self._embedder.model,
            settings=self._settings,
        )

    def search_unfiltered(self, query: str, *, k: int) -> list[Passage]:
        """Port CorpusSearch : mesure de la recherche seule, jamais le CRAG."""
        vector = self._embedder.embed_query(query)
        return search_unfiltered(
            self._source,
            vector,
            k=k,
            model=self._embedder.model,
            settings=self._settings,
        )

    def indexed(self) -> list[tuple[str, str]]:
        return indexed(self._source, self._embedder.model)


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


def sync(admin_conninfo: Conninfo, rows: list[ChunkRow], model: str) -> dict:
    """Aligne rag_chunks sur le corpus, source par source, pour un modèle d'embedding :
    supprime les extraits disparus (texte nettoyé autrement, article retiré) ou dont une
    métadonnée a changé (fin de validité…), insère les nouveaux. Rejouable : un second
    passage ne change rien."""
    wanted: dict[str, set[tuple]] = {}
    for row in rows:
        values = (row.domain, row.content_hash, *(getattr(row, f) for f in _METADATA))
        wanted.setdefault(row.source_id, set()).add(_hashable(values))
    deleted = 0
    with psycopg.connect(resolve(admin_conninfo)) as conn:
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

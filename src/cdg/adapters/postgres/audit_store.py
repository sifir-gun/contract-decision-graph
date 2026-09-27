"""Journal d'audit dans PostgreSQL (table audit_decisions), en ajout seul.

`append` lit la tête de chaîne, fait sceller par le domaine et insère, dans une même
transaction, sous un verrou consultatif de transaction : app_role n'a que SELECT et INSERT,
qui ne permettent ni verrou de table exclusif ni SELECT ... FOR UPDATE (PostgreSQL 16,
LOCK). Les index uniques de la migration 004 (thread_id, prev_hash) garantissent en base un
enregistrement par thread et une chaîne sans fourche.
"""

import hashlib
from collections.abc import Callable

from psycopg import sql
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from cdg.adapters.postgres.connexions import Source, connection
from cdg.domain.audit import AuditEntry, StoredAuditEntry
from cdg.ports.audit_store import AuditStoreError

TABLE = "audit_decisions"
_COLUMNS = (
    "id",
    "contract_id",
    "thread_id",
    "record",
    "config_hash",
    "decision_hash",
    "prev_hash",
    "chain_hash",
    "created_at",
)


def lock_key(table: str) -> int:
    """Clé du verrou consultatif d'une table de journal : entier signé de 64 bits, stable
    d'une version de PostgreSQL à l'autre (calculé ici, pas par `hashtext`)."""
    digest = hashlib.sha256(f"cdg.audit:{table}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PostgresAuditStore:
    """`table` n'est changé que par les tests (journal jetable de même structure)."""

    """`source` : le pool du processus, ou une chaîne de connexion (tests)."""

    def __init__(self, source: Source, *, table: str = TABLE) -> None:
        self._source, self._table = source, table
        self._ident = sql.Identifier(table)
        self._select = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(map(sql.Identifier, _COLUMNS)), self._ident
        )

    def append(self, seal: Callable[[str | None], AuditEntry]) -> StoredAuditEntry:
        # une transaction, validée à la fin ; lignes en tuples
        with connection(self._source) as conn, conn.transaction():
            cur = conn.cursor(row_factory=tuple_row)
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key(self._table),))
            head = cur.execute(
                sql.SQL("SELECT chain_hash FROM {} ORDER BY id DESC LIMIT 1").format(
                    self._ident
                )
            ).fetchone()
            entry = seal(None if head is None else head[0])
            existing = cur.execute(
                self._select + sql.SQL(" WHERE thread_id = %s"), (entry.thread_id,)
            ).fetchone()
            if existing is not None:  # audit_seal rejoué : même thread
                stored = _stored(existing)
                if stored.decision_hash != entry.decision_hash:
                    raise AuditStoreError(
                        f"thread {entry.thread_id} déjà scellé avec une autre décision "
                        f"({stored.decision_hash}, reçu {entry.decision_hash})"
                    )
                return stored
            row = cur.execute(
                sql.SQL(
                    "INSERT INTO {} (contract_id, thread_id, record, config_hash, "
                    "decision_hash, prev_hash, chain_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at"
                ).format(self._ident),
                (
                    entry.contract_id,
                    entry.thread_id,
                    Jsonb(entry.record),
                    entry.config_hash,
                    entry.decision_hash,
                    entry.prev_hash,
                    entry.chain_hash,
                ),
            ).fetchone()
        if row is None:  # RETURNING rend toujours la ligne insérée
            raise AuditStoreError(f"insertion sans retour dans {self._table}")
        return StoredAuditEntry(**entry.model_dump(), id=row[0], created_at=row[1])

    def entries(self) -> list[StoredAuditEntry]:
        with connection(self._source) as conn:
            cur = conn.cursor(row_factory=tuple_row)
            rows = cur.execute(self._select + sql.SQL(" ORDER BY id")).fetchall()
        return [_stored(r) for r in rows]


def _stored(row: tuple) -> StoredAuditEntry:
    return StoredAuditEntry(**dict(zip(_COLUMNS, row, strict=True)))

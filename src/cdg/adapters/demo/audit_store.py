"""Journal d'audit en mémoire (port `AuditStore`), le temps du processus : mode
démonstration de l'interface web, et doublure des tests. Mêmes règles que le journal
PostgreSQL : un enregistrement par thread, ajout rejoué idempotent à décision égale.

Comme le verrou consultatif du journal PostgreSQL, un verrou fait de la lecture de la
tête de chaîne et de l'ajout une seule opération : l'interface sert ses requêtes dans des
threads, et deux ajouts sur la même tête fourcheraient la chaîne."""

import threading
from collections.abc import Callable
from datetime import datetime

from cdg.domain.audit import AuditEntry, StoredAuditEntry
from cdg.ports.audit_store import AuditStoreError


class MemoryAuditStore:
    def __init__(self) -> None:
        self.stored: list[StoredAuditEntry] = []
        self._lock = threading.Lock()

    def append(self, seal: Callable[[str | None], AuditEntry]) -> StoredAuditEntry:
        with self._lock:
            entry = seal(self.stored[-1].chain_hash if self.stored else None)
            for stored in self.stored:
                if stored.thread_id == entry.thread_id:
                    if stored.decision_hash != entry.decision_hash:
                        raise AuditStoreError(
                            f"thread {entry.thread_id} déjà scellé avec une autre "
                            "décision"
                        )
                    return stored
            created_at = datetime.fromisoformat(entry.record["sealed_at"])
            stored = StoredAuditEntry(
                **entry.model_dump(), id=len(self.stored) + 1, created_at=created_at
            )
            self.stored.append(stored)
            return stored

    def entries(self) -> list[StoredAuditEntry]:
        with self._lock:
            return list(self.stored)

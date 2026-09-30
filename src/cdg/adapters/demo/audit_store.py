"""Journal d'audit en mémoire (port `AuditStore`), le temps du processus : mode
démonstration de l'interface web, et doublure des tests. Mêmes règles que le journal
PostgreSQL : un enregistrement par thread, ajout rejoué idempotent à décision égale.

Comme le verrou consultatif du journal PostgreSQL, un verrou fait de la lecture de la
tête de chaîne et de l'ajout une seule opération : l'interface sert ses requêtes dans des
threads, et deux ajouts sur la même tête fourcheraient la chaîne."""

import copy
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from cdg.domain.audit import AuditEntry, StoredAuditEntry
from cdg.ports.audit_store import AuditStoreError


class MemoryAuditStore:
    def __init__(self) -> None:
        self.stored: list[StoredAuditEntry] = []
        self.archive: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def append(
        self,
        seal: Callable[[str | None], AuditEntry],
        configurations: Mapping[str, Mapping[str, Any]],
    ) -> StoredAuditEntry:
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
            for key, config in configurations.items():  # ajout seul, sans doublon
                self.archive.setdefault(key, copy.deepcopy(dict(config)))
            return stored

    def entries(self) -> list[StoredAuditEntry]:
        with self._lock:
            return list(self.stored)

    def configurations(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.archive)

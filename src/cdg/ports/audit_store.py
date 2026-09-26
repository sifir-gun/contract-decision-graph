"""Port du journal d'audit, en ajout seul. Défini au J3, implémenté au J4.

Le calcul des empreintes reste dans le domaine : le magasin fournit la tête de chaîne
et insère, dans une même transaction sous verrou, ce que `seal` a scellé.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel


class AuditEntry(BaseModel):
    """Enregistrement scellé, prêt à l'ajout."""

    contract_id: str
    thread_id: str
    record: dict[str, Any]
    config_hash: str
    decision_hash: str
    prev_hash: str
    chain_hash: str


class StoredAuditEntry(AuditEntry):
    id: int
    created_at: datetime


class AuditStore(Protocol):
    def append(self, seal: Callable[[str | None], AuditEntry]) -> StoredAuditEntry:
        """Lit la tête de chaîne (None si le journal est vide), appelle `seal`, insère."""
        ...

    def entries(self) -> list[StoredAuditEntry]:
        """Tous les enregistrements, du plus ancien au plus récent, pour `verify`."""
        ...

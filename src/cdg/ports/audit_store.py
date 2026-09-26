"""Port du journal d'audit, en ajout seul. Défini au J3, implémenté au J4.

Le calcul des empreintes reste dans le domaine (`domain/audit.py`, qui définit aussi
`AuditEntry`) : le magasin fournit la tête de chaîne et insère, dans une même
transaction sous verrou, ce que `seal` a scellé.
"""

from collections.abc import Callable
from typing import Protocol

from cdg.domain.audit import AuditEntry, StoredAuditEntry


class AuditStore(Protocol):
    def append(self, seal: Callable[[str | None], AuditEntry]) -> StoredAuditEntry:
        """Lit la tête de chaîne (None si le journal est vide), appelle `seal`, insère."""
        ...

    def entries(self) -> list[StoredAuditEntry]:
        """Tous les enregistrements, du plus ancien au plus récent, pour `verify`."""
        ...

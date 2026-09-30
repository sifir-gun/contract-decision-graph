"""Port du journal d'audit, en ajout seul. Défini au J3, implémenté au J4.

Le calcul des empreintes reste dans le domaine (`domain/audit.py`, qui définit aussi
`AuditEntry`) : le magasin fournit la tête de chaîne et insère, dans une même
transaction sous verrou, ce que `seal` a scellé, avec les configurations de la décision
(archive en ajout seul, par empreinte).
"""

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from cdg.domain.audit import AuditEntry, StoredAuditEntry


class AuditStoreError(Exception):
    """Ajout refusé : thread déjà scellé avec une autre décision."""


class AuditStore(Protocol):
    def append(
        self,
        seal: Callable[[str | None], AuditEntry],
        configurations: Mapping[str, Mapping[str, Any]],
    ) -> StoredAuditEntry:
        """Lit la tête de chaîne (None si le journal est vide), appelle `seal`, insère
        l'enregistrement et archive `configurations` (empreinte → forme validée), dans
        la même transaction ; une configuration déjà archivée ne l'est pas deux fois.

        Un seul enregistrement par thread : un ajout rejoué (même thread, même
        `decision_hash`) rend l'enregistrement existant sans rien insérer ; une autre
        décision pour le même thread lève `AuditStoreError`, et rien n'est archivé."""
        ...

    def entries(self) -> list[StoredAuditEntry]:
        """Tous les enregistrements, du plus ancien au plus récent, pour `verify`."""
        ...

    def configurations(self) -> dict[str, dict[str, Any]]:
        """Archive des configurations, par empreinte, telles que relues : l'empreinte se
        recalcule par la forme canonique du domaine, jamais sur le texte relu."""
        ...

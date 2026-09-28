"""Port des verrous de contrat : une seule opération qui modifie un contrat à la fois,
quel que soit le processus ou le réplica (phase Kubernetes, ADR 005).

Adaptateurs : `adapters/postgres/locks.py` (verrou consultatif de session, relâché aussi
si le processus meurt), `adapters/demo/locks.py` (un processus : démonstration, tests).
Le moteur le prend pour créer un contrat, trancher une revue, expirer un contrat en
attente ou reprendre une analyse interrompue.
"""

from contextlib import AbstractContextManager
from typing import Protocol


class ContractBusy(Exception):
    """Contrat déjà en cours de traitement, ici ou dans un autre processus."""

    def __init__(self, thread_id: str):
        super().__init__(
            f"le contrat {thread_id} est en cours de traitement (analyse ou revue) : "
            "réessayer quand il sera terminé"
        )
        self.thread_id = thread_id


class ContractLocks(Protocol):
    def hold(self, thread_id: str) -> AbstractContextManager[None]:
        """Verrou du contrat, pris sans attendre : `ContractBusy` s'il est déjà tenu.
        Tenu jusqu'à la sortie du bloc, exception comprise."""
        ...

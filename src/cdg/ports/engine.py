"""Port d'exécution des contrats : un contrat = un thread du graphe.

Adaptateur : `adapters/langgraph/engine.py`, avec le checkpointer PostgreSQL, ou en
mémoire pour la démonstration. Le service des contrats (`application/service.py`) passe
par lui, pour la CLI comme pour l'interface web.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from cdg.domain.authorization import Actor


class ThreadError(Exception):
    """Thread inconnu, déjà existant, ou pas en attente d'une décision humaine."""


class ContractEngine(Protocol):
    def run(
        self,
        contract_id: str,
        raw_text: str,
        parties: Sequence[str],
        analysis_date: date,
        actor: Actor,
    ) -> dict[str, Any]:
        """Analyse un contrat, masqué avant le graphe ; statut du thread et masquage.
        `actor` : qui lance l'analyse, posé dans l'état et scellé (quatre yeux)."""
        ...

    def resume(self, thread_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        """Reprise humaine d'un thread en attente ; statut du thread."""
        ...

    def status(self, thread_id: str) -> dict[str, Any]: ...

    def values(self, thread_id: str) -> dict[str, Any]:
        """État courant du thread (texte masqué, clauses, consommation…)."""
        ...

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        """Checkpoints, du plus ancien au plus récent."""
        ...

    def overview(self) -> list[dict[str, Any]]:
        """Statut de chaque contrat, avec la date de son premier et de son dernier
        checkpoint (`started_at`, `updated_at`), en ouvrant le graphe une seule fois,
        quel que soit leur nombre."""
        ...

    def expire(
        self, older_than: timedelta, now: datetime, actor: Actor
    ) -> list[dict[str, Any]]:
        """NO_GO système pour les threads en attente depuis plus de `older_than` ;
        `actor` : qui lance l'expiration, scellé avec la décision."""
        ...

    def resume_interrupted(self) -> list[dict[str, Any]]:
        """Reprend, depuis leur dernier checkpoint, les analyses interrompues (processus
        arrêté ou mort en cours d'analyse) ; laisse celles qu'un autre processus tient.
        Statut de chaque analyse reprise."""
        ...

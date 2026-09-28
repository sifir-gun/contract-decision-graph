"""Port du compteur de reprises : combien de fois l'analyse d'un contrat a été reprise
après une interruption (processus arrêté ou mort), durablement, pour survivre aux
redémarrages (phase Kubernetes, ADR 005).

Adaptateurs : `adapters/postgres/resumes.py` (table `contract_resumes`, migration 006),
`adapters/demo/resumes.py` (un processus : démonstration, tests). Le moteur compte une
reprise sous le verrou du contrat, avant de la faire : une reprise qui tue le processus
est comptée quand même.
"""

from typing import Protocol


class ResumeCounter(Protocol):
    def record(self, thread_id: str) -> int:
        """Compte une reprise de plus pour ce contrat ; rend le total, celle-ci comprise."""
        ...

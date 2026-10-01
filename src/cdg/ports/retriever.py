"""Port de recherche dans le corpus : une requête en texte, les extraits les plus proches.

La requête est un texte, pas un vecteur : l'adaptateur calcule le vecteur et filtre sur
son propre modèle d'embedding, le CRAG ne peut donc pas mélanger deux modèles. Il filtre
aussi sur le type de clause (J4) : seuls les extraits dont la source est déclarée pour
cette clause sont rendus.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.models import Domain


class Passage(BaseModel):
    """Extrait trouvé : référence citable, distance cosinus à la requête."""

    id: int
    domain: Domain
    source_id: str
    reference: str
    text: str
    distance: float
    valid_until: date | None = None
    note: str | None = None
    kinds: list[str]  # types de clause que la source de l'extrait peut justifier


class Retriever(Protocol):
    def search(
        self, domain: Domain, query: str, *, kind: str, k: int
    ) -> list[Passage]: ...


class CorpusSearch(Protocol):
    """Recherche sans filtre de domaine ni de clause, dans tout le corpus du modèle
    d'embedding, chaque extrait une fois : réservée à la mesure de la recherche seule
    (`application/evaluation.py`, ADR 006). Le CRAG passe toujours par `Retriever`."""

    def search_unfiltered(self, query: str, *, k: int) -> list[Passage]: ...

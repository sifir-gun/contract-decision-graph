"""Port de recherche dans le corpus : une requête en texte, les extraits les plus proches.

La requête est un texte, pas un vecteur : l'adaptateur calcule le vecteur et filtre sur
son propre modèle d'embedding, le CRAG ne peut donc pas mélanger deux modèles.
"""

from datetime import date
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.state import Domain


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


class Retriever(Protocol):
    def search(self, domain: Domain, query: str, *, k: int) -> list[Passage]: ...

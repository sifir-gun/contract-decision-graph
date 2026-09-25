"""Port d'embedding : vecteurs des extraits (ingestion) et des requêtes (recherche).

Adaptateur : `adapters/fastembed.py`, qui ajoute lui-même les préfixes propres au modèle.
"""

from typing import Protocol


class Embedder(Protocol):
    model: str  # stocké avec chaque extrait, filtre de la recherche
    dimension: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

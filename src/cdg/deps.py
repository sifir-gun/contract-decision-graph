"""Dépendances injectées dans les nœuds : extracteur (LLM) et CRAG. Doublures dans les tests."""

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from cdg.state import Clause, Domain, RetrievalStatus, Usage


class ExtractionResult(BaseModel):
    clauses: list[Clause]
    usage: list[Usage]


class RetrievalResult(BaseModel):
    status: RetrievalStatus
    evidence_ids: list[str]
    usage: list[Usage]


Tier = Literal["main", "light"]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProvider(Protocol):
    """Fournisseur LLM : sortie structurée validée par un modèle Pydantic, consommation mesurée."""

    name: str

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]: ...


class Embedder(Protocol):
    model: str
    dimension: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Extractor(Protocol):
    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult: ...


class Retriever(Protocol):
    def __call__(self, domain: Domain, clauses: list[Clause]) -> RetrievalResult: ...


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Retriever

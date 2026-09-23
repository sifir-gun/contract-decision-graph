"""Dépendances injectées dans les nœuds : extracteur (LLM) et CRAG. Doublures dans les tests."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from cdg.state import Clause, Domain, RetrievalStatus, Usage


class ExtractionResult(BaseModel):
    clauses: list[Clause]
    usage: list[Usage]


class RetrievalResult(BaseModel):
    status: RetrievalStatus
    evidence_ids: list[str]
    usage: list[Usage]


class Extractor(Protocol):
    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult: ...


class Retriever(Protocol):
    def __call__(self, domain: Domain, clauses: list[Clause]) -> RetrievalResult: ...


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Retriever

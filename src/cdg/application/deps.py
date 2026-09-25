"""Dépendances injectées dans les nœuds : extracteur et CRAG. Doublures dans les tests.

Ce ne sont pas des dépendances externes : elles passent elles-mêmes par les ports
(`ports.llm`, `ports.retriever`).
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.state import Clause, Domain, RetrievalStatus, Usage


class ExtractionResult(BaseModel):
    clauses: list[Clause]
    usage: list[Usage]


class RetrievalResult(BaseModel):
    status: RetrievalStatus
    evidence_ids: list[str]
    usage: list[Usage]


class Extractor(Protocol):
    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult: ...


class Crag(Protocol):
    def __call__(self, domain: Domain, clauses: list[Clause]) -> RetrievalResult: ...


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Crag

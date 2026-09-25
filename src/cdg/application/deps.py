"""Dépendances injectées dans les nœuds : extracteur et CRAG. Doublures dans les tests.

Ce ne sont pas des dépendances externes : elles passent elles-mêmes par les ports
(`ports.llm`, `ports.retriever`).
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.state import Clause, Domain, RetrievalStatus, RetrievalTrace, Usage


class ExtractionResult(BaseModel):
    clauses: list[Clause]
    usage: list[Usage]


class RetrievalResult(BaseModel):
    status: RetrievalStatus
    evidence_ids: list[str]  # références retenues, en vigueur à la date d'analyse
    usage: list[Usage]
    findings: list[str] = []  # constats du CRAG (références expirées), ajoutés au verdict
    trace: RetrievalTrace | None = None  # résumé du CRAG ; None pour une doublure


class Extractor(Protocol):
    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult: ...


class Crag(Protocol):
    def __call__(
        self, domain: Domain, clauses: list[Clause], analysis_date: date
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Crag

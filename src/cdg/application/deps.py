"""Dépendances injectées dans les nœuds : extracteur et CRAG. Doublures dans les tests.

Ce ne sont pas des dépendances externes : elles passent elles-mêmes par les ports
(`ports.llm`, `ports.retriever`).
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.models import Clause, Domain, RetrievalTrace, Usage


class ExtractionResult(BaseModel):
    clauses: list[Clause]
    usage: list[Usage]


class RetrievalResult(BaseModel):
    """Recherche du CRAG pour les clauses reçues. Le statut du domaine n'en fait pas partie :
    il se déduit des constats des règles (`domain/justification.py`)."""

    trace: RetrievalTrace  # une entrée par clause recherchée, références rattachées
    usage: list[Usage]


class Extractor(Protocol):
    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult: ...


class Crag(Protocol):
    """Une recherche par clause reçue : celles du domaine qui portent un constat."""

    def __call__(
        self, domain: Domain, clauses: list[Clause], analysis_date: date
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Crag

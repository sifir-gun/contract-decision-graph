"""Dépendances injectées dans les nœuds : extracteur, CRAG, journal d'audit, horloge.
Doublures dans les tests.

L'extracteur et le CRAG ne sont pas des dépendances externes : ils passent eux-mêmes par
les ports (`ports.llm`, `ports.retriever`). Le journal est le port `AuditStore` ; l'horloge
est injectée pour que l'horodatage scellé soit déterministe en test.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.models import Clause, Domain, RetrievalTrace, Usage
from cdg.ports.audit_store import AuditStore


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


Clock = Callable[[], datetime]  # heure avec fuseau, pour l'horodatage scellé


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Crag
    audit_store: AuditStore
    clock: Clock

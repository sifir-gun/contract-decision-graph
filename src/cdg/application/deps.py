"""Dépendances injectées dans les nœuds : extracteur, CRAG, explicateur, journal d'audit,
horloge, version du code. Doublures dans les tests.

L'extracteur, le CRAG et l'explicateur ne sont pas des dépendances externes : ils passent
eux-mêmes par les ports (`ports.llm`, `ports.retriever`). Le journal est le port
`AuditStore` ; l'horloge est injectée pour que l'horodatage scellé soit déterministe en test.
La version du code est lue au lancement par la racine de composition, jamais devinée.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel

from cdg.domain.explanation import Draft, ExplanationRequest
from cdg.domain.models import Clause, Domain, RetrievalTrace, Usage
from cdg.domain.version import CodeVersion
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


class Explainer(Protocol):
    """Rédige l'explication du verdict figé ; `feedback` : motifs du refus précédent."""

    def __call__(
        self, request: ExplanationRequest, feedback: list[str]
    ) -> tuple[Draft, Usage]: ...


@dataclass(frozen=True)
class TemplateOnly:
    """Explication par le gabarit, sans LLM (expire, resume sans clé d'API) ; le motif est
    scellé avec l'explication."""

    reason: str


Clock = Callable[[], datetime]  # heure avec fuseau, pour l'horodatage scellé


@dataclass(frozen=True)
class Deps:
    extractor: Extractor
    crag: Crag
    audit_store: AuditStore
    clock: Clock
    explainer: Explainer | TemplateOnly
    code_version: CodeVersion  # scellée avec l'analyse et avec le scellement

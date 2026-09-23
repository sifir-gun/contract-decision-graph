"""Schémas d'état du graphe et modèles métier, validés à chaque frontière de nœud."""

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

Domain = Literal["juridique", "financier", "conformite", "operationnel"]
Decision = Literal["GO", "GO_RESERVES", "NO_GO", "ESCALADE"]
Route = Literal["extract_clauses", "reject", "analysts", "human_review", "explain"]
RetrievalStatus = Literal["OK", "INSUFFISANT"]

DOMAINS: tuple[Domain, ...] = ("juridique", "financier", "conformite", "operationnel")

REQUIRED_KINDS = ("responsabilite_acheteur", "responsabilite_fournisseur", "revision_prix",
                  "penalites_retard", "duree_engagement", "preavis_resiliation",
                  "donnees_personnelles", "accord_traitement_donnees")


class Clause(BaseModel):
    kind: str            # un des REQUIRED_KINDS
    present: bool        # la clause figure-t-elle dans le contrat ?
    quote: str           # citation exacte si present, "" sinon (alors non vérifiée)
    value: float | None  # quantité utile à la règle, voir « Règles par domaine »

    @model_validator(mode="after")
    def _citation_selon_presence(self) -> "Clause":
        if not self.present and self.quote != "":
            raise ValueError(f"clause absente avec une citation : {self.kind}")
        if self.present and not self.quote.strip():
            raise ValueError(f"clause présente sans citation : {self.kind}")
        return self


class AgentVerdict(BaseModel):
    domain: Domain
    score: float = Field(ge=0.0, le=1.0)   # 1 = favorable
    hard_block: bool
    findings: list[str]
    evidence_ids: list[str]
    retrieval_status: RetrievalStatus


class HumanDecision(BaseModel):
    decision: Decision
    reviewer: str
    reason: str
    overrides_block: bool = False   # vrai si l'humain lève un blocage dur


class Usage(BaseModel):
    node: str
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)


class ContractState(TypedDict, total=False):
    contract_id: str
    raw_text: str
    reject_reason: str | None
    clauses: list[Clause]
    extraction_attempts: int
    extraction_feedback: list[str]
    verdicts: Annotated[list[AgentVerdict], operator.add]
    usage: Annotated[list[Usage], operator.add]
    proposed_decision: Decision
    margin: float
    route: Route                      # écrite par un nœud, lue par l'arête
    failure_report: dict | None
    human: HumanDecision | None
    final_decision: Decision | None   # decision_gate (route explain) ou human_review ; None après reject
    explanation: str
    config_hash: str
    decision_hash: str
    chain_hash: str


class AnalystInput(TypedDict):   # état privé reçu via Send
    domain: Domain
    clauses: list[Clause]

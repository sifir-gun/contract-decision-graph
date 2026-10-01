"""État du graphe : forme de l'état d'un contrat, réducteurs, route, entrée des analystes.

Les objets qu'il transporte sont les modèles métier de `domain/models.py`.
"""

import operator
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from cdg.domain.authorization import Actor
from cdg.domain.explanation import Explanation
from cdg.domain.models import (
    AgentVerdict,
    Clause,
    Decision,
    Domain,
    HumanDecision,
    HumanReview,
    NodeFailure,
    Usage,
)

Route = Literal["extract_clauses", "reject", "analysts", "human_review", "explain"]


class ContractState(TypedDict, total=False):
    contract_id: str
    raw_text: str
    analysis_date: (
        date  # versions des textes jugées à cette date ; fixée par run_contract
    )
    reject_reason: str | None
    # constats du contrat lui-même (tentative d'instruction), écrits par validate_input
    input_findings: list[str]
    clauses: list[Clause]
    extraction_attempts: int
    extraction_feedback: list[str]
    verdicts: Annotated[list[AgentVerdict], operator.add]
    usage: Annotated[list[Usage], operator.add]
    failures: Annotated[list[NodeFailure], operator.add]  # gardes d'échec de nœud
    proposed_decision: Decision
    margin: float
    route: Route  # écrite par un nœud, lue par l'arête
    failure_report: dict[str, Any] | None
    # HumanReview (v2) ; HumanDecision (v1) dans les états d'avant la PR D2
    human: HumanReview | HumanDecision | None
    analyse_par: Actor | None  # qui a lancé l'analyse, posé par run_contract (PR D2)
    final_decision: (
        Decision | None
    )  # decision_gate (route explain) ou human_review ; None après reject
    explanation: Explanation  # écrite par explain ; absente après reject
    config_hash: str  # configuration de l'analyse, posée par run_contract (J4)
    # sa forme validée (JSON), archivée au scellement (PR d'archivage)
    analysis_config: dict[str, Any]
    models: dict[str, str]  # modèles de l'analyse, posés par run_contract (J4)
    # version du code de l'analyse (CodeVersion en JSON), posée par run_contract (PR D2)
    code_version: dict[str, str | None]
    decision_hash: str
    chain_hash: str
    sealing_findings: list[str]  # constats du scellement, écrits par audit_seal
    # analyse interrompue escaladée car sa configuration avait changé : empreintes de
    # l'analyse et de la reprise ; seul cas où resume accepte une autre configuration
    configuration_changee: dict[str, str | None]
    # cause d'une escalade écrite par la reprise (ResumeEscalation en JSON), scellée
    escalade_reprise: dict[str, Any]
    relance_de: str  # contrat escaladé dont cette analyse est la relance


class AnalystInput(TypedDict):  # état privé reçu via Send
    domain: Domain
    clauses: list[Clause]
    analysis_date: date

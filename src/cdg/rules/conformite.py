"""Règles du domaine conformité : traitement de données personnelles (RGPD)."""

from cdg.config import DecisionConfig
from cdg.rules._common import clause, verdict
from cdg.state import AgentVerdict, Clause, RetrievalStatus


def conformite(
    clauses: list[Clause], retrieval_status: RetrievalStatus, config: DecisionConfig
) -> AgentVerdict:
    hard_block, findings = False, []

    donnees = clause(clauses, "donnees_personnelles")
    accord = clause(clauses, "accord_traitement_donnees")
    if donnees.present and not accord.present:
        hard_block = True
        findings.append(
            "blocage : données personnelles traitées sans accord de traitement (art. 28 RGPD)"
        )

    return verdict(
        "conformite", retrieval_status, hard_block=hard_block, penalties=[], findings=findings
    )

"""verify_extraction : adapte l'état à la vérification du domaine (`domain/verification.py`).

Écrit `route` : analysts si tout est vérifié, extract_clauses pour un nouvel essai avec
retour ciblé, human_review (ESCALADE) après le dernier essai ou si l'extraction a échoué.
"""

from typing import Any

from cdg.application.failures import escalate
from cdg.application.state import ContractState
from cdg.domain.config import DecisionConfig
from cdg.domain.verification import check_extraction


def verify_extraction(state: ContractState, decision_config: DecisionConfig) -> dict[str, Any]:
    failures = state.get("failures", [])
    if failures:  # extraction en échec (garde de l'orchestrateur) : rien à vérifier
        return escalate(failures)
    check = check_extraction(
        state["raw_text"],
        state["clauses"],
        state["extraction_attempts"],  # incrémenté par extract_clauses
        decision_config.extraction.max_attempts,
    )
    if check.outcome == "verified":
        return {"route": "analysts"}
    if check.outcome == "retry":
        return {"route": "extract_clauses", "extraction_feedback": check.problems}
    return {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": check.failure_report,
    }

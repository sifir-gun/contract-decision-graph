"""validate_input : adapte l'état au contrôle d'entrée du domaine (`domain/input_checks.py`).

Écrit `route` : extract_clauses si l'entrée est recevable, sinon reject avec son motif.
"""

from cdg.application.state import ContractState
from cdg.domain.config import DecisionConfig
from cdg.domain.input_checks import rejection


def validate_input(state: ContractState, decision_config: DecisionConfig) -> dict:
    reason = rejection(state.get("raw_text", ""), state.get("analysis_date"), decision_config.input)
    if reason is not None:
        return {"route": "reject", "reject_reason": reason}
    return {"route": "extract_clauses", "extraction_attempts": 0}

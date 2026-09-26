"""validate_input : adapte l'état au contrôle d'entrée du domaine (`domain/input_checks.py`)
et à la détection d'instructions (`domain/instructions.py`).

Écrit `route` : extract_clauses si l'entrée est recevable, sinon reject avec son motif. Une
tentative d'instruction détectée n'arrête pas l'analyse : elle devient un constat du
contrat (`input_findings`), qui imposera la revue humaine au gate.
"""

from typing import Any

from cdg.application.state import ContractState
from cdg.domain import instructions
from cdg.domain.config import DecisionConfig
from cdg.domain.input_checks import rejection


def validate_input(
    state: ContractState, decision_config: DecisionConfig
) -> dict[str, Any]:
    reason = rejection(
        state.get("raw_text", ""), state.get("analysis_date"), decision_config.input
    )
    if reason is not None:
        return {"route": "reject", "reject_reason": reason}
    update: dict[str, Any] = {"route": "extract_clauses", "extraction_attempts": 0}
    found = instructions.findings(
        state["raw_text"], decision_config.input.instruction_patterns
    )
    if found:
        update["input_findings"] = found
    return update

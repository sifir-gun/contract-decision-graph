"""decision_gate : adapte l'état au gate du domaine (`domain/decision.py`), puis la
proposition à l'état. Écrit `route` : explain, ou human_review pour une revue humaine."""

from typing import Any

from cdg.application.state import ContractState
from cdg.domain.config import DecisionConfig
from cdg.domain.decision import decide


def decision_gate(
    state: ContractState, decision_config: DecisionConfig
) -> dict[str, Any]:
    # pas « config » : LangGraph réserve ce nom de paramètre au RunnableConfig
    outcome = decide(
        state["verdicts"],
        state.get("failures", []),
        state.get("usage", []),
        decision_config,
    )
    update: dict[str, Any] = {
        "proposed_decision": outcome.proposed,
        "route": "human_review" if outcome.human_review else "explain",
    }
    if (
        outcome.final is not None
    ):  # decision_gate n'écrit la décision finale que vers explain
        update["final_decision"] = outcome.final
    if outcome.margin is not None:
        update["margin"] = outcome.margin
    if outcome.failure_report is not None:
        update["failure_report"] = outcome.failure_report
    return update

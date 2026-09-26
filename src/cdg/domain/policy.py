"""Politique d'arbitrage humain, lue depuis la configuration. Fonctions pures, sans LangGraph."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from cdg.domain.config import DecisionConfig
from cdg.domain.models import AgentVerdict, HumanDecision
from cdg.domain.numeric import rounded


def build_request(state: Mapping[str, Any], config: DecisionConfig) -> dict[str, Any]:
    """Charge utile exposée à l'humain ; le texte du contrat n'y figure pas.

    `state` : l'état du contrat, lu seulement ; le domaine n'en connaît pas la forme
    (`application/state.py`), seulement les clés lues ici.
    """
    margin = state.get("margin")
    return {
        "contract_id": state.get("contract_id"),
        "proposed_decision": state.get("proposed_decision"),
        "margin": None if margin is None else rounded(margin),
        "failure_report": state.get("failure_report"),
        "verdicts": [
            {
                "domain": v.domain,
                "score": rounded(v.score),
                "hard_block": v.hard_block,
                "findings": list(v.findings),
                "retrieval_status": v.retrieval_status,
            }
            for v in state.get("verdicts", [])
        ],
        "allowed_decisions": list(config.human_policy.allowed_decisions),
    }


def check(human: HumanDecision, verdicts: list[AgentVerdict], config: DecisionConfig) -> str | None:
    """None si la décision est recevable, sinon le motif du refus."""
    rules = config.human_policy
    if human.decision not in rules.allowed_decisions:
        return f"décision {human.decision} non autorisée : attendu {rules.allowed_decisions}"
    if not human.reviewer.strip():
        return "reviewer obligatoire"
    if not human.reason.strip():
        return "reason obligatoire"
    blocked = [v.domain for v in verdicts if v.hard_block]
    lifts_block = bool(blocked) and human.decision != "NO_GO"
    if lifts_block and not rules.allow_block_override:
        return f"levée de blocage dur interdite par la configuration ({', '.join(blocked)})"
    if lifts_block and not human.overrides_block:
        return f"blocage dur ({', '.join(blocked)}) : overrides_block requis pour {human.decision}"
    if human.overrides_block and not lifts_block:
        return "overrides_block sans blocage dur levé"
    return None


def review(
    payload: Any, verdicts: list[AgentVerdict], config: DecisionConfig
) -> tuple[HumanDecision | None, str | None]:
    """Valide la réponse brute reçue à la reprise, puis applique la politique."""
    try:
        human = HumanDecision.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'réponse'} : {e['msg']}" for e in exc.errors()
        )
        return None, f"réponse invalide : {details}"
    error = check(human, verdicts, config)
    return (None, error) if error else (human, None)

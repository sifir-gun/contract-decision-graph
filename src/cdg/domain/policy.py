"""Politique d'arbitrage humain, lue depuis la configuration. Fonctions pures, sans LangGraph."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from cdg.domain import authorization
from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig
from cdg.domain.models import AgentVerdict, HumanReview
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
        # tentative d'instruction détectée dans le contrat : visible par l'humain
        "input_findings": list(state.get("input_findings", [])),
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
        # configuration changée pendant l'analyse (reprise escaladée) : montré au
        # relecteur, qui peut trancher sous la configuration actuelle
        "configuration_changee": state.get("configuration_changee"),
    }


def analysis_allows_override(state: Mapping[str, Any], config: DecisionConfig) -> bool:
    """La configuration de l'analyse permet-elle la levée d'un blocage dur ? Sans
    changement de configuration pendant l'analyse, c'est la configuration courante (même
    empreinte : `resume` refuse sinon). Après un changement, celle de l'analyse doit
    l'autoriser aussi : un changement de configuration n'assouplit jamais rétroactivement
    un contrôle de sécurité ; inconnue, elle ne l'autorise pas."""
    if not state.get("configuration_changee"):
        return config.human_policy.allow_block_override
    analysed = state.get("analysis_config")
    if analysed is None:
        return False
    return bool(analysed["human_policy"]["allow_block_override"])


def check(
    human: HumanReview,
    verdicts: list[AgentVerdict],
    config: DecisionConfig,
    analyst: Actor | None,
    *,
    analysis_allows_override: bool,
) -> str | None:
    """None si la décision est recevable, sinon le motif du refus. Quatre yeux : second
    contrôle, après celui de l'interface, et seul contrôle pour la CLI ; une décision
    système (expiration, NO_GO) n'y est pas soumise."""
    rules = config.human_policy
    if human.decision not in rules.allowed_decisions:
        return f"décision {human.decision} non autorisée : attendu {rules.allowed_decisions}"
    if human.source == "humain":
        refused = authorization.four_eyes(analyst, human.acteur)
        if refused:
            return refused
    if not human.reason.strip():
        return "reason obligatoire"
    blocked = [v.domain for v in verdicts if v.hard_block]
    lifts_block = bool(blocked) and human.decision != "NO_GO"
    if lifts_block and not rules.allow_block_override:
        return f"levée de blocage dur interdite par la configuration ({', '.join(blocked)})"
    if lifts_block and not analysis_allows_override:
        return (
            f"levée de blocage dur ({', '.join(blocked)}) interdite par la configuration "
            "d'analyse : un changement de configuration n'assouplit jamais un contrôle "
            "de sécurité"
        )
    if lifts_block and not human.overrides_block:
        return f"blocage dur ({', '.join(blocked)}) : overrides_block requis pour {human.decision}"
    if human.overrides_block and not lifts_block:
        return "overrides_block sans blocage dur levé"
    return None


def review(
    payload: Any,
    verdicts: list[AgentVerdict],
    config: DecisionConfig,
    analyst: Actor | None,
    *,
    analysis_allows_override: bool,
) -> tuple[HumanReview | None, str | None]:
    """Valide la réponse brute reçue à la reprise, puis applique la politique."""
    try:
        human = HumanReview.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'réponse'} : {e['msg']}"
            for e in exc.errors()
        )
        return None, f"réponse invalide : {details}"
    error = check(
        human,
        verdicts,
        config,
        analyst,
        analysis_allows_override=analysis_allows_override,
    )
    return (None, error) if error else (human, None)

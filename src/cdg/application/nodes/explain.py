"""explain : explication du verdict figé, par le LLM puis contrôlée, sinon par le gabarit.

Jamais bloquante : une explication refusée est régénérée une fois (essais de la section
`explain`), puis remplacée par le gabarit ; une erreur du LLM mène au gabarit, sauf une
erreur passagère que la reprise du nœud relance (`retrying`, fourni par l'orchestrateur).
La source, les essais et les motifs sont scellés avec l'explication.
"""

from collections.abc import Callable
from typing import Any

from cdg.application.deps import Explainer, TemplateOnly
from cdg.application.state import ContractState
from cdg.domain import explanation
from cdg.domain.config import DecisionConfig
from cdg.domain.models import Usage


def explain(
    state: ContractState,
    explainer: Explainer | TemplateOnly,
    decision_config: DecisionConfig,
    retrying: Callable[[Exception], bool],
) -> dict[str, Any]:
    request = explanation.request(state)
    if isinstance(explainer, TemplateOnly):
        return {
            "explanation": explanation.template(request, reasons=[explainer.reason])
        }
    reasons: list[str] = []
    usage: list[Usage] = []
    result = None
    attempts = decision_config.explain.max_attempts
    for attempt in range(1, attempts + 1):
        try:
            draft, spent = explainer(request, list(reasons))
        except Exception as exc:
            if retrying(exc):  # erreur passagère : le nœud est repris
                raise
            reasons.append(f"essai {attempt} : {type(exc).__name__} : {exc}")
            result = explanation.template(request, reasons=reasons, attempts=attempt)
            break
        usage.append(spent)
        refused = explanation.refusals(draft, request)
        if not refused:
            result = explanation.accepted(
                draft, request, attempts=attempt, reasons=reasons
            )
            break
        reasons += [f"essai {attempt} : {reason}" for reason in refused]
    else:
        result = explanation.template(request, reasons=reasons, attempts=attempts)
    update: dict[str, Any] = {"explanation": result}
    if usage:
        update["usage"] = usage
    return update

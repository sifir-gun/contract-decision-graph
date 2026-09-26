"""Escalade d'un nœud à plusieurs sorties après un échec de nœud : route vers l'humain."""

from cdg.domain.decision import failure_report
from cdg.domain.models import NodeFailure


def escalate(failures: list[NodeFailure]) -> dict:
    """Mise à jour d'un nœud à plusieurs sorties : ESCALADE, route vers l'humain."""
    return {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": failure_report(failures),
    }

"""Rapport d'échec de nœud et escalade, communs aux nœuds qui routent vers l'humain."""

from cdg.domain.state import NodeFailure


def failure_report(failures: list[NodeFailure]) -> dict:
    return {"stage": "noeuds", "failures": [f.model_dump() for f in failures]}


def escalate(failures: list[NodeFailure]) -> dict:
    """Mise à jour d'un nœud à plusieurs sorties : ESCALADE, route vers l'humain."""
    return {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": failure_report(failures),
    }

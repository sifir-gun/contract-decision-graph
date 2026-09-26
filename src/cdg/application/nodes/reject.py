"""reject : verdict d'invalidité explicite, puis audit_seal.

`reject_reason` est écrit par validate_input ; `final_decision` reste None,
car `Decision` n'a pas de valeur « invalide ». Rien d'autre à écrire : le motif suffit
au scellement, qui distingue un rejet par lui (et non par des listes vides).
"""

from typing import Any

from cdg.application.state import ContractState


def reject(state: ContractState) -> dict[str, Any]:
    return {}

"""reject : verdict d'invalidité explicite, puis audit_seal.

`reject_reason` est écrit par validate_input ; `final_decision` reste None,
car `Decision` n'a pas de valeur « invalide ».
"""

from cdg.application.state import ContractState


def reject(state: ContractState) -> dict:
    # TODO J4 : contenu du rejet tel qu'il sera scellé par audit_seal.
    return {}

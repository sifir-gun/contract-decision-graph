"""audit_seal : sérialisation canonique, SHA-256, chaînage. Scelle aussi les rejets."""

from typing import Any

from cdg.application.state import ContractState


def audit_seal(state: ContractState) -> dict[str, Any]:
    # TODO J4 : canonical, decision_hash, chain_hash, écriture dans audit_decisions.
    return {}

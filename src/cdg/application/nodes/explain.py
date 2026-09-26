"""explain : justification rédigée à partir du verdict figé."""

from typing import Any

from cdg.application.state import ContractState


def explain(state: ContractState) -> dict[str, Any]:
    # TODO J4 : LLM sur le verdict figé, rejet d'un texte qui contredit la décision,
    # une régénération puis gabarit.
    return {}

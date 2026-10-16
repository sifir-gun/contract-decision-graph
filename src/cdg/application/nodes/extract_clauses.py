"""extract_clauses : extraction structurée par l'extracteur injecté ; incrémente les essais."""

from typing import Any

from cdg.application.deps import ExtractionResult, Extractor
from cdg.application.state import ContractState
from cdg.domain import profiles


def extract_clauses(state: ContractState, extractor: Extractor) -> dict[str, Any]:
    # `of_state` : un état d'avant les profils n'a pas la clé `profile`
    result = ExtractionResult.model_validate(
        extractor(
            state["raw_text"],
            state.get("extraction_feedback", []),
            profile=profiles.of_state(state),
        )
    )
    return {
        "clauses": result.clauses,
        "extraction_attempts": state["extraction_attempts"] + 1,
        "usage": result.usage,
    }

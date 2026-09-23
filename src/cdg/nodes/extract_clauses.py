"""extract_clauses : extraction structurée par l'extracteur injecté ; incrémente les essais."""

from cdg.deps import ExtractionResult, Extractor
from cdg.state import ContractState


def extract_clauses(state: ContractState, extractor: Extractor) -> dict:
    result = ExtractionResult.model_validate(
        extractor(state["raw_text"], state.get("extraction_feedback", [])))
    return {"clauses": result.clauses,
            "extraction_attempts": state["extraction_attempts"] + 1,
            "usage": result.usage}

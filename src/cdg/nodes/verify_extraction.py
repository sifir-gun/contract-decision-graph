"""verify_extraction : écrit `route` (analysts, extract_clauses ou human_review)."""

from cdg.state import ContractState


def verify_extraction(state: ContractState) -> dict:
    # TODO J3 : citations des clauses présentes vérifiées mot pour mot, REQUIRED_KINDS
    # couverts, ré-extraction avec retour ciblé, ESCALADE après extraction.max_attempts.
    return {"route": "analysts"}

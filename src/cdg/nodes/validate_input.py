"""validate_input : contrôle de l'entrée, écrit `route` (extract_clauses ou reject)."""

from cdg.state import ContractState


def validate_input(state: ContractState) -> dict:
    # TODO J3 : schéma, taille, langue, masquage (e-mails, téléphones, IBAN, SIREN, parties)
    if not state.get("raw_text", "").strip():
        return {"route": "reject", "reject_reason": "texte du contrat vide"}
    return {"route": "extract_clauses", "extraction_attempts": 0}

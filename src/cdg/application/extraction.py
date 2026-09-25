"""Extraction réelle des clauses par le LLM principal, contrat délimité comme donnée.

Le contrat (déjà masqué) est placé entre deux balises portant un jeton aléatoire :
un texte piégé ne peut pas « fermer » le bloc sans connaître le jeton. Le prompt
ne contient aucune règle de décision : le verdict reste au code (rules, gate).
"""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cdg.application.deps import ExtractionResult
from cdg.domain.state import Clause, TransferCategory
from cdg.ports.llm import LLMProvider

PROMPTS = Path(__file__).parent / "prompts"
Kind = Literal[
    "responsabilite_acheteur",
    "responsabilite_fournisseur",
    "revision_prix",
    "penalites_retard",
    "duree_engagement",
    "preavis_resiliation",
    "donnees_personnelles",
    "accord_traitement_donnees",
    "transfert_hors_ue",
]


class ExtractedClause(BaseModel):
    kind: Kind
    present: bool
    quote: str
    value: float | None
    category: TransferCategory | None = None


class ExtractionOutput(BaseModel):
    clauses: list[ExtractedClause]


def _boundary() -> str:
    return secrets.token_hex(8)


class LLMExtractor:
    def __init__(self, provider: LLMProvider, boundary: Callable[[], str] = _boundary):
        self._provider = provider
        self._boundary = boundary
        self._system = (PROMPTS / "extraction_system.md").read_text(encoding="utf-8")

    def _token(self, raw_text: str) -> str:
        token = self._boundary()
        while token in raw_text:  # un jeton déjà présent dans le texte serait falsifiable
            token = self._boundary()
        return token

    def _user_message(self, raw_text: str, feedback: list[str]) -> str:
        token = self._token(raw_text)
        parts = [
            f"Jeton de délimitation : {token}",
            f"<<<CONTRAT-{token}>>>\n{raw_text}\n<<<FIN-CONTRAT-{token}>>>",
        ]
        if feedback:  # hors du bloc du contrat : consignes du système, pas du document
            parts.append(
                "Retour de vérification de l'essai précédent, à corriger :\n"
                + "\n".join(f"- {problem}" for problem in feedback)
            )
        return "\n\n".join(parts)

    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult:
        output, usage = self._provider.structured(
            tier="main",
            system=self._system,
            user=self._user_message(raw_text, feedback),
            schema=ExtractionOutput,
            node="extract_clauses",
        )
        clauses = [Clause.model_validate(item.model_dump()) for item in output.clauses]
        return ExtractionResult(clauses=clauses, usage=[usage])

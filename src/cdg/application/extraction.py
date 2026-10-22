"""Extraction réelle des clauses par le LLM principal, contrat délimité comme donnée.

Le contrat (déjà masqué) est placé entre deux balises portant un jeton aléatoire :
un texte piégé ne peut pas « fermer » le bloc sans connaître le jeton. Le prompt
ne contient aucune règle de décision : le verdict reste au code (rules, gate).
"""

import secrets
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from cdg.application.deps import ExtractionResult
from cdg.domain import profiles
from cdg.domain.models import Clause, ClauseCategory, SubcontractCategory
from cdg.domain.profiles import Profile
from cdg.ports.llm import LLMProvider

PROMPTS = Path(__file__).parent / "prompts"
Kind = Literal[
    "responsabilite_acheteur",
    "responsabilite_fournisseur",
    "revision_prix",
    "penalites_execution",
    "delai_paiement",
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
    category: ClauseCategory | None = None


class ExtractionOutput(BaseModel):
    clauses: list[ExtractedClause]


# Schéma du profil `sous_traitance` (`profiles.SOUS_TRAITANCE`) : ses 9 types et leurs
# seules catégories. Un type ou une catégorie propre à un autre profil est refusé dès la
# sortie du modèle ; la catégorie d'un autre type du profil l'est par la vérification
# (`verification.problems_of`). Le faux Mistral choisit sa réponse d'après le nom du
# schéma : `ExtractionOutputSousTraitance`.
KindSousTraitance = Literal[
    "responsabilite_donneur_ordre",
    "responsabilite_sous_traitant",
    "protection_design",
    "revision_prix",
    "delai_paiement",
    "penalites_retard_execution",
    "obligation_certification",
    "assurance_sous_traitant",
    "delai_execution",
]


class ExtractedClauseSousTraitance(BaseModel):
    kind: KindSousTraitance
    present: bool
    quote: str
    value: float | None
    category: SubcontractCategory | None = None


class ExtractionOutputSousTraitance(BaseModel):
    clauses: list[ExtractedClauseSousTraitance]


# schéma de sortie et prompt système de chaque profil (ADR 010) : le profil `fournisseur`
# garde `ExtractionOutput` (son nom est celui que le faux Mistral reconnaît) et son prompt
SCHEMAS: Mapping[Profile, type[BaseModel]] = {
    "fournisseur": ExtractionOutput,
    "sous_traitance": ExtractionOutputSousTraitance,
}
PROMPT_FILES: Mapping[Profile, str] = {
    "fournisseur": "extraction_system.md",
    "sous_traitance": "extraction_sous_traitance_system.md",
}


def _boundary() -> str:
    return secrets.token_hex(8)


class LLMExtractor:
    def __init__(self, provider: LLMProvider, boundary: Callable[[], str] = _boundary):
        self._provider = provider
        self._boundary = boundary
        # prompts de tous les profils, lus une fois : un fichier manquant échoue au départ
        self._systems = {
            profile: (PROMPTS / PROMPT_FILES[profile]).read_text(encoding="utf-8")
            for profile in profiles.PROFILES
        }

    def _token(self, raw_text: str) -> str:
        token = self._boundary()
        # un jeton déjà présent dans le texte serait falsifiable
        while token in raw_text:
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

    def __call__(
        self, raw_text: str, feedback: list[str], profile: Profile
    ) -> ExtractionResult:
        profile = profiles.parse(profile)  # profil inconnu : erreur, avant tout appel
        output, usage = self._provider.structured(
            tier="main",
            system=self._systems[profile],
            user=self._user_message(raw_text, feedback),
            schema=SCHEMAS[profile],
            node="extract_clauses",
        )
        clauses = [
            Clause.model_validate(item) for item in output.model_dump()["clauses"]
        ]
        return ExtractionResult(clauses=clauses, usage=[usage])

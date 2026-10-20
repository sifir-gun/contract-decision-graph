"""Modèles métier, validés à chaque frontière de nœud.

La forme de l'état du graphe (réducteurs, route, entrée des analystes) est dans
`application/state.py`.
"""

from collections.abc import Collection
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cdg.domain.authorization import Actor

Domain = Literal["juridique", "financier", "conformite", "operationnel"]
Decision = Literal["GO", "GO_RESERVES", "NO_GO", "ESCALADE"]
RetrievalStatus = Literal["OK", "INSUFFISANT"]

DOMAINS: tuple[Domain, ...] = ("juridique", "financier", "conformite", "operationnel")

# Catégorie d'une clause de transfert (RGPD, art. 44 à 46) : ce que dit le contrat.
TransferCategory = Literal[
    "sans_transfert",  # données hébergées dans l'UE ou l'EEE
    "decision_adequation",
    "clauses_contractuelles_types",  # adoptées ou approuvées par la Commission (46, 2, c et d)
    "clauses_contractuelles_ad_hoc",  # propres aux parties, autorisation non mentionnée
    "clauses_contractuelles_ad_hoc_autorisees",  # autorisation de l'autorité mentionnée (46, 3, a)
    "regles_entreprise_contraignantes",
    "code_conduite",
    "certification",
    "aucune_garantie",  # transfert annoncé sans garantie nommée
]
TRANSFER_CATEGORIES: tuple[str, ...] = get_args(TransferCategory)

# Point de départ d'un délai de paiement (C. com., art. L441-10, I).
PaymentBasis = Literal[
    "date_facture",  # jours comptés à partir de la date d'émission de la facture
    "fin_de_mois",  # jours fin de mois
    "facture_periodique",  # jours après une facture périodique (L441-10, I, 4e alinéa)
]
PAYMENT_BASES: tuple[str, ...] = get_args(PaymentBasis)

ClauseCategory = Literal[TransferCategory, PaymentBasis]
CLAUSE_CATEGORIES: tuple[str, ...] = get_args(ClauseCategory)

# Catégories du profil `sous_traitance` (`docs/spec-profils-sous-traitance.md`) : ce que dit
# le contrat.

# Protection des dessins et documents remis au sous-traitant (`protection_design`).
DesignProtection = Literal[
    "usage_limite_au_projet",  # utilisés pour le seul projet, et confidentiels
    "confidentialite_seule",  # confidentialité, sans limite d'usage
    "reutilisation_autorisee",  # le sous-traitant peut les réutiliser
]
DESIGN_PROTECTIONS: tuple[str, ...] = get_args(DesignProtection)

# Obligation de certification (`obligation_certification`).
CertificationRequirement = Literal[
    "norme_et_preuves",  # normes nommées et preuves à remettre (essais, certificats)
    "norme_sans_preuves",  # normes nommées, aucune preuve exigée
    "conformite_generale",  # « conforme aux normes en vigueur », aucune norme nommée
]
CERTIFICATION_REQUIREMENTS: tuple[str, ...] = get_args(CertificationRequirement)

# Délai d'exécution (`delai_execution`).
ExecutionSchedule = Literal[
    "date_contractuelle",  # date ou planning qui engage le sous-traitant
    "delai_indicatif",
]
EXECUTION_SCHEDULES: tuple[str, ...] = get_args(ExecutionSchedule)

SubcontractCategory = Literal[
    DesignProtection, CertificationRequirement, ExecutionSchedule, PaymentBasis
]


class Clause(BaseModel):
    kind: str  # un des types du profil (`domain/profiles.py`)
    present: bool  # la clause figure-t-elle dans le contrat ?
    quote: str  # citation exacte si present, "" sinon (alors non vérifiée)
    value: float | None  # quantité utile à la règle, voir « Règles par domaine »
    category: str | None = (
        None  # types à catégorie du profil seulement (`ProfileSpec.kind_categories`)
    )

    @model_validator(mode="after")
    def _citation_selon_presence(self) -> "Clause":
        if not self.present and self.quote != "":
            raise ValueError(f"clause absente avec une citation : {self.kind}")
        if self.present and not self.quote.strip():
            raise ValueError(f"clause présente sans citation : {self.kind}")
        return self


def category_required(clause: Clause, category_kinds: Collection[str]) -> bool:
    """Catégorie exigée : transfert présent ; délai de paiement présent et chiffré (un délai
    non chiffré peut ne pas dire son point de départ). `category_kinds` : les types à
    catégorie du profil (`ProfileSpec.category_kinds`)."""
    if clause.kind not in category_kinds or not clause.present:
        return False
    return clause.kind != "delai_paiement" or clause.value is not None


class ClauseRetrieval(BaseModel):
    """Résumé du CRAG pour un type de clause : les références qui la justifient."""

    kind: str
    queries: list[str]  # requêtes essayées, dans l'ordre
    passes: int = Field(ge=0)  # recherches effectuées
    retained: list[str]  # références retenues, en vigueur à la date d'analyse
    expired: list[str]  # références pertinentes mais expirées : jamais retenues


class RetrievalTrace(BaseModel):
    """Résumé du CRAG d'un domaine, porté par le verdict pour l'audit : une entrée par
    clause recherchée, chaque référence retenue rattachée à la clause qu'elle justifie, et
    les constats propres au CRAG (références expirées). Figé, il suffit au rejeu de la
    justification (`domain/audit.py`)."""

    clauses: list[ClauseRetrieval]
    findings: list[str] = []  # constats du CRAG ; défaut : checkpoints du J3 lisibles


class AgentVerdict(BaseModel):
    domain: Domain
    score: float = Field(ge=0.0, le=1.0)  # 1 = favorable
    hard_block: bool
    findings: list[str]
    # clause de chaque constat, dans l'ordre de `findings` ; None : constat du CRAG. Vide
    # pour un verdict d'avant le J4 (checkpoints) : constats non rattachés
    finding_kinds: list[str | None] = []
    evidence_ids: list[str]
    retrieval_status: RetrievalStatus
    retrieval: RetrievalTrace | None = None  # résumé du CRAG ; None pour une doublure

    @model_validator(mode="after")
    def _un_rattachement_par_constat(self) -> "AgentVerdict":
        if self.finding_kinds and len(self.finding_kinds) != len(self.findings):
            raise ValueError(
                f"verdict {self.domain} : {len(self.finding_kinds)} finding_kinds pour "
                f"{len(self.findings)} constats"
            )
        return self


SYSTEM_REVIEWER_PREFIX = "systeme:"


class HumanDecision(BaseModel):
    """Décision humaine au format v1 (avant la PR D2), figé : relecteur nommé. Relue dans
    les anciens états du checkpointer et les enregistrements v1 ; jamais créée."""

    decision: Decision
    reviewer: str
    reason: str
    overrides_block: bool = False  # vrai si l'humain lève un blocage dur
    source: Literal["humain", "systeme"] = "humain"  # systeme : expire (timeout)

    @model_validator(mode="after")
    def _decision_systeme(self) -> "HumanDecision":
        system_reviewer = self.reviewer.startswith(SYSTEM_REVIEWER_PREFIX)
        if self.source == "systeme":
            # échec fermé : jamais d'approbation automatique
            if self.decision != "NO_GO":
                raise ValueError("une décision système ne peut être que NO_GO")
            if not system_reviewer:
                raise ValueError(
                    f"décision système : relecteur {SYSTEM_REVIEWER_PREFIX}…"
                )
        elif system_reviewer:
            raise ValueError(
                f"le préfixe {SYSTEM_REVIEWER_PREFIX} est réservé aux décisions système"
            )
        return self


class HumanReview(BaseModel):
    """Décision humaine, format v2 (PR D2) : l'acteur qui tranche (canal, iss et sub, ou
    opérateur non nominatif) remplace le relecteur nommé ; jamais de nom ni d'e-mail."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    acteur: Actor
    reason: str
    overrides_block: bool = False  # vrai si l'humain lève un blocage dur
    source: Literal["humain", "systeme"] = "humain"  # systeme : expiration (timeout)

    @model_validator(mode="after")
    def _decision_systeme(self) -> "HumanReview":
        # échec fermé : jamais d'approbation automatique
        if self.source == "systeme" and self.decision != "NO_GO":
            raise ValueError("une décision système ne peut être que NO_GO")
        return self


class NodeFailure(BaseModel):
    """Échec d'un nœud, capté par la garde de l'orchestrateur : jamais de repli silencieux."""

    node: str
    error: str  # type de l'exception
    message: str
    attempts: int = Field(
        ge=1
    )  # tentatives, reprises comprises (RetryPolicy des analystes)
    domain: Domain | None = None  # analyste en échec


class Usage(BaseModel):
    node: str
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)

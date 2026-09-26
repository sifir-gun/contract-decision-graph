"""Modèles métier, validés à chaque frontière de nœud.

La forme de l'état du graphe (réducteurs, route, entrée des analystes) est dans
`application/state.py`.
"""

from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

Domain = Literal["juridique", "financier", "conformite", "operationnel"]
Decision = Literal["GO", "GO_RESERVES", "NO_GO", "ESCALADE"]
RetrievalStatus = Literal["OK", "INSUFFISANT"]

DOMAINS: tuple[Domain, ...] = ("juridique", "financier", "conformite", "operationnel")

REQUIRED_KINDS = (
    "responsabilite_acheteur",
    "responsabilite_fournisseur",
    "revision_prix",
    "penalites_execution",  # dues par le fournisseur qui exécute en retard (C. civ., art. 1231-5)
    "delai_paiement",  # délai de paiement par l'acheteur (C. com., art. L441-10)
    "duree_engagement",
    "preavis_resiliation",
    "donnees_personnelles",
    "accord_traitement_donnees",
    "transfert_hors_ue",
)

# Clauses jugées par chaque domaine : une partition des REQUIRED_KINDS. Sert aux requêtes
# du CRAG, construites à partir des seuls types et valeurs des clauses du domaine.
DOMAIN_KINDS: dict[Domain, tuple[str, ...]] = {
    "juridique": ("responsabilite_acheteur", "responsabilite_fournisseur"),
    "financier": ("revision_prix", "penalites_execution", "delai_paiement"),
    "conformite": (
        "donnees_personnelles",
        "accord_traitement_donnees",
        "transfert_hors_ue",
    ),
    "operationnel": ("duree_engagement", "preavis_resiliation"),
}

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
# types qui portent une catégorie, et catégories admises pour chacun
KIND_CATEGORIES: dict[str, tuple[str, ...]] = {
    "transfert_hors_ue": TRANSFER_CATEGORIES,
    "delai_paiement": PAYMENT_BASES,
}
CATEGORY_KINDS = frozenset(KIND_CATEGORIES)


class Clause(BaseModel):
    kind: str  # un des REQUIRED_KINDS
    present: bool  # la clause figure-t-elle dans le contrat ?
    quote: str  # citation exacte si present, "" sinon (alors non vérifiée)
    value: float | None  # quantité utile à la règle, voir « Règles par domaine »
    category: str | None = (
        None  # types de CATEGORY_KINDS seulement, voir KIND_CATEGORIES
    )

    @model_validator(mode="after")
    def _citation_selon_presence(self) -> "Clause":
        if not self.present and self.quote != "":
            raise ValueError(f"clause absente avec une citation : {self.kind}")
        if self.present and not self.quote.strip():
            raise ValueError(f"clause présente sans citation : {self.kind}")
        return self


def category_required(clause: Clause) -> bool:
    """Catégorie exigée : transfert présent ; délai de paiement présent et chiffré (un délai
    non chiffré peut ne pas dire son point de départ)."""
    if clause.kind not in CATEGORY_KINDS or not clause.present:
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

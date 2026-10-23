"""Profils de contrat : ce que le code sait d'un genre de contrat (ADR 010).

Un profil nomme un genre de contrat analysable. Il porte les constantes du domaine qui
dépendent du genre : ses types de clauses, leur partition entre les quatre domaines, l'unité
de la valeur de chaque type chiffré, les catégories admises de chaque type catégoriel et les
noms des parties dans les constats. Deux profils sont enregistrés : `fournisseur`, qui reprend
à l'identique les constantes qui étaient globales, sans changer aucun comportement, et
`sous_traitance` (`SOUS_TRAITANCE`, spec-profils-sous-traitance.md).

Ce module n'importe que `domain/models.py` (ni `domain/config.py`, ni `domain/rules/` :
pas de cycle configuration -> profils -> règles -> configuration).

Le profil d'une analyse est obligatoire, sans valeur par défaut. Seule exception, `of_state` :
un état d'avant les profils (analyse interrompue, contrat à relancer) n'a pas la clé
`profile`, et seul `fournisseur` était alors possible.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, get_args

from cdg.domain.models import (
    CERTIFICATION_REQUIREMENTS,
    DESIGN_PROTECTIONS,
    EXECUTION_SCHEDULES,
    PAYMENT_BASES,
    TRANSFER_CATEGORIES,
    Domain,
)

Profile = Literal["fournisseur", "sous_traitance"]
PROFILES: tuple[Profile, ...] = get_args(Profile)

# profil des états d'avant les profils : le seul possible alors (`of_state`)
LEGACY_PROFILE: Profile = "fournisseur"


@dataclass(frozen=True)
class Parties:
    """Noms des deux parties dans les constats du profil, avec leur forme au génitif."""

    client: str  # celle qui commande et paie
    client_of: str
    contractor: str  # celle qui exécute
    contractor_of: str


@dataclass(frozen=True)
class ProfileSpec:
    kinds: tuple[str, ...]  # types de clauses, tous rendus une fois et une seule
    # clauses jugées par chaque domaine : une partition de `kinds`. Sert aux requêtes du
    # CRAG, construites à partir des seuls types et valeurs des clauses du domaine
    domain_kinds: Mapping[Domain, tuple[str, ...]]
    # unité de la valeur de chaque type chiffré (« Règles par domaine » de la spec)
    value_units: Mapping[str, str]
    # types qui portent une catégorie, et catégories admises pour chacun
    kind_categories: Mapping[str, tuple[str, ...]]
    # noms des parties dans les constats : sans valeur par défaut, un profil les nomme
    parties: Parties

    def __post_init__(self) -> None:
        # copies en lecture seule : un profil ne change pas après sa construction
        for name in ("domain_kinds", "value_units", "kind_categories"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @property
    def category_kinds(self) -> frozenset[str]:
        return frozenset(self.kind_categories)


# Profil de la sous-traitance industrielle (spec-profils-sous-traitance.md), enregistré dans
# `SPECS` sous ce nom : la configuration valide ses termes contre lui. Les types qu'il
# partage avec `fournisseur` y ont le même domaine.
SOUS_TRAITANCE = ProfileSpec(
    kinds=(
        "responsabilite_donneur_ordre",
        "responsabilite_sous_traitant",
        "protection_design",
        "revision_prix",
        "delai_paiement",
        "penalites_retard_execution",
        "obligation_certification",
        "assurance_sous_traitant",
        "delai_execution",
    ),
    domain_kinds={
        "juridique": (
            "responsabilite_donneur_ordre",
            "responsabilite_sous_traitant",
            "protection_design",
        ),
        "financier": ("revision_prix", "delai_paiement", "penalites_retard_execution"),
        "conformite": ("obligation_certification", "assurance_sous_traitant"),
        "operationnel": ("delai_execution",),
    },
    value_units={
        "responsabilite_donneur_ordre": "%",
        "responsabilite_sous_traitant": "%",
        "revision_prix": "%",
        "delai_paiement": "jours",
        "penalites_retard_execution": "%",
        "assurance_sous_traitant": "€",
    },
    kind_categories={
        "protection_design": DESIGN_PROTECTIONS,
        "obligation_certification": CERTIFICATION_REQUIREMENTS,
        "delai_execution": EXECUTION_SCHEDULES,
        "delai_paiement": PAYMENT_BASES,
    },
    parties=Parties(
        "donneur d'ordre", "du donneur d'ordre", "sous-traitant", "du sous-traitant"
    ),
)


SPECS: Mapping[Profile, ProfileSpec] = MappingProxyType(
    {
        "fournisseur": ProfileSpec(
            kinds=(
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
            ),
            domain_kinds={
                "juridique": ("responsabilite_acheteur", "responsabilite_fournisseur"),
                "financier": ("revision_prix", "penalites_execution", "delai_paiement"),
                "conformite": (
                    "donnees_personnelles",
                    "accord_traitement_donnees",
                    "transfert_hors_ue",
                ),
                "operationnel": ("duree_engagement", "preavis_resiliation"),
            },
            value_units={
                "responsabilite_acheteur": "%",
                "responsabilite_fournisseur": "%",
                "revision_prix": "%",
                "penalites_execution": "%",
                "delai_paiement": "jours",
                "duree_engagement": "mois",
                "preavis_resiliation": "mois",
            },
            kind_categories={
                "transfert_hors_ue": TRANSFER_CATEGORIES,
                "delai_paiement": PAYMENT_BASES,
            },
            parties=Parties(
                "acheteur", "de l'acheteur", "fournisseur", "du fournisseur"
            ),
        ),
        "sous_traitance": SOUS_TRAITANCE,
    }
)


def parse(value: object) -> Profile:
    """Profil nommé par `value`. Toute autre valeur, `None` et chaîne vide compris, est
    refusée : jamais de profil par défaut."""
    for profile in PROFILES:
        if value == profile:
            return profile
    raise ValueError(f"profil inconnu : {value!r} ; profils : {', '.join(PROFILES)}")


def of_state(state: Mapping[str, Any]) -> Profile:
    """Profil d'un état du graphe. Sans clé `profile` (état d'avant les profils), le seul
    profil qui existait alors ; avec une clé, un profil connu ou une erreur."""
    if "profile" not in state:
        return LEGACY_PROFILE
    return parse(state["profile"])


def spec(profile: Profile) -> ProfileSpec:
    return SPECS[parse(profile)]


def _kind_domain_table(specs: Iterable[ProfileSpec]) -> dict[str, Domain]:
    """Domaine de chaque type de clause. Un type commun à plusieurs profils appartient au
    même domaine dans tous ; un type rangé dans deux domaines est une erreur."""
    table: dict[str, Domain] = {}
    for profile_spec in specs:
        for domain, kinds in profile_spec.domain_kinds.items():
            for kind in kinds:
                if table.setdefault(kind, domain) != domain:
                    raise ValueError(
                        f"type de clause rangé dans deux domaines : {kind} "
                        f"({table[kind]}, {domain})"
                    )
    return table


# types de clauses de tous les profils, et domaine de chacun : le corpus en déduit le
# domaine d'indexation d'une source (`corpus.by_domain`)
ALL_KINDS: frozenset[str] = frozenset(
    kind for profile_spec in SPECS.values() for kind in profile_spec.kinds
)
KIND_DOMAIN: Mapping[str, Domain] = MappingProxyType(_kind_domain_table(SPECS.values()))


def kinds_of_domain(domain: Domain) -> tuple[str, ...]:
    """Types de clauses d'un domaine, en union sur les profils, dans l'ordre de déclaration."""
    kinds: dict[str, None] = {}
    for profile in PROFILES:
        for kind in SPECS[profile].domain_kinds.get(domain, ()):
            kinds.setdefault(kind)
    return tuple(kinds)

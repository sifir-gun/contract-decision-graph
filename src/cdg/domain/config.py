"""Chargement et validation de config/decision.yaml. Invalide : ConfigError, arrêt au démarrage."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, assert_never, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cdg.domain import profiles
from cdg.domain.models import (
    CERTIFICATION_REQUIREMENTS,
    DESIGN_PROTECTIONS,
    EXECUTION_SCHEDULES,
    CertificationRequirement,
    Decision,
    DesignProtection,
    Domain,
    ExecutionSchedule,
    TransferCategory,
)
from cdg.domain.numeric import rounded
from cdg.domain.profiles import Profile

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "decision.yaml"

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Penalty = Unit
Quantity = Annotated[float, Field(ge=0.0)]


class ConfigError(Exception):
    """Configuration absente, illisible ou invalide."""


class _Strict(BaseModel):
    # strict : pas de conversion silencieuse (booléen ou chaîne pris pour un nombre)
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Weights(_Strict):
    juridique: Unit
    financier: Unit
    conformite: Unit
    operationnel: Unit

    @model_validator(mode="after")
    def _somme_egale_a_un(self) -> "Weights":
        total = rounded(
            self.juridique + self.financier + self.conformite + self.operationnel
        )
        if total != 1.0:
            raise ValueError(f"la somme des poids vaut {total}, attendu 1")
        return self


class Thresholds(_Strict):
    go: Annotated[float, Field(gt=0.0, le=1.0)]
    go_reserves: Annotated[float, Field(gt=0.0, le=1.0)]

    @model_validator(mode="after")
    def _seuils_ordonnes(self) -> "Thresholds":
        if not rounded(self.go_reserves) < rounded(self.go):
            raise ValueError("go_reserves doit être strictement inférieur à go")
        return self


class Budget(_Strict):
    max_tokens_per_contract: Annotated[int, Field(gt=0)]


Term = Annotated[str, Field(min_length=1)]
# types de clauses -> termes qui évoquent la clause (une liste non vide par type)
AbsenceTerms = dict[str, Annotated[list[Term], Field(min_length=1)]]
# types à catégorie -> catégorie -> termes qui l'évoquent, de la plus spécifique à la moins
# spécifique (l'ordre fait foi)
CategoryTerms = dict[str, dict[str, Annotated[list[Term], Field(min_length=1)]]]


def _check_terms(
    absence_terms: Mapping[str, Sequence[str]],
    category_terms: Mapping[str, Mapping[str, Sequence[str]]],
    spec: profiles.ProfileSpec,
) -> None:
    """Termes d'absence et de catégorie d'un profil, contre les types et les catégories de
    ce profil : un jeu par type, aucun type inconnu, aucune catégorie inconnue, aucun
    terme répété dans un type (casse ignorée). Partagée par la section du fournisseur
    (`extraction`) et par celle de la sous-traitance."""
    profile_kinds = set(spec.kinds)
    kinds = set(absence_terms)
    if kinds != profile_kinds:
        missing = sorted(profile_kinds - kinds)
        unknown = sorted(kinds - profile_kinds)
        raise ValueError(
            f"absence_terms : types manquants {missing}, types inconnus {unknown}"
        )
    for kind, terms in absence_terms.items():
        if len({t.casefold() for t in terms}) != len(terms):
            raise ValueError(f"absence_terms : terme répété pour {kind}")
    kind_categories = spec.kind_categories
    kinds = set(category_terms)
    if kinds != set(kind_categories):
        missing = sorted(set(kind_categories) - kinds)
        unknown = sorted(kinds - set(kind_categories))
        raise ValueError(
            f"category_terms : types manquants {missing}, types inconnus {unknown}"
        )
    for kind, by_category in category_terms.items():
        for category in by_category:
            if category not in kind_categories[kind]:
                raise ValueError(
                    f"category_terms : catégorie inconnue pour {kind} : {category}"
                )
        folded = [t.casefold() for ts in by_category.values() for t in ts]
        if len(set(folded)) != len(folded):
            raise ValueError(f"category_terms : terme répété pour {kind}")


class Extraction(_Strict):
    max_attempts: Annotated[int, Field(ge=1)]
    # vérification des absences : termes qui évoquent chaque type de clause
    absence_terms: AbsenceTerms
    # cohérence entre catégorie et citation (J5) : pour chaque type à catégorie, des termes
    # par catégorie, de la plus spécifique à la moins spécifique (l'ordre fait foi)
    category_terms: CategoryTerms

    @model_validator(mode="after")
    def _termes_du_profil_fournisseur(self) -> "Extraction":
        # ces sections sont celles du profil fournisseur (ADR 010) ; celles de la
        # sous-traitance sont dans la section `sous_traitance`
        _check_terms(
            self.absence_terms, self.category_terms, profiles.spec("fournisseur")
        )
        return self


class ExplainConfig(_Strict):
    # essais du LLM, le premier compris ; ensuite le gabarit
    max_attempts: Annotated[int, Field(ge=1)]


class JuridiqueRules(_Strict):
    supplier_cap_min_pct: Quantity
    supplier_cap_score_penalty: Penalty


class FinancierRules(_Strict):
    # pénalités d'exécution dues par le fournisseur (C. civ., art. 1231-5)
    execution_penalties_min_cap_pct: Quantity
    execution_penalties_score_penalty: Penalty
    # délai de paiement par l'acheteur (C. com., art. L441-10)
    payment_delay_max_days_invoice: Quantity  # à compter de la date de facture
    payment_delay_max_days_end_of_month: Quantity  # jours fin de mois
    payment_delay_max_days_periodic_invoice: Quantity  # après une facture périodique
    payment_delay_score_penalty: Penalty


class OperationnelRules(_Strict):
    commitment_max_months: Quantity
    commitment_score_penalty: Penalty
    notice_max_months: Quantity
    notice_score_penalty: Penalty


class ConformiteRules(_Strict):
    # garanties nommées acceptées pour un transfert hors UE (RGPD, art. 45 et 46)
    transfer_safeguards: Annotated[list[TransferCategory], Field(min_length=1)]
    # garanties dont l'autorisation par l'autorité de contrôle reste à vérifier (46, 3, a)
    transfer_authorization_to_verify: list[TransferCategory]
    transfer_authorization_score_penalty: Penalty
    unlocated_data_score_penalty: Penalty  # localisation des données non précisée

    @model_validator(mode="after")
    def _garanties(self) -> "ConformiteRules":
        for name in ("transfer_safeguards", "transfer_authorization_to_verify"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contient un doublon")
            if {"sans_transfert", "aucune_garantie"} & set(values):
                raise ValueError(
                    "sans_transfert et aucune_garantie ne sont pas des garanties"
                )
        both = set(self.transfer_safeguards) & set(
            self.transfer_authorization_to_verify
        )
        if both:
            raise ValueError(
                f"catégories à la fois reconnues et à vérifier : {sorted(both)}"
            )
        return self


class RulesConfig(_Strict):
    juridique: JuridiqueRules
    financier: FinancierRules
    conformite: ConformiteRules
    operationnel: OperationnelRules


def _check_partition(
    kind: str, categories: Iterable[str], lists: Mapping[str, Sequence[str]]
) -> None:
    """Chaque catégorie du type `kind` figure dans exactement une des listes réglées (les
    valeurs hors catégories sont déjà refusées par le type `Literal` des listes)."""
    placed = Counter(category for values in lists.values() for category in values)
    twice = sorted(category for category, n in placed.items() if n > 1)
    if twice:
        raise ValueError(
            f"{kind} : catégories classées plus d'une fois dans "
            f"{', '.join(lists)} : {twice}"
        )
    missing = sorted(set(categories) - set(placed))
    if missing:
        raise ValueError(
            f"{kind} : catégories dans aucune liste de {', '.join(lists)} : {missing}"
        )


class SousTraitanceJuridique(_Strict):
    # plafond de responsabilité du sous-traitant, % du montant du contrat
    subcontractor_cap_min_pct: Quantity
    subcontractor_cap_score_penalty: Penalty
    # protection des dessins et documents remis : chaque catégorie dans une seule liste.
    # Acceptée : rien ; pénalisée : pénalité ; bloquante : blocage dur
    design_accepted: list[DesignProtection]
    design_penalized: list[DesignProtection]
    design_blocking: list[DesignProtection]
    design_score_penalty: Penalty  # catégorie pénalisée, ou clause absente

    @model_validator(mode="after")
    def _protection_des_dessins(self) -> "SousTraitanceJuridique":
        _check_partition(
            "protection_design",
            DESIGN_PROTECTIONS,
            {
                "design_accepted": self.design_accepted,
                "design_penalized": self.design_penalized,
                "design_blocking": self.design_blocking,
            },
        )
        return self


class SousTraitanceFinancier(_Strict):
    # pénalités de retard d'exécution dues par le sous-traitant (C. civ., art. 1231-5)
    execution_delay_penalties_min_cap_pct: Quantity
    execution_delay_penalties_score_penalty: Penalty  # aussi si la clause est absente
    # délai de paiement par le donneur d'ordre (C. com., art. L441-10)
    payment_delay_max_days_invoice: Quantity  # à compter de la date de facture
    payment_delay_max_days_end_of_month: Quantity  # jours fin de mois
    payment_delay_max_days_periodic_invoice: Quantity  # après une facture périodique
    payment_delay_score_penalty: Penalty


class SousTraitanceConformite(_Strict):
    # obligation de certification : chaque catégorie dans une seule liste, acceptée ou
    # pénalisée ; la clause absente bloque (spec), sans réglage
    certification_accepted: list[CertificationRequirement]
    certification_penalized: list[CertificationRequirement]
    certification_score_penalty: Penalty
    # assurance du sous-traitant : montant minimal garanti, € par sinistre
    insurance_min_eur: Quantity
    insurance_score_penalty: Penalty  # sous le minimum, non chiffrée ou absente

    @model_validator(mode="after")
    def _obligation_de_certification(self) -> "SousTraitanceConformite":
        _check_partition(
            "obligation_certification",
            CERTIFICATION_REQUIREMENTS,
            {
                "certification_accepted": self.certification_accepted,
                "certification_penalized": self.certification_penalized,
            },
        )
        return self


class SousTraitanceOperationnel(_Strict):
    # délai d'exécution : chaque catégorie dans une seule liste
    schedule_accepted: list[ExecutionSchedule]
    schedule_penalized: list[ExecutionSchedule]
    schedule_score_penalty: Penalty  # catégorie pénalisée, ou clause absente

    @model_validator(mode="after")
    def _delai_d_execution(self) -> "SousTraitanceOperationnel":
        _check_partition(
            "delai_execution",
            EXECUTION_SCHEDULES,
            {
                "schedule_accepted": self.schedule_accepted,
                "schedule_penalized": self.schedule_penalized,
            },
        )
        return self


class SousTraitanceRules(_Strict):
    juridique: SousTraitanceJuridique
    financier: SousTraitanceFinancier
    conformite: SousTraitanceConformite
    operationnel: SousTraitanceOperationnel


class SousTraitanceConfig(_Strict):
    """Réglages du profil `sous_traitance` (spec, « Configuration »). Les sections
    `rules` et `extraction` du fichier restent celles du profil fournisseur.

    Section apparue en PR 2, après l'archive des configurations : absente par défaut du
    modèle (`DecisionConfig.sous_traitance`), pour relire une configuration archivée,
    mais exigée du fichier du projet (`load_config`, ADR 006)."""

    rules: SousTraitanceRules
    # termes qui évoquent chaque type de clause du profil, et chaque catégorie (voir
    # `Extraction`) ; validés contre les types du profil sous_traitance
    absence_terms: AbsenceTerms
    category_terms: CategoryTerms

    @model_validator(mode="after")
    def _termes_du_profil_sous_traitance(self) -> "SousTraitanceConfig":
        _check_terms(self.absence_terms, self.category_terms, profiles.SOUS_TRAITANCE)
        return self


class HumanPolicy(_Strict):
    allowed_decisions: Annotated[list[Decision], Field(min_length=1)]
    allow_block_override: bool
    hard_block_review: bool  # vrai : un blocage dur passe en revue humaine

    @model_validator(mode="after")
    def _decisions_coherentes(self) -> "HumanPolicy":
        if len(set(self.allowed_decisions)) != len(self.allowed_decisions):
            raise ValueError("allowed_decisions contient un doublon")
        if "ESCALADE" in self.allowed_decisions:
            raise ValueError("ESCALADE interdite : l'humain doit trancher")
        if "NO_GO" not in self.allowed_decisions:
            raise ValueError("NO_GO doit rester possible pour l'humain")
        return self


class InputConfig(_Strict):
    max_chars: Annotated[int, Field(gt=0)]
    min_words: Annotated[int, Field(gt=0)]  # en dessous, la langue n'est pas vérifiable
    min_french_ratio: Unit  # part minimale de mots-outils français
    # détection d'instructions adressées à l'outil (expressions régulières, casse ignorée)
    instruction_patterns: Annotated[list[Term], Field(min_length=1)]

    @model_validator(mode="after")
    def _motifs_valides(self) -> "InputConfig":
        for pattern in self.instruction_patterns:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"motif d'instruction invalide {pattern!r} : {exc}")
        return self


class EmbeddingConfig(_Strict):
    model: Annotated[str, Field(min_length=1)]
    dimension: Annotated[
        int, Field(gt=0)
    ]  # = colonne rag_chunks.embedding, contrôlé par setup-db
    query_prefix: str  # préfixes exigés par la famille e5
    passage_prefix: str


class CorpusConfig(_Strict):
    chunk_max_words: Annotated[int, Field(gt=0)]  # e5 : 512 tokens au plus


class RetrySettings(_Strict):
    """RetryPolicy d'un nœud (analystes, extraction), avant la garde d'échec."""

    max_attempts: Annotated[int, Field(gt=0)]  # tentatives, la première comprise
    initial_interval_seconds: Annotated[float, Field(ge=0.0)]
    backoff_factor: Annotated[float, Field(ge=1.0)]
    max_interval_seconds: Annotated[float, Field(ge=0.0)]
    jitter: bool


class InterruptedConfig(_Strict):
    """Analyse interrompue (processus arrêté ou mort) : reprises automatiques au plus,
    comptées par contrat ; au-delà, ESCALADE (`domain/resumption.py`)."""

    max_resumes: Annotated[int, Field(gt=0)]


class SearchConfig(_Strict):
    """Recherche dans le corpus (ADR 006), mesurée par `mesure-recherche`.

    Réglages apparus après l'archive des configurations : leur valeur par défaut est le
    comportement d'avant, pour qu'une configuration archivée sans eux se relise telle
    qu'elle était (rejeu). Le fichier du projet les règle explicitement (`load_config`).
    """

    # un seul extrait par référence parmi les top_k, le plus proche de la requête
    distinct_references: bool = False


class CragConfig(_Strict):
    top_k: Annotated[int, Field(gt=0)]  # extraits rendus par recherche, soumis au juge
    max_passes: Annotated[int, Field(gt=0)]  # recherches au plus, réécritures comprises
    search: SearchConfig = Field(default_factory=SearchConfig)  # voir SearchConfig


Tier = Literal["main", "light"]
ModelId = Annotated[str, Field(min_length=1)]


class TierModels(_Strict):
    main: ModelId  # extraction, explication
    light: ModelId  # juge CRAG, réécriture de requête

    @model_validator(mode="after")
    def _identifiants_figes(self) -> "TierModels":
        # un alias mouvant changerait de modèle sans changer la config : rejeu faussé
        for model in (self.main, self.light):
            if "latest" in model:
                raise ValueError(
                    f"alias mouvant refusé : {model} (identifiant figé attendu)"
                )
        return self


Provider = Literal["mistral", "anthropic"]


class LLMConfig(_Strict):
    provider: Provider
    temperature: Annotated[float, Field(ge=0.0, le=1.0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    timeout_seconds: Annotated[int, Field(gt=0)]
    models: dict[Provider, TierModels]

    @model_validator(mode="after")
    def _modeles_du_fournisseur(self) -> "LLMConfig":
        if self.provider not in self.models:
            raise ValueError(
                f"aucun modèle configuré pour le fournisseur {self.provider}"
            )
        return self

    def model(self, tier: Tier, provider: str | None = None) -> str:
        """Modèle d'un niveau, pour le fournisseur configuré ou celui indiqué."""
        # un fournisseur inconnu lève KeyError, comme avant : cast sans validation
        models = self.models[cast(Provider, provider or self.provider)]
        return models.main if tier == "main" else models.light


class ProfileTerms(NamedTuple):
    """Termes de la vérification de l'extraction pour un profil : ceux qui évoquent
    chaque type de clause (absences), et chaque catégorie (`verification.problems_of`)."""

    absence_terms: Mapping[str, Sequence[str]]
    category_terms: Mapping[str, Mapping[str, Sequence[str]]]


class DecisionConfig(_Strict):
    weights: Weights
    thresholds: Thresholds
    min_margin: Annotated[float, Field(ge=0.0, lt=1.0)]
    conflict_gap: Annotated[float, Field(gt=0.0, le=1.0)]
    budget: Budget
    extraction: Extraction
    rules: RulesConfig
    # profil sous_traitance (PR 2) : None seulement pour relire une configuration archivée
    # d'avant ; le fichier du projet la règle (`load_config`)
    sous_traitance: SousTraitanceConfig | None = None
    human_policy: HumanPolicy
    input: InputConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    corpus: CorpusConfig
    crag: CragConfig
    analyst_retry: RetrySettings
    extraction_retry: RetrySettings
    explain: ExplainConfig
    explain_retry: RetrySettings
    interrupted: InterruptedConfig

    def weight(self, domain: Domain) -> float:
        weight: float = getattr(self.weights, domain)
        return weight

    def terms(self, profile: Profile) -> ProfileTerms:
        """Termes d'absence et de catégorie du profil : la section `extraction` pour le
        fournisseur (celle d'avant les profils), la section `sous_traitance` pour la
        sous-traitance. Sans cette section (configuration archivée d'avant elle), erreur
        qui la nomme : jamais les termes d'un autre profil."""
        profile = profiles.parse(profile)
        if profile == "fournisseur":
            section = self.extraction
            return ProfileTerms(section.absence_terms, section.category_terms)
        if profile == "sous_traitance":
            if self.sous_traitance is None:
                raise ValueError(
                    "configuration sans section sous_traitance : profil sous_traitance "
                    "non analysable"
                )
            st = self.sous_traitance
            return ProfileTerms(st.absence_terms, st.category_terms)
        assert_never(profile)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> DecisionConfig:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration introuvable : {path}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML illisible dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un dictionnaire YAML")
    try:
        config = DecisionConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"configuration invalide dans {path} :\n{exc}") from exc
    missing = _unset(config)
    if missing:
        raise ConfigError(
            f"réglages absents de {path} : {', '.join(missing)} ; tout se règle "
            "explicitement, les valeurs par défaut du modèle ne servent qu'à relire les "
            "configurations archivées"
        )
    return config


def _unset(model: BaseModel, prefix: str = "") -> list[str]:
    """Champs du modèle, à toute profondeur, que le fichier ne règle pas lui-même : absents,
    ou nuls (la valeur `None` n'existe que par défaut du modèle, pour les archives)."""
    missing = []
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        if name not in model.model_fields_set:
            missing.append(path)
            continue
        value = getattr(model, name)
        if value is None:
            missing.append(path)
            continue
        nested = value.items() if isinstance(value, dict) else [("", value)]
        for key, item in nested:
            if isinstance(item, BaseModel):
                missing += _unset(item, f"{path}.{key}." if key else f"{path}.")
    return missing

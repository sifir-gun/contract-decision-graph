"""Chargement et validation de config/decision.yaml. Invalide : ConfigError, arrêt au démarrage."""

import re
from pathlib import Path
from typing import Annotated, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cdg.domain.models import (
    KIND_CATEGORIES,
    REQUIRED_KINDS,
    Decision,
    Domain,
    TransferCategory,
)
from cdg.domain.numeric import rounded

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


class Extraction(_Strict):
    max_attempts: Annotated[int, Field(ge=1)]
    # vérification des absences : termes qui évoquent chaque type de clause
    absence_terms: dict[str, Annotated[list[Term], Field(min_length=1)]]
    # cohérence entre catégorie et citation (J5) : pour chaque type à catégorie, des termes
    # par catégorie, de la plus spécifique à la moins spécifique (l'ordre fait foi)
    category_terms: dict[str, dict[str, Annotated[list[Term], Field(min_length=1)]]]

    @model_validator(mode="after")
    def _un_jeu_de_termes_par_type(self) -> "Extraction":
        kinds = set(self.absence_terms)
        if kinds != set(REQUIRED_KINDS):
            missing = sorted(set(REQUIRED_KINDS) - kinds)
            unknown = sorted(kinds - set(REQUIRED_KINDS))
            raise ValueError(
                f"absence_terms : types manquants {missing}, types inconnus {unknown}"
            )
        for kind, terms in self.absence_terms.items():
            if len({t.casefold() for t in terms}) != len(terms):
                raise ValueError(f"absence_terms : terme répété pour {kind}")
        return self

    @model_validator(mode="after")
    def _termes_par_categorie(self) -> "Extraction":
        kinds = set(self.category_terms)
        if kinds != set(KIND_CATEGORIES):
            missing = sorted(set(KIND_CATEGORIES) - kinds)
            unknown = sorted(kinds - set(KIND_CATEGORIES))
            raise ValueError(
                f"category_terms : types manquants {missing}, types inconnus {unknown}"
            )
        for kind, by_category in self.category_terms.items():
            for category in by_category:
                if category not in KIND_CATEGORIES[kind]:
                    raise ValueError(
                        f"category_terms : catégorie inconnue pour {kind} : {category}"
                    )
            terms = [t.casefold() for ts in by_category.values() for t in ts]
            if len(set(terms)) != len(terms):
                raise ValueError(f"category_terms : terme répété pour {kind}")
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
    # vector : vecteurs seuls ; hybrid : plein texte français (accents ignorés) et
    # vecteurs, fusionnés par rangs réciproques (RRF)
    mode: Literal["vector", "hybrid"] = "vector"
    rrf_k: Annotated[int, Field(gt=0)] = 60  # constante de la fusion (Cormack, 2009)
    candidates: Annotated[int, Field(gt=0)] = (
        20  # extraits par liste avant fusion (≥ k)
    )
    # normalisation de ts_rank par la longueur : 0 aucune, 1 par 1 + log(longueur),
    # 2 par la longueur
    text_normalization: Literal[0, 1, 2] = 1


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


class DecisionConfig(_Strict):
    weights: Weights
    thresholds: Thresholds
    min_margin: Annotated[float, Field(ge=0.0, lt=1.0)]
    conflict_gap: Annotated[float, Field(gt=0.0, le=1.0)]
    budget: Budget
    extraction: Extraction
    rules: RulesConfig
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
    """Champs du modèle, à toute profondeur, que le fichier ne règle pas lui-même."""
    missing = []
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        if name not in model.model_fields_set:
            missing.append(path)
            continue
        value = getattr(model, name)
        nested = value.items() if isinstance(value, dict) else [("", value)]
        for key, item in nested:
            if isinstance(item, BaseModel):
                missing += _unset(item, f"{path}.{key}." if key else f"{path}.")
    return missing

"""Chargement et validation de config/decision.yaml. Invalide : ConfigError, arrêt au démarrage."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cdg.numeric import rounded
from cdg.state import Decision, Domain, TransferCategory

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "decision.yaml"

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
        total = rounded(self.juridique + self.financier + self.conformite + self.operationnel)
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


class Extraction(_Strict):
    max_attempts: Annotated[int, Field(ge=1)]


class JuridiqueRules(_Strict):
    supplier_cap_min_pct: Quantity
    supplier_cap_score_penalty: Penalty


class FinancierRules(_Strict):
    late_penalties_min_cap_pct: Quantity
    late_penalties_score_penalty: Penalty


class OperationnelRules(_Strict):
    commitment_max_months: Quantity
    commitment_score_penalty: Penalty
    notice_max_months: Quantity
    notice_score_penalty: Penalty


class ConformiteRules(_Strict):
    # garanties nommées acceptées pour un transfert hors UE (RGPD, art. 45 et 46)
    transfer_safeguards: Annotated[list[TransferCategory], Field(min_length=1)]
    unlocated_data_score_penalty: Penalty  # localisation des données non précisée

    @model_validator(mode="after")
    def _garanties(self) -> "ConformiteRules":
        if len(set(self.transfer_safeguards)) != len(self.transfer_safeguards):
            raise ValueError("transfer_safeguards contient un doublon")
        if {"sans_transfert", "aucune_garantie"} & set(self.transfer_safeguards):
            raise ValueError("sans_transfert et aucune_garantie ne sont pas des garanties")
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


class EmbeddingConfig(_Strict):
    model: Annotated[str, Field(min_length=1)]
    dimension: Annotated[int, Field(gt=0)]  # = colonne rag_chunks.embedding, contrôlé par setup-db
    query_prefix: str  # préfixes exigés par la famille e5
    passage_prefix: str


class CorpusConfig(_Strict):
    chunk_max_words: Annotated[int, Field(gt=0)]  # e5 : 512 tokens au plus


Tier = Literal["main", "light"]
ModelId = Annotated[str, Field(min_length=1)]


class TierModels(_Strict):
    main: ModelId  # extraction
    light: ModelId  # juge CRAG, réécriture de requête

    @model_validator(mode="after")
    def _identifiants_figes(self) -> "TierModels":
        # un alias mouvant changerait de modèle sans changer la config : rejeu faussé
        for model in (self.main, self.light):
            if "latest" in model:
                raise ValueError(f"alias mouvant refusé : {model} (identifiant figé attendu)")
        return self


class LLMConfig(_Strict):
    provider: Literal["mistral", "anthropic"]
    temperature: Annotated[float, Field(ge=0.0, le=1.0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    timeout_seconds: Annotated[int, Field(gt=0)]
    models: dict[Literal["mistral", "anthropic"], TierModels]

    @model_validator(mode="after")
    def _modeles_du_fournisseur(self) -> "LLMConfig":
        if self.provider not in self.models:
            raise ValueError(f"aucun modèle configuré pour le fournisseur {self.provider}")
        return self

    def model(self, tier: Tier, provider: str | None = None) -> str:
        """Modèle d'un niveau, pour le fournisseur configuré ou celui indiqué."""
        return getattr(self.models[provider or self.provider], tier)


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

    def weight(self, domain: Domain) -> float:
        return getattr(self.weights, domain)


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
        return DecisionConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"configuration invalide dans {path} :\n{exc}") from exc

"""Chargement et validation de config/decision.yaml. Invalide : ConfigError, arrêt au démarrage."""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cdg.numeric import rounded
from cdg.state import Decision, Domain

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


class RulesConfig(_Strict):
    juridique: JuridiqueRules
    financier: FinancierRules
    operationnel: OperationnelRules


class HumanPolicy(_Strict):
    allowed_decisions: Annotated[list[Decision], Field(min_length=1)]
    allow_block_override: bool
    hard_block_review: bool   # vrai : un blocage dur passe en revue humaine

    @model_validator(mode="after")
    def _decisions_coherentes(self) -> "HumanPolicy":
        if len(set(self.allowed_decisions)) != len(self.allowed_decisions):
            raise ValueError("allowed_decisions contient un doublon")
        if "ESCALADE" in self.allowed_decisions:
            raise ValueError("ESCALADE interdite : l'humain doit trancher")
        if "NO_GO" not in self.allowed_decisions:
            raise ValueError("NO_GO doit rester possible pour l'humain")
        return self


class DecisionConfig(_Strict):
    weights: Weights
    thresholds: Thresholds
    min_margin: Annotated[float, Field(ge=0.0, lt=1.0)]
    conflict_gap: Annotated[float, Field(gt=0.0, le=1.0)]
    budget: Budget
    extraction: Extraction
    rules: RulesConfig
    human_policy: HumanPolicy

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

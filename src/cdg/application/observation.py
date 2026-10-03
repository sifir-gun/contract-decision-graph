"""Observabilité (ADR 008) : réglages de l'export des traces et tarifs des modèles.

Les réglages vivent dans `config/tarifs.yaml`, hors de `config/decision.yaml` : ce ne sont
pas des réglages de l'analyse, et ils changeraient l'empreinte de ses décisions (les séries
réelles, `tests/serie.py`, gardaient déjà leurs tarifs à part). Ils sont validés par un
modèle Pydantic au démarrage : invalides, rien ne démarre.
"""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cdg.domain.config import LLMConfig

DEFAULT_TARIFS_PATH = Path(__file__).resolve().parents[3] / "config" / "tarifs.yaml"


class ObservabilityConfigError(Exception):
    """`config/tarifs.yaml` absent, illisible ou invalide : le programme s'arrête."""


class UnpricedModel(ValueError):
    """Modèle sans tarif : son coût ne se calcule pas."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tarif(_Strict):
    entree_usd_par_mtoken: Annotated[float, Field(ge=0)]
    sortie_usd_par_mtoken: Annotated[float, Field(ge=0)]
    source: Annotated[str, Field(min_length=1)]
    releve_le: date


class ExportConfig(_Strict):
    delai_export_s: Annotated[float, Field(gt=0)]
    delai_lot_ms: Annotated[int, Field(gt=0)]
    file_max: Annotated[int, Field(gt=0)]
    delai_fermeture_s: Annotated[float, Field(gt=0)]


class ObservabilityConfig(_Strict):
    tarifs: dict[str, Tarif]
    export: ExportConfig


def load_observability_config(
    path: Path | str = DEFAULT_TARIFS_PATH,
) -> ObservabilityConfig:
    path = Path(path)
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ObservabilityConfigError(f"réglages introuvables : {path}") from exc
    except yaml.YAMLError as exc:
        raise ObservabilityConfigError(f"YAML illisible dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ObservabilityConfigError(f"{path} doit contenir un dictionnaire YAML")
    try:
        return ObservabilityConfig.model_validate(data)
    except ValidationError as exc:
        raise ObservabilityConfigError(
            f"réglages invalides dans {path} :\n{exc}"
        ) from exc


def missing_prices(config: ObservabilityConfig, llm: LLMConfig) -> list[str]:
    """Modèles de la configuration, pour chaque fournisseur, sans tarif."""
    models = {m for tiers in llm.models.values() for m in (tiers.main, tiers.light)}
    return sorted(models - set(config.tarifs))


def cost_usd(
    model: str, tokens_in: int, tokens_out: int, tarifs: Mapping[str, Tarif]
) -> float:
    """Coût d'un appel, en dollars, aux tarifs relevés ; non arrondi : l'arrondi unique
    du projet (`domain/numeric.py`) s'applique à l'émission d'une trace, et au rapport
    d'une série, jamais à chaque appel, qui fausserait les totaux."""
    tarif = tarifs.get(model)
    if tarif is None:
        raise UnpricedModel(
            f"tarif inconnu pour {model} : l'ajouter à config/tarifs.yaml, avec sa source"
        )
    entree = tokens_in * tarif.entree_usd_par_mtoken
    sortie = tokens_out * tarif.sortie_usd_par_mtoken
    return (entree + sortie) / 1_000_000

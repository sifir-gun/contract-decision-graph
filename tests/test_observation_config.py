"""Réglages de l'observabilité (ADR 008) : tarifs des modèles et export des traces, dans
`config/tarifs.yaml`, hors de `config/decision.yaml` (ce ne sont pas des réglages de
l'analyse : ils changeraient l'empreinte de ses décisions). Validés au démarrage."""

from datetime import date

import pytest
import yaml

from cdg.application import observation
from cdg.application.observation import (
    ObservabilityConfigError,
    UnpricedModel,
    cost_usd,
    load_observability_config,
    missing_prices,
)
from cdg.domain.config import load_config

CONFIG = load_observability_config()


def write(tmp_path, data) -> object:
    path = tmp_path / "tarifs.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def valid() -> dict:
    return yaml.safe_load(observation.DEFAULT_TARIFS_PATH.read_text(encoding="utf-8"))


def test_tarifs_charges_et_valides():
    tarif = CONFIG.tarifs["mistral-small-2603"]
    assert (tarif.entree_usd_par_mtoken, tarif.sortie_usd_par_mtoken) == (0.15, 0.60)
    assert tarif.releve_le == date(2026, 9, 26) and tarif.source.startswith("https://")
    sonnet = CONFIG.tarifs["claude-sonnet-5"]
    assert (sonnet.entree_usd_par_mtoken, sonnet.sortie_usd_par_mtoken) == (2.0, 10.0)
    assert CONFIG.export.delai_fermeture_s == 2.0


def test_chaque_modele_de_la_configuration_a_son_tarif():
    assert missing_prices(CONFIG, load_config().llm) == []


def test_modele_sans_tarif_signale(tmp_path):
    data = valid()
    del data["tarifs"]["claude-haiku-4-5-20251001"]
    config = load_observability_config(write(tmp_path, data))
    assert missing_prices(config, load_config().llm) == ["claude-haiku-4-5-20251001"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.pop("export"),
        lambda d: d["export"].pop("delai_fermeture_s"),
        lambda d: d["export"].update(inconnu=1),
        lambda d: d["tarifs"]["mistral-small-2603"].pop("source"),
        lambda d: d["tarifs"]["mistral-small-2603"].update(entree_usd_par_mtoken=-1),
        lambda d: d["export"].update(delai_export_s=0),
    ],
)
def test_reglage_manquant_inconnu_ou_invalide_refuse(tmp_path, change):
    data = valid()
    change(data)
    with pytest.raises(ObservabilityConfigError, match="tarifs.yaml"):
        load_observability_config(write(tmp_path, data))


@pytest.mark.parametrize(
    ("text", "message"),
    [("tarifs: [", "YAML illisible"), ("- liste", "dictionnaire YAML")],
)
def test_fichier_illisible_ou_mal_forme_refuse(tmp_path, text, message):
    path = tmp_path / "tarifs.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ObservabilityConfigError, match=message):
        load_observability_config(path)


def test_fichier_absent_refuse(tmp_path):
    with pytest.raises(ObservabilityConfigError, match="introuvable"):
        load_observability_config(tmp_path / "absent.yaml")


def test_cout_calcule_sans_arrondi_a_chaque_appel():
    assert cost_usd("mistral-small-2603", 1_000_000, 1_000_000, CONFIG.tarifs) == 0.75
    # 151,5 millionièmes : l'arrondi unique se fait à l'émission, pas ici
    assert cost_usd("ministral-8b-2512", 1_000, 10, CONFIG.tarifs) == pytest.approx(
        0.0001515, abs=1e-12
    )


def test_modele_sans_tarif_nomme():
    with pytest.raises(UnpricedModel, match="tarif inconnu pour modele-inconnu"):
        cost_usd("modele-inconnu", 1, 1, CONFIG.tarifs)

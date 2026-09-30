"""Changement de configuration (phase Kubernetes, point 4). Un changement de
`decision.yaml` change l'empreinte de configuration ; un contrat en attente analysé sous
une autre empreinte ne peut plus être repris (`resume` le refuse). `config-check` les
liste et échoue s'il y en a : c'est la tâche qui bloque une mise à jour du chart (PR C).
L'administration de l'interface montre la même liste, avec la procédure."""

import json

import yaml
from doubles import ACTEUR_ANALYSTE
from test_service import PENDING_TEXT, make_service
from web_helpers import BASE_URL

from cdg import cli
from cdg.adapters.langgraph.engine import memory_opener
from cdg.domain import audit
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config

CONFIG = load_config()


def other_config() -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["min_margin"] = 0.06
    return DecisionConfig.model_validate(data)


def old_and_new():
    """Deux services sur le même checkpointer : l'ancienne configuration, la nouvelle."""
    shared = memory_opener(CONFIG)
    old = make_service(config=other_config(), opener=shared)
    new = make_service(config=CONFIG, opener=shared)
    return old, new


def test_liste_les_contrats_en_attente_sous_une_autre_configuration():
    from doubles import CONTRACT_TEXT

    old, new = old_and_new()
    old.analyse(PENDING_TEXT, contract_id="c-ancienne", actor=ACTEUR_ANALYSTE)
    old.analyse(
        CONTRACT_TEXT, contract_id="c-finie", actor=ACTEUR_ANALYSTE
    )  # terminé : rien à trancher
    new.analyse(
        PENDING_TEXT, contract_id="c-courante", actor=ACTEUR_ANALYSTE
    )  # même configuration
    check = new.config_check()
    assert check["configuration"] == audit.config_hash(CONFIG)
    assert check["a_trancher"] == [
        {
            "thread_id": "c-ancienne",
            "config_hash": audit.config_hash(other_config()),
            "analysis_date": check["a_trancher"][0]["analysis_date"],
        }
    ]


def test_commande_reussit_sans_contrat_a_trancher(monkeypatch, capsys):
    _, new = old_and_new()
    monkeypatch.setattr(cli, "build_service", lambda config: new)
    assert cli.main(["config-check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"configuration": audit.config_hash(CONFIG), "a_trancher": []}


def test_commande_echoue_avec_les_contrats_a_trancher(monkeypatch, capsys):
    old, new = old_and_new()
    old.analyse(PENDING_TEXT, contract_id="c-ancienne", actor=ACTEUR_ANALYSTE)
    monkeypatch.setattr(cli, "build_service", lambda config: new)
    assert cli.main(["config-check"]) == 1
    err = json.loads(capsys.readouterr().err)
    assert err["erreur"] == "ConfigChangeBlocked"
    assert "c-ancienne" in err["detail"] and "expire" in err["detail"]
    assert [c["thread_id"] for c in err["a_trancher"]] == ["c-ancienne"]


def test_administration_montre_les_contrats_a_trancher():
    from fastapi.testclient import TestClient

    from cdg.adapters.web.app import create_app

    old, new = old_and_new()
    old.analyse(PENDING_TEXT, contract_id="c-ancienne", actor=ACTEUR_ANALYSTE)
    page = TestClient(create_app(new), base_url=BASE_URL).get("/administration")
    assert page.status_code == 200
    assert 'href="/contrats/c-ancienne"' in page.text
    assert "autre configuration" in page.text


def test_administration_sans_contrat_a_trancher():
    from fastapi.testclient import TestClient

    from cdg.adapters.web.app import create_app

    _, new = old_and_new()
    page = TestClient(create_app(new), base_url=BASE_URL).get("/administration")
    assert "Aucun contrat en attente sous une autre configuration" in page.text

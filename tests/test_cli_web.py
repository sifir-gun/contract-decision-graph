"""Commande `web` de la CLI : lance l'interface sur 127.0.0.1 par défaut ; toute autre
adresse exige une option explicite et affiche un avertissement. Le serveur n'est pas
démarré : `server.serve` est remplacé, l'application et l'adresse sont relevées."""

import json

import pytest

from cdg import cli
from cdg.adapters.web import server


@pytest.fixture
def served(monkeypatch):
    seen = {}

    def serve(app, host, port, *, log_config):
        seen.update(app=app, host=host, port=port, log_config=log_config)

    monkeypatch.setattr(server, "serve", serve)
    return seen


def test_ecoute_locale_par_defaut(served, capsys):
    assert cli.main(["web"]) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 8000)
    out, err = capsys.readouterr()
    assert json.loads(out) == {"web": "arrêtée"}
    assert "http://127.0.0.1:8000" in err and "AVERTISSEMENT" not in err


def test_hote_et_port_en_option(served, capsys):
    assert cli.main(["web", "--host", "::1", "--port", "8123"]) == 0
    assert (served["host"], served["port"]) == ("::1", 8123)
    assert "http://[::1]:8123" in capsys.readouterr().err


def test_ecoute_non_locale_refusee_sans_option(served, capsys):
    assert cli.main(["web", "--host", "0.0.0.0"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "WebConfigError"
    assert "authentification" in error["detail"]
    assert served == {}


def test_ecoute_non_locale_avec_option_et_avertissement(served, capsys):
    argv = ["web", "--host", "0.0.0.0", "--ecoute-non-locale"]
    assert cli.main(argv) == 0
    assert served["host"] == "0.0.0.0"
    assert "AVERTISSEMENT" in capsys.readouterr().err

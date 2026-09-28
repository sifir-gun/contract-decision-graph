"""Journaux structurés (phase Kubernetes, point 5) : en JSON sur la sortie standard, sans
jamais le texte d'un contrat ni un secret ; en texte, sur le poste, par défaut.

Une exception n'y laisse que son type et les lignes de code traversées, jamais son
message, qui pourrait citer une donnée reçue ; le journal d'accès, la méthode, le chemin
et le code, sans l'adresse du client."""

import json
import logging
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn
from doubles import CONTRACT_TEXT
from web_helpers import memory_service

from cdg import cli
from cdg.adapters import journaux
from cdg.adapters.web import server
from cdg.adapters.web.app import create_app

CANARY = "Clause témoin 7f3a-canari : ne doit jamais sortir"


def record(name="cdg.test", msg="message", args=(), level=logging.INFO, exc_info=None):
    return logging.LogRecord(name, level, __file__, 42, msg, args, exc_info)


def exc_info_with(message: str):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


# --- mise en forme ---------------------------------------------------------------------------


def test_json_une_ligne_horodatage_utc_niveau_journal_message():
    line = journaux.JsonFormatter().format(record(msg="démarrage %s", args=("ok",)))
    assert "\n" not in line
    entry = json.loads(line)
    assert entry["niveau"] == "INFO" and entry["journal"] == "cdg.test"
    assert entry["message"] == "démarrage ok"
    assert entry["horodatage"].endswith("+00:00")


def test_json_acces_methode_chemin_code_sans_adresse_du_client():
    access = record(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("10.42.0.7:51234", "POST", "/analyse", "1.1", 303),
    )
    entry = json.loads(journaux.JsonFormatter().format(access))
    assert (entry["methode"], entry["chemin"], entry["statut"]) == (
        "POST",
        "/analyse",
        303,
    )
    assert "10.42.0.7" not in json.dumps(entry)


@pytest.mark.parametrize("fmt", ["json", "texte"])
def test_exception_type_et_pile_jamais_son_message(fmt):
    formatter = journaux.FORMATTERS[fmt]()
    line = formatter.format(record(level=logging.ERROR, exc_info=exc_info_with(CANARY)))
    assert CANARY not in line
    assert "ValueError" in line and "test_journaux.py" in line


def test_configuration_json_tout_sur_la_sortie_standard():
    config = journaux.config("json")
    handlers = config["handlers"]
    assert {h["stream"] for h in handlers.values()} == {"ext://sys.stdout"}
    assert {h["formatter"] for h in handlers.values()} == {"json"}
    assert set(config["loggers"]) == {
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "cdg",
    }
    assert (
        config["root"]["level"] == "WARNING"
    )  # bibliothèques : avertissements et plus


def test_format_inconnu_refuse():
    with pytest.raises(ValueError, match="format de journal inconnu"):
        journaux.config("xml")


# --- CLI et serveur ----------------------------------------------------------------------------


def test_serveur_recoit_la_configuration_des_journaux():
    config = journaux.config("json")
    main, _ = server.servers(object(), "127.0.0.1", 8000, log_config=config)
    assert main.config.log_config is config


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        ([], None, "texte"),
        ([], "json", "json"),
        (["--journaux", "texte"], "json", "texte"),
        (["--journaux", "json"], None, "json"),
    ],
)
def test_cli_format_des_journaux_option_ou_environnement(
    monkeypatch, argv, env, expected
):
    if env is None:
        monkeypatch.delenv("CDG_JOURNAUX", raising=False)
    else:
        monkeypatch.setenv("CDG_JOURNAUX", env)
    seen = {}
    monkeypatch.setattr(cli.web_server, "serve", lambda *a, **kw: seen.update(kw))
    assert cli.main([*argv, "web", "--demo"]) == 0
    assert seen["log_config"] == journaux.config(expected)


def test_cli_en_json_chaque_ligne_de_sortie_est_du_json(monkeypatch, capsys):
    """Dans un pod, un collecteur lit la sortie ligne à ligne : en json, l'avertissement et
    l'adresse qu'affiche `web`, une fois le port ouvert, sont des entrées du journal, et le
    résultat final tient sur une ligne. En texte, rien ne change au terminal
    (tests/test_cli_web.py)."""
    monkeypatch.setattr(
        cli.web_server, "serve", lambda *a, on_started, **kw: on_started()
    )
    argv = ["--journaux", "json", "web", "--demo", "--host", "0.0.0.0"]
    assert cli.main([*argv, "--ecoute-non-locale"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    lines = [json.loads(line) for line in out.splitlines()]
    assert lines[-1] == {"web": "arrêtée"}
    warning, address = lines[:-1]
    assert (warning["niveau"], address["niveau"]) == ("WARNING", "INFO")
    assert warning["journal"] == address["journal"] == "cdg.cli"
    assert "AVERTISSEMENT" in warning["message"]
    assert "http://0.0.0.0:8000" in address["message"]


# --- de bout en bout : un vrai serveur uvicorn -------------------------------------------------


class Broken:
    """Service dont la liste échoue avec un message qui cite le texte témoin."""

    def __init__(self, service):
        self._service = service

    def __getattr__(self, name):
        return getattr(self._service, name)

    def contracts(self, **_):
        raise RuntimeError(CANARY)


def test_serveur_reel_journaux_json_sans_texte_du_contrat_ni_message(capsys):
    service = memory_service()
    app = create_app(Broken(service))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_config=journaux.config("json"), lifespan="off")
    real = uvicorn.Server(config)
    thread = threading.Thread(target=real.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if real.started:
                break
            time.sleep(0.05)
        with httpx.Client(base_url=base) as web:
            page = web.get("/analyse")
            token = page.text.split('name="csrf" value="')[1].split('"')[0]
            text = f"{CONTRACT_TEXT}\n{CANARY}\n"
            data = {
                "csrf": token,
                "source": "texte",
                "texte": text,
                "identifiant": "c1",
            }
            posted = web.post("/analyse", data=data, headers={"origin": base})
            assert posted.status_code == 303
            assert web.get("/").status_code == 500  # message témoin dans l'exception
    finally:
        real.should_exit = True
        thread.join(10)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    entries = [json.loads(line) for line in lines]  # que du JSON
    assert CANARY not in "\n".join(lines)
    assert {"POST", "GET"} <= {e.get("methode") for e in entries}
    assert any(
        e.get("exception", {}).get("type", "").endswith("RuntimeError") for e in entries
    )

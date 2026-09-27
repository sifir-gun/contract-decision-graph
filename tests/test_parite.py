"""Même moteur, deux portes (décision du 27/09, principe 1) : chaque action de l'interface
web appelle la même méthode du service des contrats que sa commande de la CLI.

Les deux portes tournent sur le même service en mémoire, enveloppé d'un enregistreur des
méthodes appelées ; la CLI le reçoit à la place de `build_service`.
"""

import pytest
from cli_helpers import run_cli
from web_helpers import PENDING_TEXT, analyse, client, csrf, memory_service

from cdg import cli

METHODS = {
    "analyse",
    "decide",
    "contracts",
    "dossier",
    "history",
    "expire",
    "journal",
    "verify",
    "replay",
}
# action de l'interface -> commande de la CLI
WEB_TO_CLI = {
    "POST /analyse": "run",
    "POST /contrats/{id}/decision": "resume",
    "GET /": "list",
    "GET /contrats/{id}": "show",
    "POST /administration/expiration (confirmée)": "expire",
    "GET /journal": "journal",
    "GET /journal/verification": "verify",
    "GET /contrats/{id}/rejeu": "replay",
}


class Recorder:
    """Service des contrats qui note chaque méthode appelée, puis délègue."""

    def __init__(self, service):
        self._service, self.calls = service, []

    def __getattr__(self, name):
        target = getattr(self._service, name)
        if name not in METHODS:
            return target

        def call(*args, **kwargs):
            self.calls.append(name)
            return target(*args, **kwargs)

        return call

    def take(self) -> set[str]:
        taken, self.calls = set(self.calls), []
        return taken


@pytest.fixture
def cli_calls(tmp_path, monkeypatch, capsys) -> dict[str, set[str]]:
    recorder = Recorder(memory_service())
    monkeypatch.setattr(cli, "build_service", lambda config: recorder)
    pending = tmp_path / "c-attente.txt"
    pending.write_text(PENDING_TEXT, encoding="utf-8")
    commands = {
        "run": ["run", str(pending)],
        "show": ["show", "c-attente"],
        "history": ["history", "c-attente"],
        "list": ["list"],
        "resume": [
            "resume",
            "c-attente",
            "--decision",
            "GO",
            "--reviewer",
            "Camille",
            "--reason",
            "revu",
        ],
        "journal": ["journal"],
        "verify": ["verify"],
        "replay": ["replay", "c-attente"],
        "expire": ["expire", "--older-than", "24h"],
    }
    calls = {}
    for name, argv in commands.items():
        code, out = run_cli(capsys, *argv)
        assert code == 0, (name, out)
        calls[name] = recorder.take()
    return calls


@pytest.fixture
def web_calls() -> dict[str, set[str]]:
    recorder = Recorder(memory_service())
    web = client(recorder)
    calls = {}
    thread = analyse(web, PENDING_TEXT, identifiant="c-attente")
    calls["POST /analyse"] = recorder.take()
    web.get(f"/contrats/{thread}")
    calls["GET /contrats/{id}"] = recorder.take()
    web.get("/")
    calls["GET /"] = recorder.take()
    token = csrf(web)
    recorder.take()
    web.post(
        f"/contrats/{thread}/decision",
        data={"csrf": token, "decision": "GO", "relecteur": "Camille", "motif": "revu"},
    )
    calls["POST /contrats/{id}/decision"] = recorder.take()
    web.get(f"/contrats/{thread}/rejeu")
    calls["GET /contrats/{id}/rejeu"] = recorder.take()
    web.get("/journal")
    calls["GET /journal"] = recorder.take()
    web.get("/journal/verification")
    calls["GET /journal/verification"] = recorder.take()
    web.post(
        "/administration/expiration",
        data={"csrf": token, "heures": "24", "confirme": "oui"},
    )
    calls["POST /administration/expiration (confirmée)"] = recorder.take()
    return calls


def test_chaque_commande_passe_par_une_methode_du_service(cli_calls):
    assert {name: len(methods) for name, methods in cli_calls.items()} == dict.fromkeys(
        cli_calls, 1
    )
    assert set().union(*cli_calls.values()) == METHODS


@pytest.mark.parametrize("action", WEB_TO_CLI)
def test_action_de_l_interface_et_sa_commande_appellent_la_meme_methode(
    action, web_calls, cli_calls
):
    assert web_calls[action] == cli_calls[WEB_TO_CLI[action]]

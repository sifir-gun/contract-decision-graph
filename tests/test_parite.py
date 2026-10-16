"""Même moteur, trois portes (décision du 27/09, principe 1 ; serveur MCP, ADR 007) :
chaque action de l'interface web et chaque outil du serveur MCP appellent la même méthode
du service des contrats que leur commande de la CLI.

Les portes tournent sur le même service en mémoire, enveloppé d'un enregistreur des
méthodes appelées ; la CLI le reçoit à la place de `build_service`. Le serveur MCP n'a
aucun outil de décision : aucun de ses outils n'appelle decide, expire ni relaunch.
"""

import pytest
from cli_helpers import run_cli
from mcp_helpers import MCP_ACTOR, call, session
from web_helpers import (
    PENDING_TEXT,
    analyse,
    client,
    csrf,
    memory_service,
    post_analyse,
)

from cdg import cli
from cdg.adapters.mcp.server import create_server

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
    "config_check",
    "relaunch",
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
    "GET /administration": "config-check",
    "POST /contrats/{id}/relance": "relaunch",
}
# outil du serveur MCP -> commande de la CLI ; aucun outil de décision
MCP_TO_CLI = {
    "analyser_contrat": "run",
    "lister_contrats": "list",
    "consulter_dossier": "show",
    "verifier_journal": "verify",
}
DECISIONS = {"decide", "expire", "relaunch"}


class Recorder:
    """Service des contrats qui note chaque méthode appelée, avec ses arguments nommés,
    puis délègue."""

    def __init__(self, service):
        self._service, self.calls = service, []
        self.keywords: dict[str, list[dict]] = {}  # arguments nommés, par méthode

    def __getattr__(self, name):
        target = getattr(self._service, name)
        if name not in METHODS:
            return target

        def call(*args, **kwargs):
            self.calls.append(name)
            self.keywords.setdefault(name, []).append(kwargs)
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
        "run": [
            "run",
            "--profil",
            "fournisseur",
            str(pending),
            "--operateur",
            "lot-parite",
        ],
        "show": ["show", "c-attente"],
        "history": ["history", "c-attente"],
        "list": ["list"],
        "resume": [
            "resume",
            "c-attente",
            "--decision",
            "GO",
            "--operateur",
            "relecteur-parite",
            "--reason",
            "revu",
        ],
        "journal": ["journal"],
        "verify": ["verify"],
        "replay": ["replay", "c-attente"],
        "expire": ["expire", "--older-than", "24h", "--operateur", "relecteur-parite"],
        "config-check": ["config-check"],
        # aucun contrat escaladé pour changement de configuration : refusée, mais par
        # la même méthode du service
        "relaunch": ["relaunch", "c-attente", "--operateur", "lot-parite"],
    }
    calls = {}
    for name, argv in commands.items():
        code, out = run_cli(capsys, *argv)
        if name == "relaunch":
            assert (code, out["erreur"]) == (1, "ThreadError"), out
        else:
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
    web.get("/administration")
    calls["GET /administration"] = recorder.take()
    web.post(
        "/administration/expiration",
        data={"csrf": token, "heures": "24", "confirme": "oui"},
    )
    calls["POST /administration/expiration (confirmée)"] = recorder.take()
    refused = web.post(f"/contrats/{thread}/relance", data={"csrf": token})
    assert refused.status_code == 409  # aucun contrat escaladé pour ce motif
    calls["POST /contrats/{id}/relance"] = recorder.take()
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


@pytest.fixture
def mcp_calls() -> dict[str, set[str]]:
    recorder = Recorder(memory_service())
    server = create_server(recorder, actor=MCP_ACTOR, demo=False)
    arguments = {
        "analyser_contrat": {
            "profil": "fournisseur",
            "texte": PENDING_TEXT,
            "identifiant": "c-attente",
        },
        "lister_contrats": {},
        "consulter_dossier": {"thread_id": "c-attente"},
        "verifier_journal": {},
    }

    async def steps(client):
        calls = {}
        for name in MCP_TO_CLI:  # l'analyse d'abord : le dossier existe ensuite
            result = await client.call_tool(name, arguments[name])
            assert not result.is_error, (name, result.content)
            calls[name] = recorder.take()
        return calls

    return session(server, steps)


@pytest.mark.parametrize("tool", MCP_TO_CLI)
def test_outil_mcp_et_sa_commande_appellent_la_meme_methode(tool, mcp_calls, cli_calls):
    assert mcp_calls[tool] == cli_calls[MCP_TO_CLI[tool]]
    assert len(mcp_calls[tool]) == 1


def test_aucun_outil_mcp_n_appelle_une_methode_de_decision(mcp_calls):
    assert set().union(*mcp_calls.values()) & DECISIONS == set()


# --- profil : choisi aux trois portes, jamais déduit ---------------------------------


@pytest.fixture
def portes(tmp_path, monkeypatch):
    """Les trois portes sur un même service enregistré. Chacune rend si l'analyse a été
    acceptée ; `profil=None` omet le champ (ancien formulaire, ancien assistant, commande
    d'avant les profils)."""
    recorder = Recorder(memory_service())
    monkeypatch.setattr(cli, "build_service", lambda config: recorder)
    contract = tmp_path / "c-cli.txt"
    contract.write_text(PENDING_TEXT, encoding="utf-8")
    web = client(recorder)
    server = create_server(recorder, actor=MCP_ACTOR, demo=False)

    def by_cli(profil) -> bool:
        argv = ["run", str(contract), "--operateur", "lot-parite"]
        if profil is not None:
            argv += ["--profil", profil]
        try:
            return cli.main(argv) == 0
        except SystemExit as refused:  # argparse : profil absent ou hors des choix
            assert refused.code == 2
            return False

    def by_web(profil) -> bool:
        response = post_analyse(web, PENDING_TEXT, profil=profil, identifiant="c-web")
        assert response.status_code in (303, 400), response.text
        return response.status_code == 303

    def by_mcp(profil) -> bool:
        arguments = {"texte": PENDING_TEXT, "identifiant": "c-mcp"}
        if profil is not None:
            arguments["profil"] = profil
        return not call(server, "analyser_contrat", arguments).is_error

    return recorder, {"cli": by_cli, "web": by_web, "mcp": by_mcp}


def test_meme_profil_aux_trois_portes(portes):
    recorder, doors = portes
    for door in doors.values():
        assert door("fournisseur")
    assert [k["profile"] for k in recorder.keywords["analyse"]] == ["fournisseur"] * 3


@pytest.mark.parametrize("profil", [None, "", "Fournisseur", "sous_traitance"])
@pytest.mark.parametrize("door", ["cli", "web", "mcp"])
def test_profil_absent_refuse_aux_trois_portes(portes, door, profil):
    """Profil absent, vide ou inconnu : refusé à chaque porte, avant tout appel au
    service : rien n'est analysé ni scellé."""
    recorder, doors = portes
    assert not doors[door](profil)
    assert recorder.take() == set()
    assert recorder.contracts() == []

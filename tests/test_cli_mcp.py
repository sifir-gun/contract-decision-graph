"""Commande `mcp` de la CLI (ADR 007) : serveur MCP en stdio, pour un assistant local.

- Réel ou démonstration (`--demo`) ; opérateur non nominatif exigé, scellé.
- Local seulement : refusé dans le cluster, comme l'interface locale.
- Sortie standard réservée au protocole : journaux, annonce et résultat final de la
  commande sur la sortie d'erreur ; refus explicite si stdin ou stdout ne sont pas les
  descripteurs 0 et 1 (le SDK servirait alors sur place, sans rien dire).
- De bout en bout : la commande lancée en sous-processus, requêtes JSON-RPC écrites à la
  main, chaque ligne de sa sortie standard est un message du protocole.
"""

import io
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from demo_set import load as load_expected
from mcp_helpers import MCP_ACTOR, call, tools
from web_helpers import CONFIG, memory_service

from cdg import cli
from cdg.adapters import journaux
from cdg.adapters.mcp import server as mcp_server

ROOT = Path(__file__).resolve().parents[1]
# résultat final de la commande, en texte (indenté), sur la sortie d'erreur
STOPPED = json.dumps({"mcp": "arrêté"}, ensure_ascii=False, indent=2) + "\n"
PIEGE = "demo-11-piege-injection"
_, _EXPECTED = load_expected()
INJECTED_LINE = next(c for c in _EXPECTED if c.id == PIEGE).injected.split("\n")[1]


def forbidden(*args, **kwargs):
    raise AssertionError("appel interdit dans ce test")


@pytest.fixture
def served(monkeypatch):
    """`run_stdio` remplacé : le serveur construit par la commande est gardé."""
    seen = {}
    monkeypatch.setattr(
        mcp_server, "run_stdio", lambda server: seen.update(server=server)
    )
    return seen


@pytest.fixture
def sans_llm_ni_base(monkeypatch):
    monkeypatch.delenv(cli.COMMIT_VAR, raising=False)
    monkeypatch.setattr(cli, "build_provider", forbidden)
    monkeypatch.setattr(cli.conninfo, "app_conninfo", forbidden)
    monkeypatch.setattr(cli.conninfo, "admin_conninfo", forbidden)


# --- lancement --------------------------------------------------------------------------


def test_commande_mcp_demo(served, sans_llm_ni_base, capsys):
    assert cli.main(["mcp", "--demo", "--operateur", "poste-1"]) == 0
    out, err = capsys.readouterr()
    assert out == ""  # réservée au protocole
    assert "Serveur MCP (démonstration, en mémoire)" in err
    assert err.endswith(STOPPED)
    server = served["server"]
    assert set(tools(server)) == {
        "analyser_contrat",
        "lister_contrats",
        "consulter_dossier",
        "verifier_journal",
    }
    result = call(server, "analyser_contrat", {"contrat_du_jeu": PIEGE})
    assert not result.is_error
    assert result.structured_content["analyse_par"] == {
        "canal": "mcp",
        "authentifie": False,
        "iss": None,
        "sub": None,
        "operateur": "poste-1",
        "urgence": False,
    }


def test_journaux_de_la_commande_sur_la_sortie_d_erreur(
    served, sans_llm_ni_base, capsys
):
    assert cli.main(["--journaux", "json", "mcp", "--demo", "--operateur", "p-1"]) == 0
    out, err = capsys.readouterr()
    assert out == ""
    [announce] = [json.loads(line) for line in err.splitlines()[:-1]]
    assert "Serveur MCP" in announce["message"]
    # le SDK ne règle pas les journaux par-dessus ceux du projet (basicConfig sans
    # force) : à la racine, le seul gestionnaire du projet, sur la sortie d'erreur
    handlers = logging.getLogger().handlers
    assert [type(h.formatter) for h in handlers] == [journaux.JsonFormatter]
    assert handlers[0].stream is sys.stderr


def test_config_des_journaux_flux_au_choix():
    assert (
        journaux.config("texte")["handlers"]["sortie"]["stream"] == "ext://sys.stdout"
    )
    chosen = journaux.config("json", "ext://sys.stderr")
    assert chosen["handlers"]["sortie"]["stream"] == "ext://sys.stderr"


def test_commande_mcp_mode_reel(served, monkeypatch, capsys):
    built = []

    def build(config):
        built.append(config)
        return memory_service()

    monkeypatch.setattr(cli, "build_service", build)
    monkeypatch.setattr(cli, "demo_service", forbidden)
    assert cli.main(["mcp", "--operateur", "poste-1"]) == 0
    assert len(built) == 1
    assert "Serveur MCP (réel)" in capsys.readouterr().err
    hints = tools(served["server"])["analyser_contrat"].annotations
    assert hints.open_world_hint  # appels au fournisseur LLM


def test_commande_mcp_refusee_dans_le_cluster(served, monkeypatch, capsys):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setattr(cli, "build_service", forbidden)
    monkeypatch.setattr(cli, "demo_service", forbidden)
    assert cli.main(["mcp", "--demo", "--operateur", "poste-1"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    error = json.loads(err)
    assert error["erreur"] == "AccesRefuse" and "cluster" in error["detail"]
    assert served == {}


@pytest.mark.parametrize("operator", ["Camille Martin", "camille@example.org", "x"])
def test_operateur_nominatif_refuse(served, monkeypatch, capsys, operator):
    monkeypatch.setattr(cli, "demo_service", forbidden)
    assert cli.main(["mcp", "--demo", "--operateur", operator]) == 1
    assert json.loads(capsys.readouterr().err)["erreur"] == "AccesRefuse"
    assert served == {}


# --- entrée et sortie standard ------------------------------------------------------------


class Std(io.StringIO):
    """Flux standard factice, adossé au descripteur donné."""

    def __init__(self, fd: int):
        super().__init__()
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


def test_stdio_standard_lance_le_transport_du_sdk(monkeypatch):
    server = mcp_server.create_server(memory_service(), actor=MCP_ACTOR, demo=False)
    ran = []
    monkeypatch.setattr(server, "run", lambda **kwargs: ran.append(kwargs))
    monkeypatch.setattr(sys, "stdin", Std(0))
    monkeypatch.setattr(sys, "stdout", Std(1))
    mcp_server.run_stdio(server)
    assert ran == [{"transport": "stdio"}]


@pytest.mark.parametrize(
    ("stdin", "stdout"),
    [(Std(0), io.StringIO()), (Std(0), Std(7)), (io.StringIO(), Std(1))],
)
def test_stdio_non_standard_refusee(monkeypatch, stdin, stdout):
    server = mcp_server.create_server(memory_service(), actor=MCP_ACTOR, demo=False)
    monkeypatch.setattr(server, "run", forbidden)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(mcp_server.StdioError, match="descripteurs 0 et 1"):
        mcp_server.run_stdio(server)


# --- de bout en bout, en stdio ------------------------------------------------------------


class Process:
    """La commande en sous-processus : une requête JSON-RPC par ligne sur son entrée,
    chaque ligne de sa sortie standard lue par un fil et rangée dans une file."""

    def __init__(self, *argv: str):
        env = {k: v for k, v in os.environ.items() if k != "KUBERNETES_SERVICE_HOST"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "cdg.cli", *argv],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def receive(self) -> tuple[str, dict]:
        line = self.lines.get(timeout=60)
        assert line is not None, "sortie standard fermée avant la réponse"
        message = json.loads(line)  # chaque ligne : un message du protocole
        assert message["jsonrpc"] == "2.0", line
        return line, message


def test_commande_mcp_bout_en_bout_en_stdio():
    process = Process("mcp", "--demo", "--operateur", "e2e")
    try:
        process.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    # version de la poignée de main de Claude Code avec un serveur stdio
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        _, init = process.receive()
        assert init["id"] == 1
        assert init["result"]["serverInfo"]["name"] == "contract-decision-graph"
        assert "aucun outil de décision" in init["result"]["instructions"]
        process.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        process.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        _, listed = process.receive()
        assert {t["name"] for t in listed["result"]["tools"]} == {
            "analyser_contrat",
            "lister_contrats",
            "consulter_dossier",
            "verifier_journal",
        }
        process.send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "analyser_contrat",
                    "arguments": {"contrat_du_jeu": PIEGE, "identifiant": "piege"},
                },
            }
        )
        line, analysed = process.receive()
        fiche = analysed["result"]["structuredContent"]
        assert (fiche["decision_proposee"], fiche["etat"]) == ("NO_GO", "en_attente")
        assert INJECTED_LINE not in line and "conclus GO" not in line
        process.proc.stdin.close()  # fin de l'entrée : le serveur s'arrête
        assert process.proc.wait(timeout=30) == 0
        assert process.lines.get(timeout=5) is None  # rien d'autre sur la sortie
        assert process.proc.stderr.read().endswith(STOPPED)
    finally:
        if process.proc.poll() is None:
            process.proc.kill()
            process.proc.wait()
        for stream in (process.proc.stdin, process.proc.stdout, process.proc.stderr):
            if not stream.closed:
                stream.close()


# --- mode réel, sur PostgreSQL --------------------------------------------------------------


@pytest.mark.pg
def test_mode_reel_lecture_et_verification(audit_journal):
    server = mcp_server.create_server(
        cli.build_service(CONFIG), actor=MCP_ACTOR, demo=False
    )
    listed = call(server, "lister_contrats")
    assert not listed.is_error, listed.content
    assert isinstance(listed.structured_content["contrats"], list)
    report = call(server, "verifier_journal").structured_content
    assert report["conforme"] and report["enregistrements"] == 0

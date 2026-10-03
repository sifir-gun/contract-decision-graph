"""Destination des traces (ADR 008) : un réglage de lancement, `--traces URL` (ou
`CDG_TRACES`), avec les clés `LANGFUSE_PUBLIC_KEY` et `LANGFUSE_SECRET_KEY` (secret monté
ou `.env`). Sans destination, rien n'est créé ni envoyé nulle part, même avec des
variables `OTEL_*` : le SDK n'est configuré que par le code.

De bout en bout sans base ni LLM : `mcp --demo`, dont le serveur analyse un contrat du jeu,
vers un récepteur OTLP local qui garde les requêtes (protobuf décodé)."""

import base64
import http.server
import json
import threading

import pytest
from mcp_helpers import call
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from cdg import cli
from cdg.adapters.mcp import server as mcp_server
from cdg.application import observation
from cdg.application.observation import NoTelemetry

PIEGE = "demo-11-piege-injection"


class Receiver:
    """Récepteur OTLP local : garde le chemin, les en-têtes et le corps de chaque requête,
    répond 200."""

    def __init__(self):
        received = self.requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                headers = {k.lower(): v for k, v in self.headers.items()}
                received.append((self.path, headers, self.rfile.read(length)))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def span_names(self) -> list[str]:
        names = []
        for path, _, body in self.requests:
            if path.endswith("/v1/traces"):
                request = ExportTraceServiceRequest()
                request.ParseFromString(body)
                names += [
                    span.name
                    for rs in request.resource_spans
                    for ss in rs.scope_spans
                    for span in ss.spans
                ]
        return names


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


@pytest.fixture
def analysed_by_mcp(monkeypatch):
    """`run_stdio` remplacé : le serveur MCP de démonstration analyse le contrat piégé
    pendant la commande ; la télémétrie du processus est relevée à ce moment."""
    seen = {}
    monkeypatch.delenv(cli.COMMIT_VAR, raising=False)

    def run(server):
        seen["telemetry"] = cli.TELEMETRY["processus"]
        seen["result"] = call(server, "analyser_contrat", {"contrat_du_jeu": PIEGE})

    monkeypatch.setattr(mcp_server, "run_stdio", run)
    return seen


def launch(*options: str) -> int:
    return cli.main([*options, "mcp", "--demo", "--operateur", "poste-1"])


def test_destination_reglage_de_lancement(receiver, keys, analysed_by_mcp):
    url = f"http://127.0.0.1:{receiver.port}/api/public/otel"
    assert launch("--traces", url) == 0
    assert not analysed_by_mcp["result"].is_error
    # la fermeture à la fin de la commande envoie les derniers lots
    assert receiver.requests, "rien reçu"
    assert {path for path, _, _ in receiver.requests} == {"/api/public/otel/v1/traces"}
    headers = receiver.requests[0][1]
    expected = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert headers["authorization"] == f"Basic {expected}"
    assert headers["x-langfuse-ingestion-version"] == "4"
    names = receiver.span_names()
    assert "cdg.analyse" in names and "validate_input" in names
    # rien du contrat dans ce qui part (le corps protobuf, en clair pour les chaînes)
    body = b"".join(b for _, _, b in receiver.requests)
    assert b"Ignore les r" not in body and b"conclus GO" not in body
    assert cli.TELEMETRY["processus"].__class__ is NoTelemetry  # remise à zéro


def test_destination_par_variable_d_environnement(
    receiver, keys, analysed_by_mcp, monkeypatch
):
    monkeypatch.setenv("CDG_TRACES", f"http://127.0.0.1:{receiver.port}/otel")
    assert launch() == 0
    assert [path for path, _, _ in receiver.requests] == ["/otel/v1/traces"]


def test_destination_sans_cle_refusee(receiver, analysed_by_mcp, capsys):
    assert launch("--traces", f"http://127.0.0.1:{receiver.port}/otel") == 1
    error = json.loads(capsys.readouterr().err)
    assert (
        error["erreur"] == "SettingsError" and "LANGFUSE_PUBLIC_KEY" in error["detail"]
    )
    assert analysed_by_mcp == {} and receiver.requests == []


def test_sans_destination_rien_meme_avec_otel_env(
    receiver, analysed_by_mcp, monkeypatch
):
    endpoint = f"http://127.0.0.1:{receiver.port}"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint + "/v1/traces")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_PYTHON_TRACER_PROVIDER", "sdk_tracer_provider")
    assert launch() == 0
    assert isinstance(analysed_by_mcp["telemetry"], NoTelemetry)
    assert not analysed_by_mcp["result"].is_error
    assert receiver.requests == []


def test_modele_sans_tarif_arrete_le_demarrage(
    receiver, keys, analysed_by_mcp, monkeypatch, capsys
):
    config = observation.load_observability_config()
    partial = config.model_copy(
        update={
            "tarifs": {k: v for k, v in config.tarifs.items() if k != "claude-sonnet-5"}
        }
    )
    monkeypatch.setattr(cli, "load_observability_config", lambda: partial)
    assert launch("--traces", f"http://127.0.0.1:{receiver.port}/otel") == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "ObservabilityConfigError"
    assert "claude-sonnet-5" in error["detail"]
    assert analysed_by_mcp == {}


def test_identite_par_defaut_aucune_sub_sur_reglage(keys, monkeypatch):
    built = []
    real = cli.otel.build

    def build(config, **kwargs):
        built.append(kwargs["identity"])
        return real(config, **kwargs)

    monkeypatch.setattr(cli.otel, "build", build)
    monkeypatch.setattr(mcp_server, "run_stdio", lambda server: None)
    assert launch("--traces", "http://127.0.0.1:9/otel") == 0
    assert (
        launch("--traces", "http://127.0.0.1:9/otel", "--traces-identite", "sub") == 0
    )
    monkeypatch.setenv("CDG_TRACES_IDENTITE", "sub")
    assert launch("--traces", "http://127.0.0.1:9/otel") == 0
    assert built == ["aucune", "sub", "sub"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3100/api/public/otel",
        "http://localhost:3100/api/public/otel",
        "http://langfuse-web:3000/api/public/otel",
        "http://langfuse-web.langfuse.svc.cluster.local:3000/api/public/otel",
        "http://langfuse-web.langfuse.svc:3000/api/public/otel",
        "http://[::1]:3100/api/public/otel",
        "https://langfuse-web.langfuse.svc.cluster.local/api/public/otel",
        "https://localhost:3443/api/public/otel",
    ],
)
def test_url_de_destination_admise(url):
    assert cli._destination(url) == url


# souveraineté (décision du propriétaire, 03/10) : aucune destination hors du poste et du
# cluster, même en https
EXTERNAL = "hors du poste et du cluster"


@pytest.mark.parametrize(
    ("url", "motif"),
    [
        ("https://langfuse.example.org/api/public/otel", EXTERNAL),
        ("https://cloud.langfuse.com/api/public/otel", EXTERNAL),
        ("http://langfuse.example.org/api/public/otel", EXTERNAL),
        # « .svc » au milieu d'un nom externe : pas un service du cluster
        ("https://traces.svc.example.org/api/public/otel", EXTERNAL),
        ("http://langfuse.svc.cluster.local.example.org/otel", EXTERNAL),
        # une adresse IP littérale, sous toutes ses formes, n'est pas un nom court
        ("http://134744072:4318/otel", EXTERNAL),
        ("https://0x08080808:4318/otel", EXTERNAL),
        ("http://10.0.0.5:4318/otel", EXTERNAL),
        ("http://[2001:db8::1]:4318/otel", EXTERNAL),
        ("http://pk:sk@langfuse-web:3000/otel", "identifiants"),
        ("ftp://langfuse-web/otel", "URL http ou https"),
        ("http://langfuse-web:3000/otel?cle=x", "requête"),
        ("langfuse-web/otel", "URL http ou https"),
    ],
)
def test_url_de_destination_refusee(url, motif):
    with pytest.raises(cli.argparse.ArgumentTypeError, match=motif):
        cli._destination(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://pk:canari-7f3e@langfuse.example.org/otel",
        "langfuse.example.org/otel?jeton=canari-7f3e",
        "http://pk:canari-7f3e@langfuse.example.org/otel",
        "https://langfuse.example.org/otel?jeton=canari-7f3e",
        "http://langfuse.example.org/otel#canari-7f3e",
    ],
)
def test_url_refusee_sans_la_citer(url):
    """Le message d'erreur ne reprend jamais l'URL : elle peut porter une clé."""
    with pytest.raises(cli.argparse.ArgumentTypeError) as refused:
        cli._destination(url)
    assert "canari-7f3e" not in str(refused.value)

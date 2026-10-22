"""Export OTLP hors de tout mandataire de l'environnement (ADR 008). Dans le cluster,
`HTTP_PROXY` et `HTTPS_PROXY` désignent le proxy de sortie, qui ne laisse passer que
Mistral et le fournisseur d'identité, et la destination des traces (Langfuse,
`http://…svc.cluster.local`) n'est pas dans `NO_PROXY` : un export qui les suivrait serait
refusé, traces perdues sans erreur (échec ouvert).

L'exportateur 1.45.0 passe par urllib3, qui les ignore ; le 1.45.1 rétablit requests, qui
les suit (CHANGELOG d'OpenTelemetry Python, #5714) : l'exportateur reste en 1.45.0 quand
l'API passe en 1.45.1 (22/10/2026, `docs/journal.md`). Ce test échoue le jour où une
montée de version fait passer l'export par le mandataire : décision à prendre alors."""

import http.server
import threading

import pytest
from doubles import CODE

from cdg.adapters.otel.telemetry import Destination, build
from cdg.application.observation import load_observability_config
from cdg.domain.authorization import Actor

CLI = Actor(canal="cli", authentifie=False, operateur="astreinte-1")
DONE = {
    "statut": "termine",
    "proposed_decision": "GO",
    "final_decision": "GO",
    "reject_reason": None,
}
PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class Recorder:
    """Serveur HTTP local qui garde le chemin de chaque requête et répond 200 : récepteur
    OTLP, ou mandataire (qui reçoit alors l'adresse complète de la cible)."""

    def __init__(self):
        received = self.paths = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

            def do_CONNECT(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def recepteur():
    recorder = Recorder()
    yield recorder
    recorder.close()


@pytest.fixture
def mandataire(monkeypatch):
    """Toutes les variables de mandataire sur un serveur local, sans exception
    (`NO_PROXY` absente), comme le conteneur de l'application pour Langfuse."""
    recorder = Recorder()
    for name in PROXY_VARIABLES:
        monkeypatch.setenv(name, recorder.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield recorder
    recorder.close()


def test_export_des_traces_et_des_metriques_hors_du_mandataire(recepteur, mandataire):
    telemetry = build(
        load_observability_config(),
        traces=Destination(
            f"{recepteur.url}/api/public/otel", "pk-lf-test", "sk-lf-test"
        ),
        metrics=recepteur.url,
        code=CODE,
    )
    with telemetry.operation("analyse", contract_id="c-1", actor=CLI) as finish:
        finish(DONE)
    # la fermeture envoie le dernier lot de spans et la dernière collecte des métriques
    telemetry.close()
    assert mandataire.paths == []
    assert sorted(recepteur.paths) == ["/api/public/otel/v1/traces", "/v1/metrics"]

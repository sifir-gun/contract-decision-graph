"""Traçage tiers refusé (ADR 008) : les traces ne partent que vers la destination
configurée du projet, jamais chez un tiers.

- LangSmith : langsmith trace si LANGSMITH_TRACING_V2, LANGCHAIN_TRACING_V2,
  LANGSMITH_TRACING ou LANGCHAIN_TRACING vaut « true » (dans cet ordre :
  LANGCHAIN_TRACING_V2=true l'emporte sur le LANGSMITH_TRACING=false de l'image), ou en
  mode OpenTelemetry (LANGSMITH_TRACING_MODE, LANGSMITH_OTEL_*). La CLI refuse de démarrer
  avec l'une de ces variables, et le moteur coupe le traçage lui-même
  (`langsmith.configure(enabled=False)`, qui passe avant l'environnement).
- SDK Mistral : MISTRAL_SDK_TELEMETRY (global, dedicated) tracerait prompts et réponses,
  vers le traceur global ou vers api.mistral.ai : refusée de même, et coupée par
  l'adaptateur dans la configuration du client (le SDK relit la variable à chaque requête).
- FastAPI (depuis 0.142) : trace chaque requête et journalise ses erreurs, messages et
  piles compris, vers les fournisseurs globaux d'OpenTelemetry ; sous
  FASTAPI_OTEL_AUTO_CONFIGURE, les installe lui-même et exporte vers
  OTEL_EXPORTER_OTLP_ENDPOINT. Variable refusée de même ; télémétrie de FastAPI coupée à
  la création de chaque application (interface et sondes de santé).
Le refus nomme la variable, jamais sa valeur. Sans réseau : test_embeddings.py vérifie
qu'une analyse n'ouvre aucune connexion, même sous ces variables.
"""

import json
import threading

import langsmith.utils
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.tracers.context import _tracing_v2_is_enabled
from mistral_factice import serve
from mistralai.client import Mistral
from mistralai.client._hooks.tracing import TracingHook
from mistralai.extra.observability.telemetry import configure_telemetry_for_hook
from opentelemetry import _logs, metrics, trace
from opentelemetry._logs import _internal as _logs_internal
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from web_helpers import client, memory_service

from cdg import cli, settings
from cdg.adapters.llm.mistral import MistralProvider
from cdg.adapters.web import sante
from cdg.application import demo_set
from cdg.application.extraction import LLMExtractor
from cdg.domain import masking
from cdg.domain.config import load_config

REFUSED = {
    "LANGSMITH_TRACING_V2": "true",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGSMITH_TRACING": "true",
    "LANGCHAIN_TRACING": "true",
    "LANGSMITH_OTEL_ENABLED": "true",
    "LANGSMITH_OTEL_ONLY": "true",
    "LANGSMITH_TRACING_MODE": "otel",
    "MISTRAL_SDK_TELEMETRY": "dedicated",
    "FASTAPI_OTEL_AUTO_CONFIGURE": "true",
}


@pytest.fixture(autouse=True)
def _environnement_propre(monkeypatch):
    for name in REFUSED:
        monkeypatch.delenv(name, raising=False)
    langsmith.utils.get_env_var.cache_clear()  # langsmith met l'environnement en cache
    yield
    langsmith.utils.get_env_var.cache_clear()


@pytest.mark.parametrize(("name", "value"), REFUSED.items())
def test_tracage_tiers_refuse_au_demarrage(monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    assert cli.main(["list"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "TracageTiersRefuse"
    assert name in error["detail"]
    assert value not in error["detail"].replace(name, "")


@pytest.mark.parametrize("value", ["true", "True", "1", "local", "global"])
def test_toute_valeur_autre_que_false_refusee(value):
    assert settings.third_party_tracing({"LANGCHAIN_TRACING_V2": value}) == [
        "LANGCHAIN_TRACING_V2"
    ]


def test_valeurs_neutres_admises():
    environ = {
        "LANGSMITH_TRACING": "false",  # comme l'image et .env.example
        "LANGCHAIN_TRACING_V2": "",
        "MISTRAL_SDK_TELEMETRY": "FALSE",
        "LANGSMITH_API_KEY": "cle-fictive",  # une clé seule ne trace rien
    }
    assert settings.third_party_tracing(environ) == []


def test_plusieurs_variables_toutes_nommees_dans_l_ordre():
    environ = {"MISTRAL_SDK_TELEMETRY": "global", "LANGCHAIN_TRACING_V2": "true"}
    assert settings.third_party_tracing(environ) == [
        "LANGCHAIN_TRACING_V2",
        "MISTRAL_SDK_TELEMETRY",
    ]


def test_le_moteur_coupe_langsmith_meme_sous_variables_hostiles(monkeypatch):
    memory_service()  # construit le moteur LangGraph
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "cle-fictive")
    langsmith.utils.get_env_var.cache_clear()
    assert langsmith.utils.tracing_is_enabled() is False
    assert _tracing_v2_is_enabled() is False


def tracing_hook(client: Mistral) -> TracingHook:
    hooks = client.sdk_configuration._hooks.before_request_hooks
    [hook] = [h for h in hooks if isinstance(h, TracingHook)]
    return hook


def test_le_client_mistral_coupe_sa_telemetrie_meme_sous_variable_hostile(monkeypatch):
    """Deuxième défense après le refus au démarrage : une vraie requête du SDK, vers le
    serveur factice, sous MISTRAL_SDK_TELEMETRY=dedicated, ne monte aucune télémétrie."""
    monkeypatch.setenv("MISTRAL_SDK_TELEMETRY", "dedicated")
    # si la coupure manquait, le SDK enverrait ici, sur un port fermé du poste, jamais à
    # api.mistral.ai
    monkeypatch.setenv("MISTRAL_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    # témoin : sans la coupure, la variable suffit à monter un provider du SDK
    witness = TracingHook()
    client = Mistral(api_key="cle-factice")
    assert configure_telemetry_for_hook(
        witness, client.sdk_configuration, respect_global_provider=True
    )
    witness._auto_telemetry_provider.shutdown()  # rien d'émis : file vide
    server = serve("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        llm = MistralProvider(load_config().llm, api_key="cle-factice", server_url=url)
        _, contracts = demo_set.load()
        contract = contracts["demo-01-go-maintenance"]
        LLMExtractor(llm)(
            masking.mask(contract.text(), contract.parties).text, [], contract.profile
        )
    finally:
        server.shutdown()
        server.server_close()
    hook = tracing_hook(llm._client)
    assert hook._auto_telemetry_provider is None
    assert not hook._telemetry_use_global_provider
    assert not hook.tracing_enabled


# --- FastAPI ---------------------------------------------------------------------------


def health_app() -> FastAPI:
    checks = sante.Checks(
        started=lambda: True, database=lambda: True, draining=lambda: False
    )
    return sante.create_health_app(checks, instance="cdg-test")


def bare_app() -> FastAPI:
    """Témoin : une application FastAPI sans réglage de télémétrie."""
    app = FastAPI()

    @app.get("/nombre")
    def nombre(n: int) -> dict[str, int]:
        return {"n": n}

    return app


def global_providers(monkeypatch):
    """Fournisseurs globaux d'OpenTelemetry simulés, qui enregistrent : FastAPI les relit
    à chaque requête. Jamais installés (un fournisseur global ne se remplace plus)."""
    spans, logs, reader = (
        InMemorySpanExporter(),
        InMemoryLogRecordExporter(),
        InMemoryMetricReader(),
    )
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(logs))
    meter_provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: tracer_provider)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: meter_provider)
    monkeypatch.setattr(_logs, "get_logger_provider", lambda: logger_provider)
    # _logs.get_logger, sans fournisseur, appelle la fonction de son module interne, que
    # le paquet ne fait que réexporter
    monkeypatch.setattr(_logs_internal, "get_logger_provider", lambda: logger_provider)

    def recorded() -> tuple[int, int, int]:
        data = reader.get_metrics_data()
        measures = sum(
            len(scope.metrics)
            for resource in (data.resource_metrics if data else [])
            for scope in resource.scope_metrics
        )
        return len(spans.get_finished_spans()), len(logs.get_finished_logs()), measures

    return recorded


def test_fastapi_ne_trace_rien_meme_sous_des_fournisseurs_globaux(monkeypatch):
    """Deuxième défense : même si un fournisseur global apparaissait (FastAPI le
    créerait sous FASTAPI_OTEL_AUTO_CONFIGURE), ni l'interface ni les sondes de santé ne
    tracent, ne mesurent ni ne journalisent quoi que ce soit."""
    recorded = global_providers(monkeypatch)
    web = client()
    assert web.get("/").status_code == 200
    assert web.get("/contrats/inconnu").status_code == 404
    health = TestClient(health_app())
    assert health.get("/sante/vie").status_code == 200
    assert recorded() == (0, 0, 0)
    # témoin : la même requête, sur une application FastAPI sans réglage, trace,
    # mesure, et journalise l'échec de validation
    bare = TestClient(bare_app())
    assert bare.get("/nombre", params={"n": "pas-un-nombre"}).status_code == 422
    spans, logs, measures = recorded()
    assert spans and logs and measures


def test_fastapi_n_installe_aucun_export_sous_la_variable(monkeypatch):
    """Même si la variable passait le refus au démarrage, aucune application du projet
    n'installe de fournisseur global ni d'export."""
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    # port fermé du poste : si l'export partait, jamais vers l'extérieur
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    installed: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(metrics, "set_meter_provider", installed.append)
    monkeypatch.setattr(_logs, "set_logger_provider", installed.append)
    with client():  # démarrage et arrêt de l'application (lifespan)
        pass
    with TestClient(health_app()):
        pass
    assert installed == []
    # témoin : une application FastAPI sans réglage installe un fournisseur au démarrage
    with TestClient(bare_app()):
        pass
    assert installed

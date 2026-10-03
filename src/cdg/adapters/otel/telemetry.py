"""Adaptateur du port de télémétrie (`ports/telemetry.py`) : OpenTelemetry (ADR 008).

- Un `TracerProvider` et un `MeterProvider` à lui, **jamais installés comme globaux** :
  ni le SDK Mistral (sa télémétrie traçerait prompts et réponses), ni le middleware du
  SDK MCP (qui enregistre le texte des exceptions) ne trouvent où émettre.
- Ressource construite par le code (nom du service, commit), jamais par `Resource.create`
  ni par ses détecteurs : ni hôte, ni processus, ni variables `OTEL_RESOURCE_ATTRIBUTES`.
- Attributs en liste blanche (`attributes.py`). Une exception ne laisse que son type
  (`error.type`) et un statut d'erreur sans description : ni `record_exception`, ni
  message. Aucune identité par défaut ; le `sub` de l'interface authentifiée sur réglage.
- Coût calculé aux tarifs de `config/tarifs.yaml` (Langfuse n'a aucun tarif Mistral),
  arrondi à l'émission (`domain/numeric.py`).
- Échec ouvert : export en arrière-plan (`BatchSpanProcessor`, file bornée qui abandonne
  au-delà) ; fermeture bornée à `delai_fermeture_s` (le `force_flush` du SDK ignore son
  délai : la fermeture se fait dans un fil, attendu au plus ce délai).
"""

import base64
import logging
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from cdg.adapters.otel import attributes
from cdg.adapters.otel.attributes import checked
from cdg.application.observation import (
    NoTelemetry,
    ObservabilityConfig,
    UnpricedModel,
    cost_usd,
)
from cdg.application.service import state_label
from cdg.domain.authorization import Actor
from cdg.domain.models import Usage
from cdg.domain.numeric import rounded
from cdg.domain.version import CodeVersion
from cdg.ports.telemetry import Finish, LLMCall, Operation, Telemetry

SERVICE = "contract-decision-graph"
# fournisseur du projet -> `gen_ai.provider.name` des conventions GenAI
PROVIDERS = {"mistral": "mistral_ai", "anthropic": "anthropic"}
IDENTITIES = ("aucune", "sub")
Identity = Literal["aucune", "sub"]
METRICS_INTERVAL_MS = 60_000
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Destination:
    """Point d'entrée OTLP des traces (`…/api/public/otel` pour Langfuse) et clés."""

    url: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)


@dataclass
class _Operation:
    """Totaux d'une opération, partagés par ses nœuds (LangGraph copie le contexte dans
    ses fils : tous voient le même objet)."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    attempts: Counter[str] = field(default_factory=Counter)
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    status: Mapping[str, Any] | None = None

    def finish(self, status: Mapping[str, Any]) -> None:
        self.status = status

    def next_attempt(self, node: str) -> int:
        with self.lock:
            self.calls += 1
            self.attempts[node] += 1
            return self.attempts[node]

    def add(self, usage: Usage, cost: float | None) -> None:
        with self.lock:
            self.tokens_in += usage.tokens_in
            self.tokens_out += usage.tokens_out
            self.cost += cost or 0.0


_CURRENT: ContextVar[_Operation | None] = ContextVar("cdg_operation", default=None)


class _Call:
    def __init__(self) -> None:
        self.usage: Usage | None = None

    def done(self, usage: Usage) -> None:
        self.usage = usage


def _failed(span: Span, exc: BaseException) -> dict[str, Any]:
    span.set_status(Status(StatusCode.ERROR))  # sans description : jamais le message
    return {"error.type": type(exc).__name__}


class OtelTelemetry:
    def __init__(
        self,
        config: ObservabilityConfig,
        *,
        span_processor: SpanProcessor | None,
        metric_reader: MetricReader | None,
        code: CodeVersion,
        identity: str = "aucune",
    ) -> None:
        if identity not in IDENTITIES:
            raise ValueError(
                f"réglage d'identité des traces inconnu : {identity!r} (aucune ou sub)"
            )
        resource = Resource({"service.name": SERVICE, "service.version": code.commit})
        self._traces = TracerProvider(resource=resource, shutdown_on_exit=False)
        if span_processor is not None:
            self._traces.add_span_processor(span_processor)
        self._tracer = self._traces.get_tracer("cdg")
        readers = [metric_reader] if metric_reader is not None else []
        self._metrics = MeterProvider(
            metric_readers=readers, resource=resource, shutdown_on_exit=False
        )
        meter = self._metrics.get_meter("cdg")
        self._llm_duration = meter.create_histogram(
            "gen_ai.client.operation.duration", unit="s"
        )
        self._llm_tokens = meter.create_histogram(
            "gen_ai.client.token.usage", unit="{token}"
        )
        self._llm_cost = meter.create_counter("cdg.llm.cout", unit="USD")
        self._duration = meter.create_histogram("cdg.operation.duree", unit="s")
        self._tarifs = config.tarifs
        self._close_timeout = config.export.delai_fermeture_s
        self._identity = identity

    # --- opération ------------------------------------------------------------------------

    @contextmanager
    def operation(
        self, name: Operation, *, contract_id: str | None, actor: Actor | None
    ) -> Iterator[Finish]:
        state = _Operation()
        token = _CURRENT.set(state)
        start = time.monotonic()
        user = None
        if self._identity == "sub" and actor is not None and actor.canal == "interface":
            user = actor.sub
        with self._tracer.start_as_current_span(
            f"cdg.{name}", record_exception=False, set_status_on_exception=False
        ) as span:
            values: dict[str, Any] = {
                "langfuse.trace.name": name,
                "langfuse.session.id": contract_id,
                "langfuse.user.id": user,
                "cdg.operation": name,
                "cdg.canal": actor.canal if actor is not None else None,
            }
            try:
                yield state.finish
            except Exception as exc:
                values |= _failed(span, exc)
                raise
            finally:
                etat = None
                if state.status is not None:
                    etat = state_label(state.status)
                    values |= {
                        "cdg.etat": etat,
                        "cdg.decision.proposee": state.status.get("proposed_decision"),
                        "cdg.decision.finale": state.status.get("final_decision"),
                        "cdg.rejet": bool(state.status.get("reject_reason")),
                    }
                values |= {
                    "cdg.appels_llm": state.calls,
                    "cdg.tokens.entree": state.tokens_in,
                    "cdg.tokens.sortie": state.tokens_out,
                    "cdg.cout_usd": rounded(state.cost),
                }
                span.set_attributes(checked("operation", values))
                self._duration.record(
                    time.monotonic() - start,
                    checked("metrique", {"cdg.operation": name, "cdg.etat": etat}),
                )
                _CURRENT.reset(token)

    # --- étape ----------------------------------------------------------------------------

    @contextmanager
    def step(
        self,
        node: str,
        *,
        attempt: int | None,
        domain: str | None,
        passthrough: tuple[type[BaseException], ...] = (),
    ) -> Iterator[None]:
        name = node if attributes.identifier(node) else attributes.REFUSED
        with self._tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            values: dict[str, Any] = {
                "langfuse.observation.type": "span",
                "cdg.noeud": node,
                "cdg.tentative": attempt,
                "cdg.domaine": domain,
            }
            try:
                yield
            except passthrough:  # contrôle du graphe (interruption) : pas un échec
                raise
            except Exception as exc:
                values |= _failed(span, exc)
                raise
            finally:
                span.set_attributes(checked("etape", values))

    # --- appel au LLM ---------------------------------------------------------------------

    def _cost(self, usage: Usage) -> float | None:
        try:
            return cost_usd(
                usage.model, usage.tokens_in, usage.tokens_out, self._tarifs
            )
        except UnpricedModel:
            # le démarrage exige un tarif pour chaque modèle de la configuration : un modèle
            # sans tarif ici est signalé, et l'appel reste tracé sans son coût
            log.warning("tarif inconnu pour %s : coût absent de la trace", usage.model)
            return None

    @contextmanager
    def llm_call(self, *, provider: str, tier: str, node: str) -> Iterator[LLMCall]:
        state = _CURRENT.get()
        attempt = state.next_attempt(node) if state is not None else 1
        start = time.monotonic()
        call = _Call()
        gen_ai = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": PROVIDERS.get(provider, provider),
        }
        with self._tracer.start_as_current_span(
            "chat",
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            values: dict[str, Any] = {
                "langfuse.observation.type": "generation",
                **gen_ai,
                "cdg.noeud": node,
                "cdg.niveau": tier,
                "cdg.tentative": attempt,
            }
            measures: dict[str, Any] = dict(gen_ai)
            try:
                yield call
            except Exception as exc:
                failure = _failed(span, exc)
                values |= failure
                measures |= failure
                raise
            finally:
                usage = call.usage
                if usage is not None:
                    self._used(span, usage, values, measures, state)
                span.set_attributes(checked("appel", values))
                self._llm_duration.record(
                    time.monotonic() - start, checked("metrique", measures)
                )

    def _used(
        self,
        span: Span,
        usage: Usage,
        values: dict[str, Any],
        measures: dict[str, Any],
        state: _Operation | None,
    ) -> None:
        if attributes.identifier(usage.model):
            span.update_name(f"chat {usage.model}")
        cost = self._cost(usage)
        values |= {
            "gen_ai.request.model": usage.model,
            "gen_ai.usage.input_tokens": usage.tokens_in,
            "gen_ai.usage.output_tokens": usage.tokens_out,
            "gen_ai.usage.cost": rounded(cost) if cost is not None else None,
            "cdg.latence_ms": usage.latency_ms,
        }
        measures["gen_ai.request.model"] = usage.model
        if state is not None:
            state.add(usage, cost)
        for kind, tokens in (("input", usage.tokens_in), ("output", usage.tokens_out)):
            self._llm_tokens.record(
                tokens, checked("metrique", {**measures, "gen_ai.token.type": kind})
            )
        if cost is not None:
            self._llm_cost.add(
                cost, checked("metrique", {"gen_ai.request.model": usage.model})
            )

    # --- fermeture ------------------------------------------------------------------------

    def close(self) -> None:
        """Envoie les derniers lots, en `delai_fermeture_s` au plus : le `shutdown` du SDK
        tourne dans un fil, abandonné au-delà (journalisé, par type)."""
        done = threading.Event()

        def stop() -> None:
            try:
                self._traces.shutdown()
                self._metrics.shutdown()
            # échec ouvert : la fermeture ne bloque jamais l'arrêt du processus
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "fermeture de la télémétrie en échec (%s)", type(exc).__name__
                )
            finally:
                done.set()

        threading.Thread(target=stop, name="telemetrie-fermeture", daemon=True).start()
        if not done.wait(self._close_timeout):
            log.warning(
                "télémétrie : derniers envois abandonnés après %s s",
                self._close_timeout,
            )


def _basic(destination: Destination) -> str:
    pair = f"{destination.public_key}:{destination.secret_key}".encode()
    return "Basic " + base64.b64encode(pair).decode()


def build(
    config: ObservabilityConfig,
    *,
    traces: Destination | None,
    metrics: str | None,
    code: CodeVersion,
    identity: str = "aucune",
) -> Telemetry:
    """Télémétrie du processus : rien sans destination (aucun objet du SDK créé) ; sinon
    l'export OTLP en HTTP, configuré par le code seul (jamais par les variables `OTEL_*`,
    dont le SDK fusionne pourtant les en-têtes : à ne pas poser)."""
    if traces is None and metrics is None:
        return NoTelemetry()
    export = config.export
    processor = None
    if traces is not None:
        exporter = OTLPSpanExporter(
            endpoint=traces.url.rstrip("/") + "/v1/traces",
            headers={
                "Authorization": _basic(traces),
                "x-langfuse-ingestion-version": "4",
            },
            timeout=export.delai_export_s,
        )
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=export.file_max,
            schedule_delay_millis=export.delai_lot_ms,
            export_timeout_millis=export.delai_export_s * 1000,
        )
    reader = None
    if metrics is not None:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=metrics.rstrip("/") + "/v1/metrics",
                timeout=export.delai_export_s,
            ),
            export_interval_millis=METRICS_INTERVAL_MS,
            export_timeout_millis=export.delai_export_s * 1000,
        )
    return OtelTelemetry(
        config,
        span_processor=processor,
        metric_reader=reader,
        code=code,
        identity=identity,
    )

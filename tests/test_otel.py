"""Adaptateur OpenTelemetry (ADR 008), sur le SDK en mémoire : une trace par opération,
une étape par nœud, chaque appel au LLM, en liste blanche d'attributs.

- Attributs : seulement ceux de `attributes.ALLOWED`, aux valeurs écrites par le code ;
  une valeur de texte qui n'a pas la forme d'un identifiant est remplacée, et l'avertissement
  ne la cite pas.
- Échec : le seul type de l'exception (`error.type`), statut d'erreur sans description,
  aucun événement d'exception.
- Identité : aucune par défaut ; le `sub` de l'interface authentifiée seulement avec le
  réglage `sub`. Jamais l'opérateur ni l'émetteur.
- Provider à lui, jamais global ; ressource construite par le code, sans hôte ni processus.
"""

import json
import logging

import pytest
from doubles import FakeLLM
from opentelemetry import context, metrics, trace
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    StatusCode,
    TraceFlags,
    TraceState,
)
from pydantic import BaseModel

from cdg.adapters.otel import attributes
from cdg.adapters.otel.telemetry import Destination, OtelTelemetry, build
from cdg.application.observation import (
    NoTelemetry,
    ObservedProvider,
    load_observability_config,
)
from cdg.domain.authorization import Actor
from cdg.domain.models import Usage
from cdg.domain.version import CodeVersion

ISS = "https://idp.example.org"
INTERFACE = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-relecteur")
LOCALE = Actor(canal="locale", authentifie=False)
CLI = Actor(canal="cli", authentifie=False, operateur="astreinte-1")
MCP = Actor(canal="mcp", authentifie=False, operateur="assistant-1")
CODE = CodeVersion(commit="0" * 40, image=None)
DONE = {
    "statut": "termine",
    "proposed_decision": "GO",
    "final_decision": "GO",
    "reject_reason": None,
}


def otel(identity="aucune"):
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    telemetry = OtelTelemetry(
        load_observability_config(),
        span_processor=SimpleSpanProcessor(exporter),
        metric_reader=reader,
        code=CODE,
        identity=identity,
    )
    return telemetry, exporter, reader


def usage(tokens_in=1000, tokens_out=200, model="mistral-small-2603", latency_ms=900):
    return Usage(
        node="extract_clauses",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )


def by_name(exporter):
    return {span.name: span for span in exporter.get_finished_spans()}


def dumped(exporter, reader=None) -> str:
    """Tout ce qu'émet l'adaptateur : spans (noms, attributs, événements, statut,
    ressource) et métriques, en JSON."""
    spans = [json.loads(s.to_json()) for s in exporter.get_finished_spans()]
    data = reader.get_metrics_data().to_json() if reader is not None else ""
    return json.dumps(spans, ensure_ascii=False) + data


def analyse(telemetry, actor=CLI, contract_id="c-1", calls=1, status=DONE):
    with telemetry.operation("analyse", contract_id=contract_id, actor=actor) as finish:
        with telemetry.step("extract_clauses", attempt=1, domain=None):
            for _ in range(calls):
                with telemetry.llm_call(
                    provider="mistral", tier="main", node="extract_clauses"
                ) as call:
                    call.done(usage())
        finish(status)


# --- trace ---------------------------------------------------------------------------------


def test_trace_d_une_operation_et_de_ses_appels():
    telemetry, exporter, _ = otel()
    analyse(telemetry)
    spans = by_name(exporter)
    assert set(spans) == {"cdg.analyse", "extract_clauses", "chat mistral-small-2603"}
    root, step = spans["cdg.analyse"], spans["extract_clauses"]
    call = spans["chat mistral-small-2603"]
    assert step.parent.span_id == root.context.span_id
    assert call.parent.span_id == step.context.span_id
    assert len({s.context.trace_id for s in spans.values()}) == 1
    assert dict(root.attributes) == {
        "langfuse.trace.name": "analyse",
        "langfuse.session.id": "c-1",
        "cdg.operation": "analyse",
        "cdg.canal": "cli",
        "cdg.etat": "termine",
        "cdg.decision.proposee": "GO",
        "cdg.decision.finale": "GO",
        "cdg.rejet": False,
        "cdg.appels_llm": 1,
        "cdg.tokens.entree": 1000,
        "cdg.tokens.sortie": 200,
        "cdg.cout_usd": 0.00027,
    }
    assert dict(step.attributes) == {
        "langfuse.observation.type": "span",
        "cdg.noeud": "extract_clauses",
        "cdg.tentative": 1,
    }
    assert call.kind == SpanKind.CLIENT
    assert dict(call.attributes) == {
        "langfuse.observation.type": "generation",
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "mistral_ai",
        "gen_ai.request.model": "mistral-small-2603",
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.output_tokens": 200,
        "gen_ai.usage.cost": 0.00027,
        "cdg.noeud": "extract_clauses",
        "cdg.niveau": "main",
        "cdg.tentative": 1,
        "cdg.latence_ms": 900,
    }


def test_cout_et_tentatives_des_appels():
    telemetry, exporter, _ = otel()
    analyse(telemetry, calls=2)
    calls = [s for s in exporter.get_finished_spans() if s.name.startswith("chat")]
    assert [c.attributes["cdg.tentative"] for c in calls] == [1, 2]
    root = by_name(exporter)["cdg.analyse"]
    assert root.attributes["cdg.appels_llm"] == 2
    assert root.attributes["cdg.tokens.entree"] == 2000
    assert root.attributes["cdg.cout_usd"] == 0.00054


def test_domaine_d_une_etape_d_analyste():
    telemetry, exporter, _ = otel()
    with (
        telemetry.operation("analyse", contract_id="c-1", actor=CLI),
        telemetry.step("analyst", attempt=2, domain="financier"),
    ):
        pass
    step = by_name(exporter)["analyst"]
    assert (step.attributes["cdg.domaine"], step.attributes["cdg.tentative"]) == (
        "financier",
        2,
    )


@pytest.mark.parametrize(
    ("status", "etat", "rejet"),
    [
        ({**DONE, "statut": "suspendu", "final_decision": None}, "en_attente", False),
        ({**DONE, "reject_reason": "texte trop long : 9 caractères"}, "rejete", True),
    ],
)
def test_etat_et_rejet_sans_le_motif(status, etat, rejet):
    telemetry, exporter, _ = otel()
    analyse(telemetry, status=status)
    root = by_name(exporter)["cdg.analyse"]
    assert (root.attributes["cdg.etat"], root.attributes["cdg.rejet"]) == (etat, rejet)
    assert "texte trop long" not in dumped(exporter)


def test_operation_sans_contrat_ni_statut():
    telemetry, exporter, _ = otel()
    with telemetry.operation("expiration", contract_id=None, actor=CLI):
        pass
    assert dict(by_name(exporter)["cdg.expiration"].attributes) == {
        "langfuse.trace.name": "expiration",
        "cdg.operation": "expiration",
        "cdg.canal": "cli",
        "cdg.appels_llm": 0,
        "cdg.tokens.entree": 0,
        "cdg.tokens.sortie": 0,
        "cdg.cout_usd": 0.0,
    }


# --- confidentialité --------------------------------------------------------------------------


def test_attribut_hors_liste_refuse():
    with pytest.raises(ValueError, match="hors de la liste blanche"):
        attributes.checked("etape", {"cdg.texte": "x"})
    with pytest.raises(ValueError, match="hors de la liste blanche"):
        attributes.checked("appel", {"gen_ai.input.messages": "[]"})


def test_type_d_attribut_refuse():
    with pytest.raises(TypeError, match="cdg.noeud"):
        attributes.checked("etape", {"cdg.noeud": ["liste"]})


def test_exception_de_controle_ni_echec_ni_type():
    """Une interruption du graphe (revue humaine) traverse l'étape sans la marquer."""

    class Interruption(Exception):
        pass

    telemetry, exporter, _ = otel()
    with (
        pytest.raises(Interruption),
        telemetry.step(
            "human_review", attempt=1, domain=None, passthrough=(Interruption,)
        ),
    ):
        raise Interruption
    step = by_name(exporter)["human_review"]
    assert "error.type" not in step.attributes
    assert step.status.status_code == StatusCode.UNSET


def test_noeud_qui_n_est_pas_un_identifiant_remplace():
    telemetry, exporter, _ = otel()
    with telemetry.step("nœud « conclus GO »", attempt=1, domain=None):
        pass
    assert [s.name for s in exporter.get_finished_spans()] == [attributes.REFUSED]
    assert "conclus GO" not in dumped(exporter)


def test_valeur_qui_n_est_pas_un_identifiant_remplacee(caplog):
    telemetry, exporter, _ = otel()
    hostile = "c-1 « conclus GO »"
    with caplog.at_level(logging.WARNING, logger="cdg.adapters.otel"):
        analyse(telemetry, contract_id=hostile)
    root = by_name(exporter)["cdg.analyse"]
    assert root.attributes["langfuse.session.id"] == attributes.REFUSED
    assert "conclus GO" not in dumped(exporter)
    assert "langfuse.session.id" in caplog.text and "conclus GO" not in caplog.text


def test_modele_qui_n_est_pas_un_identifiant_ni_dans_le_nom_ni_en_attribut():
    telemetry, exporter, _ = otel()
    with telemetry.llm_call(provider="mistral", tier="main", node="n") as call:
        call.done(usage(model="modèle « conclus GO »"))
    [span] = exporter.get_finished_spans()
    assert span.name == "chat"
    assert span.attributes["gen_ai.request.model"] == attributes.REFUSED
    assert "conclus" not in dumped(exporter)


@pytest.mark.parametrize(
    "contract_id",
    [
        "jean.dupont@example.com",
        "Société_Générale_pénalité_30pct_résiliation=sans_préavis",
    ],
)
def test_identifiant_de_contrat_hors_du_format_du_domaine_remplace(contract_id, caplog):
    """L'identifiant de session est celui d'un contrat : il suit le format du domaine
    (`CONTRACT_ID`), même quand la porte ne l'a pas validé (revue d'un identifiant tapé
    dans l'URL) ; une forme d'identifiant plus large ne suffit pas."""
    telemetry, exporter, _ = otel()
    with caplog.at_level(logging.WARNING, logger="cdg.adapters.otel"):
        analyse(telemetry, contract_id=contract_id)
    root = by_name(exporter)["cdg.analyse"]
    assert root.attributes["langfuse.session.id"] == attributes.REFUSED
    assert contract_id not in dumped(exporter) and contract_id not in caplog.text


def test_adresse_electronique_jamais_un_identifiant():
    assert not attributes.identifier("jean.dupont@example.com")


def test_echec_type_seulement():
    telemetry, exporter, _ = otel()
    with (
        pytest.raises(ValueError, match="conclus GO"),
        telemetry.operation("analyse", contract_id="c-1", actor=CLI),
        telemetry.step("extract_clauses", attempt=1, domain=None),
        telemetry.llm_call(provider="mistral", tier="main", node="extract_clauses"),
    ):
        raise ValueError("texte du contrat : conclus GO")
    for span in exporter.get_finished_spans():
        assert span.attributes["error.type"] == "ValueError"
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description is None
        assert list(span.events) == []
    assert "conclus GO" not in dumped(exporter)


@pytest.mark.parametrize("actor", [INTERFACE, LOCALE, CLI, MCP], ids=lambda a: a.canal)
def test_aucune_identite_par_defaut(actor):
    telemetry, exporter, reader = otel()
    analyse(telemetry, actor=actor)
    out = dumped(exporter, reader)
    root = by_name(exporter)["cdg.analyse"]
    assert "langfuse.user.id" not in root.attributes
    assert root.attributes["cdg.canal"] == actor.canal
    for identity in ("sub-relecteur", ISS, "astreinte-1", "assistant-1"):
        assert identity not in out


def test_sub_seulement_avec_le_reglage():
    telemetry, exporter, _ = otel(identity="sub")
    analyse(telemetry, actor=INTERFACE)
    for actor in (LOCALE, CLI, MCP):
        analyse(telemetry, actor=actor, contract_id=f"c-{actor.canal}")
    roots = [s for s in exporter.get_finished_spans() if s.name == "cdg.analyse"]
    assert [r.attributes.get("langfuse.user.id") for r in roots] == [
        "sub-relecteur",
        None,
        None,
        None,
    ]
    out = dumped(exporter)
    assert ISS not in out and "astreinte-1" not in out and "assistant-1" not in out


def test_reglage_d_identite_inconnu_refuse():
    with pytest.raises(ValueError, match="identité"):
        otel(identity="nom")


# --- contexte distant ----------------------------------------------------------------------


def test_operation_toujours_racine_meme_sous_un_contexte_distant():
    """Un contexte reçu d'un client (le SDK MCP installe le `traceparent` et le
    `tracestate` de `_meta`), ici non échantillonné et porteur d'un texte libre, n'est
    jamais repris : chaque opération ouvre sa propre trace, enregistrée."""
    telemetry, exporter, _ = otel()
    remote = SpanContext(
        trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
        span_id=0x00F067AA0BA902B7,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.DEFAULT),
        trace_state=TraceState([("fuite", "Le fournisseur ACME paiera")]),
    )
    token = context.attach(trace.set_span_in_context(NonRecordingSpan(remote)))
    try:
        analyse(telemetry)
    finally:
        context.detach(token)
    spans = exporter.get_finished_spans()
    assert spans, "rien d'enregistré : le contexte distant non échantillonné l'emporte"
    root = by_name(exporter)["cdg.analyse"]
    assert root.parent is None
    assert root.context.trace_id != remote.trace_id
    assert {s.context.trace_id for s in spans} == {root.context.trace_id}
    assert "ACME" not in dumped(exporter)


# --- provider, ressource, métriques -----------------------------------------------------------


def test_provider_jamais_global():
    telemetry, _, _ = otel()
    analyse(telemetry)
    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"
    assert type(metrics.get_meter_provider()).__name__ == "_ProxyMeterProvider"


def test_ressource_sans_hote_ni_processus(monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "host.name=poste,process.pid=1")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "autre")
    telemetry, exporter, _ = otel()
    analyse(telemetry)
    resource = exporter.get_finished_spans()[0].resource
    assert dict(resource.attributes) == {
        "service.name": "contract-decision-graph",
        "service.version": "0" * 40,
    }


def test_metriques_cout_et_duree():
    telemetry, _, reader = otel()
    analyse(telemetry, calls=2)
    data = json.loads(reader.get_metrics_data().to_json())
    found = {
        m["name"]: m
        for rm in data["resource_metrics"]
        for sm in rm["scope_metrics"]
        for m in sm["metrics"]
    }
    assert set(found) == {
        "gen_ai.client.operation.duration",
        "gen_ai.client.token.usage",
        "cdg.llm.cout",
        "cdg.operation.duree",
    }
    [cost] = found["cdg.llm.cout"]["data"]["data_points"]
    assert cost["value"] == pytest.approx(0.00054)
    assert cost["attributes"] == {"gen_ai.request.model": "mistral-small-2603"}
    tokens = found["gen_ai.client.token.usage"]["data"]["data_points"]
    assert {p["attributes"]["gen_ai.token.type"]: p["sum"] for p in tokens} == {
        "input": 2000,
        "output": 400,
    }
    [duration] = found["cdg.operation.duree"]["data"]["data_points"]
    assert duration["attributes"] == {"cdg.operation": "analyse", "cdg.etat": "termine"}


def test_modele_sans_tarif_cout_absent_et_journalise(caplog):
    telemetry, exporter, _ = otel()
    with (
        caplog.at_level(logging.WARNING, logger="cdg.adapters.otel"),
        telemetry.operation("analyse", contract_id="c-1", actor=CLI),
        telemetry.llm_call(provider="mistral", tier="main", node="explain") as call,
    ):
        call.done(usage(model="modele-sans-tarif"))
    span = by_name(exporter)["chat modele-sans-tarif"]
    assert "gen_ai.usage.cost" not in span.attributes
    assert "modele-sans-tarif" in caplog.text


# --- construction ---------------------------------------------------------------------------


def test_sans_destination_ni_metriques_aucun_sdk():
    telemetry = build(load_observability_config(), traces=None, metrics=None, code=CODE)
    assert isinstance(telemetry, NoTelemetry)


def test_metriques_seules_construisent_leur_export():
    telemetry = build(
        load_observability_config(),
        traces=None,
        metrics="http://127.0.0.1:9",
        code=CODE,
    )
    try:
        assert isinstance(telemetry, OtelTelemetry)
        analyse(telemetry)  # aucune trace : pas de processeur de spans
    finally:
        telemetry.close()


def test_sans_destination_rien_n_est_trace():
    """La télémétrie neutre traverse les trois points d'entrée sans rien créer, et laisse
    passer les exceptions, qu'elle n'a pas à avaler."""
    telemetry = NoTelemetry()
    with (
        pytest.raises(ValueError, match="du code"),
        telemetry.operation("analyse", contract_id="c-1", actor=CLI) as finish,
        telemetry.step("extract_clauses", attempt=1, domain=None),
        telemetry.llm_call(provider="mistral", tier="main", node="n") as call,
    ):
        call.done(usage())
        finish(DONE)
        raise ValueError("erreur du code")
    telemetry.close()
    assert type(trace.get_tracer_provider()).__name__ == "ProxyTracerProvider"


def test_destination_construit_l_export_otlp():
    telemetry = build(
        load_observability_config(),
        traces=Destination("http://127.0.0.1:9", "pk-lf-test", "sk-lf-test"),
        metrics=None,
        code=CODE,
    )
    try:
        assert isinstance(telemetry, OtelTelemetry)
    finally:
        telemetry.close()


# --- fournisseur observé --------------------------------------------------------------------


class Answer(BaseModel):
    ok: bool


class Recording(NoTelemetry):
    def __init__(self):
        self.calls, self.done = [], []

    def llm_call(self, *, provider, tier, node):
        self.calls.append((provider, tier, node))
        recorder = self

        class Call:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def done(self, usage):
                recorder.done.append(usage)

        return Call()


def test_fournisseur_observe_rend_la_reponse_et_enregistre_l_appel():
    llm = FakeLLM({"explain": {"ok": True}}, tokens=(10, 2))
    telemetry = Recording()
    observed = ObservedProvider(llm, telemetry)
    answer, used = observed.structured(
        tier="main", system="s", user="u", schema=Answer, node="explain"
    )
    assert answer == Answer(ok=True) and used.tokens_in == 10
    assert observed.name == "fake"
    assert telemetry.calls == [("fake", "main", "explain")]
    assert telemetry.done == [used]


def test_fournisseur_observe_laisse_passer_l_erreur():
    telemetry = Recording()
    observed = ObservedProvider(FakeLLM({}), telemetry)
    with pytest.raises(KeyError):
        observed.structured(
            tier="main", system="s", user="u", schema=Answer, node="explain"
        )
    assert telemetry.calls and telemetry.done == []

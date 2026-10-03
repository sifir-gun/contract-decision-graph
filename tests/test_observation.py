"""Observabilité de bout en bout (ADR 008), sur le SDK en mémoire : le service ouvre une
trace par opération, la garde de chaque nœud une étape, le fournisseur LLM observé un
appel par requête au modèle ; rien d'autre.

Confidentialité (point 4 de la spec) : les 13 contrats du jeu passent par l'extraction et
l'explication réelles (LLM en doublure), puis par la revue humaine ; aucune trace ni
métrique ne contient une fenêtre de six mots de leur texte, brut ou masqué, des prompts
envoyés au modèle ni de ses réponses, ni le nom d'une partie.
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest
from doubles import CODE, CONTRACT_TEXT, clauses
from fuites import leaks
from mcp_helpers import MCP_ACTOR, session
from observation_helpers import ANALYSTE, CONFIG, service, tarifs_de_test
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from web_helpers import PENDING_TEXT

from cdg import cli
from cdg.adapters.demo.references import DeclaredCrag
from cdg.adapters.mcp.server import create_server
from cdg.adapters.otel.telemetry import OtelTelemetry
from cdg.application import demo_set
from cdg.application.observation import NoTelemetry
from cdg.domain.authorization import Actor

CLI = Actor(canal="cli", authentifie=False, operateur="relecteur-1")
PROMPTS = (
    Path(__file__).resolve().parents[1] / "src" / "cdg" / "application" / "prompts"
)


@pytest.fixture
def otel():
    exporter, reader = InMemorySpanExporter(), InMemoryMetricReader()
    telemetry = OtelTelemetry(
        tarifs_de_test(),
        span_processor=SimpleSpanProcessor(exporter),
        metric_reader=reader,
        code=CODE,
    )
    return telemetry, exporter, reader


def traces(exporter) -> dict[int, list]:
    grouped: dict[int, list] = {}
    for span in exporter.get_finished_spans():
        grouped.setdefault(span.context.trace_id, []).append(span)
    return grouped


def root_of(spans):
    [root] = [s for s in spans if s.parent is None]
    return root


def answer(actor: Actor) -> dict:
    return {
        "decision": "NO_GO",
        "acteur": actor.model_dump(mode="json"),
        "reason": "revu",
    }


# --- une trace par opération, une étape par nœud, chaque appel au LLM ----------------------


def test_trace_d_une_analyse(otel):
    telemetry, exporter, _ = otel
    # juridique 0,5 : un constat, GO direct ; expliqué par le LLM (sans constat, rien à
    # expliquer : le gabarit, sans appel)
    svc, _ = service(telemetry, found=clauses(responsabilite_fournisseur=50))
    svc.analyse(CONTRACT_TEXT, contract_id="c-1", actor=ANALYSTE)
    [spans] = traces(exporter).values()
    root = root_of(spans)
    assert root.name == "cdg.analyse"
    steps = [s.name for s in spans if s.attributes.get("cdg.noeud") and s is not root]
    assert {
        "validate_input",
        "extract_clauses",
        "verify_extraction",
        "decision_gate",
    } <= set(steps)
    assert steps.count("analyst") == 4
    calls = {s.attributes["cdg.noeud"]: s for s in spans if s.name.startswith("chat")}
    assert set(calls) == {"extract_clauses", "explain"}
    by_id = {s.context.span_id: s for s in spans}
    assert by_id[calls["extract_clauses"].parent.span_id].name == "extract_clauses"
    assert by_id[calls["explain"].parent.span_id].name == "explain"
    assert root.attributes["cdg.appels_llm"] == 2
    assert (
        root.attributes["cdg.tokens.entree"],
        root.attributes["cdg.tokens.sortie"],
    ) == (
        2000,
        500,
    )
    # tarif de test du modèle double : 1 $ et 2 $ par million
    assert root.attributes["cdg.cout_usd"] == 0.003
    assert root.attributes["cdg.etat"] == "termine"


def test_revue_et_expiration_ont_leur_trace_de_meme_session(otel):
    telemetry, exporter, _ = otel
    svc, _ = service(telemetry)
    svc.analyse(PENDING_TEXT, contract_id="c-attente", actor=ANALYSTE)
    svc.decide("c-attente", answer(CLI))
    svc.analyse(PENDING_TEXT, contract_id="c-expire", actor=ANALYSTE)
    svc.expire(timedelta(0), CLI)
    roots = [root_of(spans) for spans in traces(exporter).values()]
    seen = [(r.name, r.attributes.get("langfuse.session.id")) for r in roots]
    assert seen == [
        ("cdg.analyse", "c-attente"),
        ("cdg.revue", "c-attente"),
        ("cdg.analyse", "c-expire"),
        ("cdg.expiration", None),
    ]
    revue = roots[1]
    assert (revue.attributes["cdg.etat"], revue.attributes["cdg.decision.finale"]) == (
        "termine",
        "NO_GO",
    )


def test_analystes_en_parallele_dans_la_meme_trace(otel):
    telemetry, exporter, _ = otel
    svc, _ = service(telemetry)
    svc.analyse(CONTRACT_TEXT, contract_id="c-1", actor=ANALYSTE)
    [spans] = traces(exporter).values()
    root = root_of(spans)
    analysts = [s for s in spans if s.name == "analyst"]
    assert len(analysts) == 4
    assert {s.parent.span_id for s in analysts} == {root.context.span_id}
    assert {s.attributes["cdg.domaine"] for s in analysts} == {
        "juridique",
        "financier",
        "conformite",
        "operationnel",
    }


def test_interruption_de_la_revue_n_est_pas_un_echec(otel):
    telemetry, exporter, _ = otel
    svc, _ = service(telemetry)
    svc.analyse(PENDING_TEXT, contract_id="c-attente", actor=ANALYSTE)
    [review] = [s for s in exporter.get_finished_spans() if s.name == "human_review"]
    assert "error.type" not in review.attributes


def test_contexte_d_un_client_mcp_ignore(otel, monkeypatch):
    """Le SDK MCP installe comme contexte courant le `traceparent` et le `tracestate`
    que le client met dans `_meta` : ni le rattachement, ni le texte libre du
    `tracestate`, ni son drapeau d'échantillonnage ne passent dans les traces."""
    telemetry, exporter, _ = otel
    monkeypatch.setitem(cli.TELEMETRY, "processus", telemetry)
    server = create_server(cli.demo_service(CONFIG), actor=MCP_ACTOR, demo=True)
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    for flags in ("01", "00"):
        arguments = {
            "contrat_du_jeu": "demo-01-go-maintenance",
            "identifiant": f"c-{flags}",
        }
        meta = {
            "traceparent": f"00-{trace_id}-00f067aa0ba902b7-{flags}",
            "tracestate": "fuite=Le fournisseur ACME paiera 30 pct de penalites",
        }
        result = session(
            server,
            lambda client, arguments=arguments, meta=meta: client.call_tool(
                "analyser_contrat", arguments, meta=meta
            ),
        )
        assert not result.is_error, result.content
    roots = [root_of(spans) for spans in traces(exporter).values()]
    assert [r.attributes["langfuse.session.id"] for r in roots] == ["c-01", "c-00"]
    assert all(r.context.trace_id != int(trace_id, 16) for r in roots)
    spans = [json.loads(s.to_json()) for s in exporter.get_finished_spans()]
    assert "ACME" not in json.dumps(spans)


def test_revue_d_un_identifiant_hostile_session_refusee(otel):
    """Une revue sur un identifiant tapé dans l'URL, jamais validé (un contrat existant
    reste lisible quel que soit son identifiant) : la trace n'en garde rien."""
    telemetry, exporter, _ = otel
    svc, _ = service(telemetry)
    with pytest.raises(Exception):  # noqa: B017  (contrat inconnu : l'erreur du moteur)
        svc.decide("jean.dupont@example.com", answer(CLI))
    [revue] = [s for s in exporter.get_finished_spans() if s.name == "cdg.revue"]
    assert revue.attributes["langfuse.session.id"] == "<refusé>"
    assert "dupont" not in revue.to_json()


def test_revue_refusee_type_seulement(otel):
    telemetry, exporter, _ = otel
    svc, _ = service(telemetry)
    svc.analyse(PENDING_TEXT, contract_id="c-attente", actor=ANALYSTE)
    mcp = Actor(canal="mcp", authentifie=False, operateur="assistant-1")
    with pytest.raises(Exception, match="jamais de décision"):
        svc.decide("c-attente", answer(mcp))
    [revue] = [s for s in exporter.get_finished_spans() if s.name == "cdg.revue"]
    assert revue.attributes["error.type"] == "FourEyesRefused"
    assert "jamais" not in json.dumps(dict(revue.attributes))


# --- confidentialité : les 13 contrats du jeu ------------------------------------------------


def dumped(exporter, reader) -> str:
    spans = [json.loads(s.to_json()) for s in exporter.get_finished_spans()]
    return json.dumps(spans, ensure_ascii=False) + reader.get_metrics_data().to_json()


def test_aucun_texte_des_13_contrats_dans_les_traces(otel):
    telemetry, exporter, reader = otel
    expected_on, contracts = demo_set.load()
    sources: list[str] = [p.read_text(encoding="utf-8") for p in PROMPTS.glob("*.md")]
    sent: list[dict] = []
    parties: list[str] = []
    for contract in contracts.values():
        svc, llms = service(
            telemetry,
            found=list(contract.clauses) or clauses(),
            crag=DeclaredCrag(CONFIG.corpus.chunk_max_words),
            today=expected_on,
        )
        svc.analyse(
            contract.text(),
            contract_id=contract.id,
            actor=ANALYSTE,
            parties=list(contract.parties),
        )
        if svc.dossier(contract.id)["etat"] == "en_attente":
            svc.decide(contract.id, answer(CLI))
        sources += [contract.text(), svc.dossier(contract.id)["texte_masque"]]
        sources += [c.quote for c in contract.clauses if c.quote]
        parties += list(contract.parties)
        sent += [call for llm in llms for call in llm.calls]
    out = dumped(exporter, reader)
    assert exporter.get_finished_spans(), "aucune trace : le test passerait à vide"
    found = {window for source in sources for window in leaks(source, out)}
    assert found == set()
    assert [p for p in parties if p in out] == []
    # le détecteur, lui, trouve ces textes dans ce qui a été envoyé au modèle
    to_model = json.dumps(
        [{k: str(v) for k, v in call.items()} for call in sent], ensure_ascii=False
    )
    assert leaks(contracts["demo-11-piege-injection"].text(), to_model)


def test_demo_sans_destination_aucune_telemetrie():
    svc = cli.demo_service(CONFIG)
    assert isinstance(svc.telemetry, NoTelemetry)

"""Série réelle sur le jeu de démonstration (J5), avec le vrai modèle. Payant.

Extraction, CRAG et explication réels, sur les 13 contrats du jeu, 5 essais chacun. Quand
un contrat part en revue avec l'issue attendue, la décision humaine du jeu est reprise ;
avec une autre issue, un NO_GO prudent. Chaque contrat est scellé dans un journal jetable,
puis rejoué à l'identique.

Invariant exigé à chaque essai : jamais de décision automatique plus favorable que la
décision attendue. Concordance, stabilité, coût et latences sont mesurés sans seuil : une
ligne `LLM-RESULT` par essai, une ligne `LLM-SERIE` pour la série, reportées au journal.

Essai préalable, consigné et non compté : `uv run pytest --llm -m llm
tests/test_llm_jeu.py -s -k prealable`. Série : `-k "not prealable"`.
"""

import json
import time

import pytest
from demo_set import load
from doubles import make_deps
from langgraph.checkpoint.memory import InMemorySaver
from serie import (
    ACCOUNT_TOKENS_PER_MINUTE,
    Pacer,
    analysts_wall_ms,
    classify,
    clause_gaps,
    cost_usd,
    describe,
    expected_outcome,
    outcome_of,
    step_latencies,
    summarize,
    tokens_by_model,
)

from cdg import settings
from cdg.adapters import fastembed
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.adapters.llm import build_provider
from cdg.adapters.postgres import conninfo, rag_store
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.domain import audit
from cdg.domain.config import load_config

pytestmark = [pytest.mark.llm, pytest.mark.pg]

CONFIG = load_config()
ANALYSIS_DATE, CONTRACTS = load()
BY_ID = {c.id: c for c in CONTRACTS}
RUNS = range(1, 6)
# revue non prévue par le jeu (une autre issue que l'attendue) : décision prudente
UNPLANNED_REVIEW = {
    "decision": "NO_GO",
    "reviewer": "relecteur-serie",
    "reason": "revue non prévue par le jeu : décision prudente de la série",
}


@pytest.fixture(scope="module")
def llm():
    settings.load_env()
    return build_provider(CONFIG.llm)  # clé absente : SettingsError, échec explicite


@pytest.fixture(scope="module")
def real_crag(llm):
    """CRAG réel : pgvector, e5 local, juge léger."""
    embedder = fastembed.FastembedEmbedder(
        CONFIG.embedding, settings.embedding_cache_dir()
    )
    retriever = rag_store.PgvectorRetriever(conninfo.app_conninfo(), embedder)
    return orchestrator.crag_runner(retriever, llm, CONFIG)


@pytest.fixture(scope="module")
def pace():
    return Pacer(ACCOUNT_TOKENS_PER_MINUTE)


@pytest.fixture(scope="module")
def series():
    lines: list[dict] = []
    yield lines
    if lines:
        summary = {"serie": "jeu de démonstration", **summarize(lines)}
        print("\nLLM-SERIE " + json.dumps(summary, ensure_ascii=False))


def analyse(llm, crag, store, contract, thread):
    """Un contrat, du texte au scellement ; l'issue est lue avant toute reprise humaine."""
    deps = make_deps(LLMExtractor(llm), crag, store, explainer=LLMExplainer(llm))
    graph = orchestrator.build_graph(CONFIG, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    start = time.monotonic()
    status = orchestrator.run_contract(
        graph,
        thread,
        contract.text,
        contract.parties,
        analysis_date=ANALYSIS_DATE,
        config=CONFIG,
    )
    outcome, human = outcome_of(status), None
    if status["statut"] == "suspendu":
        planned = classify(contract.expected, outcome) == "conforme"
        human = contract.human if planned and contract.human else UNPLANNED_REVIEW
        status = orchestrator.resume_thread(graph, thread, human, config=CONFIG)
    duration_ms = round((time.monotonic() - start) * 1000)
    config = {"configurable": {"thread_id": thread}}
    values = graph.get_state(config).values
    history = reversed(list(graph.get_state_history(config)))
    steps = [(s.created_at, s.next) for s in history]
    return outcome, human, status, values, steps, duration_ms


def measure(llm, crag, pace, store, contract, run) -> dict:
    pace.wait()
    thread = f"llm-jeu-{contract.id}-{run}"
    outcome, human, status, values, steps, duration_ms = analyse(
        llm, crag, store, contract, thread
    )
    usage = values.get("usage", [])
    report = values.get("failure_report") or {}
    explanation = values.get("explanation")
    tokens = tokens_by_model(usage)
    line = {
        "contrat": contract.id,
        "essai": run,
        "realiste": contract.realistic,
        "attendu": describe(expected_outcome(contract.expected)),
        "issue": describe(outcome),
        "classement": classify(contract.expected, outcome),
        "revue": None if human is None else human["decision"],
        "revue_prevue": human is not None and human is contract.human,
        "finale": status["final_decision"],
        "essais_extraction": values.get("extraction_attempts", 0),
        "echec": report.get("stage"),
        "problemes_extraction": report.get("problems", []),
        "constats_contrat": values.get("input_findings", []),
        "statuts": {v.domain: v.retrieval_status for v in values.get("verdicts", [])},
        "ecarts_extraction": clause_gaps(values.get("clauses", []), contract.clauses),
        "explication": None
        if explanation is None
        else {
            "source": explanation.source,
            "essais": explanation.attempts,
            "motifs": explanation.reasons,
        },
        "tokens": tokens,
        "cout_usd": round(cost_usd(usage), 6),
        "duree_ms": duration_ms,
        "latences": {**step_latencies(usage), "analystes_ms": analysts_wall_ms(steps)},
    }
    pace.consumed(tokens.get(CONFIG.llm.model("main"), 0))
    print(
        "LLM-RESULT "
        + json.dumps(
            {"serie": "jeu", "fournisseur": CONFIG.llm.provider, **line},
            ensure_ascii=False,
            default=str,
        )
    )
    # invariant de la série, à chaque essai
    assert line["classement"] != "plus_favorable", line
    assert status["statut"] == "termine", line
    [entry] = store.entries()
    assert entry.contract_id == thread
    assert audit.replay(entry.record, CONFIG).identical
    return line


def test_essai_prealable_non_compte(llm, real_crag, pace, audit_journal):
    """Un contrat, une fois : vérifie le banc (clé, base, corpus indexé, modèles) avant la
    série. Consigné au journal, jamais compté."""
    measure(
        llm,
        real_crag,
        pace,
        audit_journal,
        BY_ID["demo-01-go-maintenance"],
        "prealable",
    )


@pytest.mark.parametrize("contract", CONTRACTS, ids=[c.id for c in CONTRACTS])
@pytest.mark.parametrize("run", RUNS)
def test_jeu_reel(llm, real_crag, pace, series, audit_journal, run, contract):
    series.append(measure(llm, real_crag, pace, audit_journal, contract, run))

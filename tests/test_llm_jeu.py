"""Série réelle sur le jeu de démonstration (J5), avec le vrai modèle. Payant.

Extraction, CRAG et explication réels, 5 essais par contrat, chaque contrat sous son
profil : une série par profil (PR 4 des profils), 13 contrats `fournisseur`, 7
`sous_traitance`. Quand un contrat part en revue avec l'issue attendue, la décision
humaine du jeu est reprise ; avec une autre issue, un NO_GO prudent. Chaque contrat est
scellé dans un journal jetable, sous son profil, puis rejoué à l'identique.

Invariant exigé à chaque essai : jamais de décision automatique plus favorable que la
décision attendue. Concordance, stabilité, coût et latences sont mesurés sans seuil : une
ligne `LLM-RESULT` par essai, une ligne `LLM-SERIE` par profil, reportées au journal.

Le profil se choisit par `-k`, chaque série précédée de son essai préalable, consigné et
non compté, qui exige l'issue attendue (banc défaillant : arrêt avant la dépense) :
`uv run pytest --llm -m llm tests/test_llm_jeu.py -s -k "fournisseur and prealable"`,
puis `-k "fournisseur and not prealable"` ; de même avec `sous_traitance`. Sans profil
dans `-k`, les deux séries tournent.
"""

import json
import time

import pytest
from demo_set import load
from doubles import ACTEUR_ANALYSTE, ACTEUR_RELECTEUR, CODE, make_deps
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
    extraction_refusals,
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
# profil dans l'identifiant de chaque essai : `-k` choisit la série d'un profil
IDS = [f"{c.profile}-{c.id}" for c in CONTRACTS]
RUNS = range(1, 6)
# essai préalable de chaque profil. Le CRAG ne tourne que sur les clauses à constat : pour
# la sous-traitance, un contrat à constats dans les quatre domaines, pour que le banc
# interroge le corpus du profil ; un contrat sans constat n'y toucherait pas.
PREALABLES = {
    "fournisseur": "demo-01-go-maintenance",
    "sous_traitance": "st-05-reserves-revetements",
}
# revue non prévue par le jeu (une autre issue que l'attendue) : décision prudente
UNPLANNED_REVIEW = {
    "decision": "NO_GO",
    "acteur": ACTEUR_RELECTEUR.model_dump(mode="json"),
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
    retriever = rag_store.PgvectorRetriever(
        conninfo.app_conninfo(), embedder, CONFIG.crag.search
    )
    return orchestrator.crag_runner(retriever, llm, CONFIG)


@pytest.fixture(scope="module")
def pace():
    return Pacer(ACCOUNT_TOKENS_PER_MINUTE)


@pytest.fixture(scope="module")
def series():
    """Essais de la série, par profil : une ligne `LLM-SERIE` par profil lancé."""
    lines: dict[str, list[dict]] = {}
    yield lines
    for profile, runs in lines.items():
        summary = {
            "serie": "jeu de démonstration",
            "profil": profile,
            **summarize(runs),
        }
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
        actor=ACTEUR_ANALYSTE,
        code=CODE,
        profile=contract.profile,
    )
    outcome, human = outcome_of(status), None
    if status["statut"] == "suspendu":
        planned = classify(contract.expected, outcome) == "conforme"
        human = contract.human if planned and contract.human else UNPLANNED_REVIEW
        status = orchestrator.resume_thread(graph, thread, human, config=CONFIG)
    duration_ms = round((time.monotonic() - start) * 1000)
    config = {"configurable": {"thread_id": thread}}
    values = graph.get_state(config).values
    history = list(reversed(list(graph.get_state_history(config))))
    steps = [(s.created_at, s.next) for s in history]
    feedback = [(s.next, s.values.get("extraction_feedback")) for s in history]
    return outcome, human, status, values, steps, feedback, duration_ms


def measure(llm, crag, pace, store, contract, run) -> dict:
    pace.wait()
    thread = f"llm-jeu-{contract.id}-{run}"
    outcome, human, status, values, steps, feedback, duration_ms = analyse(
        llm, crag, store, contract, thread
    )
    usage = values.get("usage", [])
    report = values.get("failure_report") or {}
    explanation = values.get("explanation")
    tokens = tokens_by_model(usage)
    line = {
        "contrat": contract.id,
        "profil": contract.profile,
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
        # motifs de refus de chaque extraction refusée, dans l'ordre (J5, série 7)
        "refus_extraction": extraction_refusals(feedback, report or None),
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
    assert entry.record["decision"]["profile"] == contract.profile, line
    assert audit.replay(entry.record, CONFIG.model_dump(mode="json")).identical
    return line


@pytest.mark.parametrize("profile", PREALABLES)
def test_essai_prealable_non_compte(llm, real_crag, pace, audit_journal, profile):
    """Un contrat du profil, une fois : vérifie le banc (clé, base, corpus indexé,
    modèles) avant la série. Consigné au journal, jamais compté. L'issue attendue est
    exigée : le 01/10, un préalable qui ne vérifiait que l'invariant passait alors que
    tout le jeu escaladait (CRAG en échec)."""
    line = measure(
        llm,
        real_crag,
        pace,
        audit_journal,
        BY_ID[PREALABLES[profile]],
        "prealable",
    )
    assert line["classement"] == "conforme", line


@pytest.mark.parametrize("contract", CONTRACTS, ids=IDS)
@pytest.mark.parametrize("run", RUNS)
def test_jeu_reel(llm, real_crag, pace, series, audit_journal, run, contract):
    line = measure(llm, real_crag, pace, audit_journal, contract, run)
    series.setdefault(contract.profile, []).append(line)

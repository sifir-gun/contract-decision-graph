"""explain dans le graphe compilé : explication dans l'état et scellée avec sa source et ses
motifs, hors de la partie décision ; reprise sur erreur passagère, puis gabarit ;
l'explication ne bloque jamais le scellement. Doublures, journal en mémoire."""

from doubles import (
    ANALYSIS_DATE,
    CONTRACT_TEXT,
    FakeLLM,
    FixedExtractor,
    MemoryAuditStore,
    clauses,
    context,
    faithful_explanation,
    make_deps,
)
from langgraph.checkpoint.memory import InMemorySaver

from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.application.deps import TemplateOnly
from cdg.application.explanation import LLMExplainer
from cdg.domain import explanation
from cdg.domain.config import load_config
from cdg.ports.llm import LLMTransientError

CONFIG = load_config()
# reprise immédiate : les tests n'attendent pas
FAST = CONFIG.model_copy(
    update={
        "explain_retry": CONFIG.explain_retry.model_copy(
            update={"max_attempts": 2, "initial_interval_seconds": 0.0}
        )
    }
)
# juridique 0,5 : un constat, GO direct (0,85)
FOUND = clauses(responsabilite_fournisseur=50)


def transient(user: str) -> dict:
    raise LLMTransientError("503 : service indisponible")


def run(explainer, config=CONFIG, cid="c-explain"):
    store = MemoryAuditStore()
    deps = make_deps(FixedExtractor(FOUND), audit_store=store, explainer=explainer)
    graph = orchestrator.build_graph(config, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    graph.invoke(
        {
            "contract_id": cid,
            "raw_text": CONTRACT_TEXT,
            "analysis_date": ANALYSIS_DATE,
            **context(config),
        },
        {"configurable": {"thread_id": cid}},
    )
    [entry] = store.entries()
    return graph, entry


def test_explication_llm_dans_l_etat_et_scellee_avec_sa_source():
    llm = FakeLLM({"explain": faithful_explanation}, tokens=(300, 90))
    graph, entry = run(LLMExplainer(llm))
    status = orchestrator.thread_status(graph, "c-explain")
    sealed = entry.record["explanation"]
    assert status["explanation"] == sealed
    assert (sealed["source"], sealed["decision"], sealed["attempts"]) == (
        "llm",
        "GO",
        1,
    )
    assert [(f["id"], f["kind"], f["references"]) for f in sealed["findings"]] == [
        ("juridique-1", "responsabilite_fournisseur", ["juridique-ref-1"])
    ]
    # consommation d'explain scellée, hors de la partie décision
    assert {"node": "explain", "tokens_in": 300}.items() <= next(
        u for u in entry.record["usage"] if u["node"] == "explain"
    ).items()
    assert "explanation" not in entry.record["decision"]


def test_meme_decision_quelle_que_soit_l_explication():
    _, by_llm = run(LLMExplainer(FakeLLM({"explain": faithful_explanation})), cid="c-a")
    _, by_template = run(TemplateOnly("doublure : gabarit"), cid="c-b")
    assert by_llm.record["explanation"]["source"] == "llm"
    assert by_template.record["explanation"]["source"] == "gabarit"
    assert by_llm.decision_hash == by_template.decision_hash


def test_erreur_passagere_reprise_puis_gabarit_scelle():
    llm = FakeLLM({"explain": transient})
    _, entry = run(LLMExplainer(llm), config=FAST)
    assert len(llm.calls) == 2  # deux tentatives du nœud (explain_retry)
    sealed = entry.record["explanation"]
    assert (sealed["source"], sealed["reasons"]) == (
        "gabarit",
        ["essai 1 : LLMTransientError : 503 : service indisponible"],
    )
    assert entry.record["failures"] == []  # repli tracé dans l'explication, pas d'échec
    assert entry.record["decision"]["final_decision"] == "GO"


def test_erreur_passagere_reprise_puis_acceptee():
    llm = FakeLLM({"explain": [transient, faithful_explanation]})
    _, entry = run(LLMExplainer(llm), config=FAST)
    sealed = entry.record["explanation"]
    assert (sealed["source"], sealed["attempts"], sealed["reasons"]) == ("llm", 1, [])


def test_explication_jamais_bloquante_erreur_quelconque_puis_scellement():
    def broken(user):
        raise RuntimeError("panne inattendue")

    _, entry = run(LLMExplainer(FakeLLM({"explain": broken})))
    sealed = entry.record["explanation"]
    assert (sealed["source"], sealed["reasons"]) == (
        "gabarit",
        ["essai 1 : RuntimeError : panne inattendue"],
    )


def test_explication_scellee_relue_par_le_modele():
    _, entry = run(TemplateOnly("doublure : gabarit"))
    sealed = explanation.Explanation.model_validate(entry.record["explanation"])
    assert sealed.reasons == ["doublure : gabarit"]

"""explain dans le graphe compilé : explication dans l'état et scellée avec sa source et ses
motifs, hors de la partie décision ; reprise sur erreur passagère, puis gabarit ;
l'explication ne bloque jamais le scellement. Doublures, journal en mémoire."""

import pytest
from corpus_de_test import CODE_A_REF, edit_sources, write_homonyms
from doubles import (
    ANALYSIS_DATE,
    CONTRACT_TEXT,
    FaithfulExplainer,
    FakeCrag,
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


def run(explainer, config=CONFIG, cid="c-explain", crag=None, **initial):
    store = MemoryAuditStore()
    deps = make_deps(
        FixedExtractor(FOUND), crag=crag, audit_store=store, explainer=explainer
    )
    graph = orchestrator.build_graph(config, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    graph.invoke(
        {
            "contract_id": cid,
            "raw_text": CONTRACT_TEXT,
            "analysis_date": ANALYSIS_DATE,
            **context(config),
            **initial,
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


def test_explication_recoit_le_profil_de_l_etat_dans_le_graphe():
    # état sans clé `profile` (d'avant les profils) : le seul profil possible alors
    without, with_profile = FaithfulExplainer(), FaithfulExplainer()
    _, entry = run(without, cid="c-sans-profil")
    run(with_profile, cid="c-profil", profile="fournisseur")
    assert without.profiles == with_profile.profiles == ["fournisseur"]
    assert entry.record["explanation"]["source"] == "llm"


def citing(text):
    """Réponse du LLM d'explication : chaque constat recopié, le premier expliqué par
    `text`."""

    def answer(user):
        faithful = faithful_explanation(user)
        faithful["findings"][0]["text"] = text
        return faithful

    return answer


def test_noms_des_sources_lus_dans_le_manifeste_a_la_construction(corpus_synthetique):
    # corpus de test réduit à deux homonymes (code A, code B, art. L1-1) : seul
    # « Code A, art. L1-1 » est retenu ; le contrôle reçoit les noms de leur manifeste
    write_homonyms(corpus_synthetique)
    crag = FakeCrag(reference=CODE_A_REF)
    wrong = LLMExplainer(FakeLLM({"explain": citing("Voir l'article L1-1 du code B.")}))
    _, refused = run(wrong, crag=crag, cid="c-code-b")
    sealed = refused.record["explanation"]
    assert sealed["source"] == "gabarit"
    assert sealed["reasons"][0] == (
        "essai 1 : juridique-1 : article cité hors des références retenues pour la "
        "clause responsabilite_fournisseur : L1-1 (code-b)"
    )
    right = LLMExplainer(FakeLLM({"explain": citing("Voir l'article L1-1 du code A.")}))
    _, accepted = run(right, crag=crag, cid="c-code-a")
    assert accepted.record["explanation"]["source"] == "llm"


def test_noms_ambigus_le_graphe_ne_se_construit_pas(corpus_synthetique):
    # deux sources au même nom : aucune citation ne s'y rattacherait sûrement ; refusé
    # avant toute analyse, à la construction du graphe
    write_homonyms(corpus_synthetique)
    edit_sources(
        corpus_synthetique, lambda sources: sources["code-b"].update(citation="Code A")
    )
    with pytest.raises(ValueError, match="nom de source commun à code-a et code-b"):
        orchestrator.build_graph(CONFIG, make_deps())

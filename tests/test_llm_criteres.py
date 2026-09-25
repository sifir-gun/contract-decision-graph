"""Critères 3 et 10 avec le vrai modèle (fournisseur de la configuration). Payant.

Lancement : `uv run pytest --llm -m llm -s`. Chaque critère est répété 5 fois : 5 réussites
sur 5 exigées, sans relance automatique. Chaque essai imprime une ligne `LLM-RESULT` (JSON),
et la série du critère 10 une ligne `LLM-SERIE` (taux d'aboutissement aux analystes),
reportées au journal avec la date et les modèles.
"""

import json
from pathlib import Path

import pytest
from doubles import ANALYSIS_DATE, CONTRACT_TEXT, FakeCrag, FixedExtractor, clauses
from langgraph.checkpoint.memory import InMemorySaver

from cdg import settings
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.adapters.llm import build_provider
from cdg.application import ingestion
from cdg.application.deps import Deps
from cdg.application.extraction import LLMExtractor
from cdg.domain.config import load_config
from cdg.domain.verification import problems_of
from cdg.ports.retriever import Passage

pytestmark = pytest.mark.llm

CONFIG = load_config()
RUNS = range(1, 6)
CONTRACT = (Path(__file__).parent / "fixtures" / "contrat-synthetique-llm.txt").read_text(
    encoding="utf-8"
)
PARTIES = ["Alpha Services Synthétiques", "Bêta Achats Synthétiques"]


@pytest.fixture(scope="module")
def llm():
    settings.load_env()
    return build_provider(CONFIG.llm)  # clé absente : SettingsError, échec explicite


def report(criterion: str, run: int, **fields) -> None:
    line = {"critere": criterion, "essai": run, "fournisseur": CONFIG.llm.provider, **fields}
    print("LLM-RESULT " + json.dumps(line, ensure_ascii=False, default=str))


def compiled(deps: Deps):
    return orchestrator.build_graph(CONFIG, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )


# --- Critère 10 : aucun analyste sur une citation non vérifiée -------------------------

# Contrat valide : chaque clause stipulée se cite mot pour mot ; les pénalités de retard
# n'y figurent pas, une extraction correcte les déclare absentes. Valeurs attendues :
# pour information (écarts consignés), le critère porte sur les citations.
EXPECTED = {
    "responsabilite_acheteur": (True, 100.0, None),
    "responsabilite_fournisseur": (True, 150.0, None),
    "revision_prix": (True, 3.0, None),
    "penalites_retard": (False, None, None),
    "duree_engagement": (True, 24.0, None),
    "preavis_resiliation": (True, 3.0, None),
    "donnees_personnelles": (True, None, None),
    "accord_traitement_donnees": (True, None, None),
    "transfert_hors_ue": (True, None, "sans_transfert"),
}


@pytest.fixture(scope="module")
def serie_10():
    """Mesure de la série : essais qui aboutissent aux analystes, citations toutes
    vérifiées. Un système qui escaladerait toujours passerait le critère avec 0/5 ici.
    Pas de seuil pour l'instant : le taux est consigné au journal."""
    outcomes: list[dict] = []
    yield outcomes
    reached = [o for o in outcomes if o["issue"] == "analystes"]
    summary = {
        "critere": "10",
        "mesure": "aboutissement aux analystes, citations toutes vérifiées",
        "taux": f"{len(reached)}/{len(outcomes)}",
        "au_premier_essai": sum(o["essais_extraction"] == 1 for o in reached),
        "issues": [o["issue"] for o in outcomes],
        "extractions_exactes": sum(not o["ecarts_de_valeur"] for o in reached),
    }
    print("\nLLM-SERIE " + json.dumps(summary, ensure_ascii=False))


def value_gaps(clauses) -> list[str]:
    extracted = {c.kind: c for c in clauses}
    gaps = []
    for kind, expected in EXPECTED.items():
        c = extracted.get(kind)
        got = (c.present, c.value, c.category) if c else None
        if got != expected:
            gaps.append(f"{kind} : attendu {expected}, obtenu {got}")
    return gaps


@pytest.mark.parametrize("run", RUNS)
def test_10_vrai_modele_aucune_citation_non_verifiee(llm, serie_10, run):
    """L'extraction réelle ne doit mener aux analystes qu'avec des citations toutes
    retrouvées mot pour mot dans le texte masqué ; sinon ré-extraction avec retour ciblé,
    puis ESCALADE avec rapport d'échec. L'issue de chaque essai entre dans la mesure."""
    graph = compiled(Deps(extractor=LLMExtractor(llm), crag=FakeCrag()))
    thread = f"llm-10-{run}"
    orchestrator.run_contract(graph, thread, CONTRACT, PARTIES, analysis_date=ANALYSIS_DATE)
    values = graph.get_state({"configurable": {"thread_id": thread}}).values
    failures = values.get("failures", [])
    if failures:
        outcome = "echec_de_noeud"
    elif values["verdicts"]:
        outcome = "analystes"
    else:
        outcome = "escalade"
    line = {
        "issue": outcome,
        "essais_extraction": values.get("extraction_attempts", 0),
        "retours": values.get("extraction_feedback", []),
        "ecarts_de_valeur": value_gaps(values.get("clauses", [])),
        "echecs": [f"{f.node} : {f.message[:120]}" for f in failures],
        "tokens": sum(u.tokens_in + u.tokens_out for u in values.get("usage", [])),
    }
    serie_10.append(line)
    report("10", run, modele=CONFIG.llm.model("main"), **line)

    assert failures == []  # le vrai modèle a répondu, rien n'a échoué
    if outcome == "analystes":  # les analystes ont tourné : tout est vérifié
        assert problems_of(values["raw_text"], values["clauses"]) == []
    else:
        assert values["proposed_decision"] == "ESCALADE"
        assert values["failure_report"]["stage"] == "extraction"
        assert values["failure_report"]["attempts"] == CONFIG.extraction.max_attempts
    assert "Alpha" not in values["raw_text"] and "Bêta" not in values["raw_text"]


# --- Critère 3 : CRAG hors corpus, INSUFFISANT puis ESCALADE, rien d'inventé ------------


def _real_passages(*keys, domain):
    """Extraits réels du corpus (textes publics, fiches), sans base ni embedding."""
    articles = {(a.source_id, a.article): a for a, _ in ingestion.articles()}
    fiches = {f.id: f for f in ingestion.load_fiches()}
    passages = []
    for n, key in enumerate(keys, start=1):
        if key in fiches:
            reference, text = f"Fiche projet : {fiches[key].title}", fiches[key].body
        else:
            reference, text = articles[key].reference, articles[key].text
        passages.append(
            Passage(
                id=n,
                domain=domain,
                source_id=key if isinstance(key, str) else key[0],
                reference=reference,
                text=text,
                distance=0.2,
            )
        )
    return passages


class FixedRetriever:
    """Retriever qui rend toujours les mêmes extraits réels, quelle que soit la requête."""

    def __init__(self, passages: list[Passage]):
        self.passages, self.queries = passages, []

    def search(self, domain, query, *, k):
        self.queries.append(query)
        return self.passages[:k]


class ByDomain:
    """CRAG réel pour certains domaines, doublure OK pour les autres (coût réduit)."""

    def __init__(self, real: dict):
        self.real, self.fake = real, FakeCrag()

    def __call__(self, domain, clauses, analysis_date):
        crag = self.real.get(domain, self.fake)
        return crag(domain, clauses, analysis_date)


@pytest.mark.parametrize("run", RUNS)
def test_3_vrai_juge_hors_corpus_insuffisant_puis_escalade(llm, run):
    """Financier : la recherche ne rend que des extraits hors sujet (sécurité des données,
    résiliation) ; le vrai juge doit les écarter aux 2 passes, d'où INSUFFISANT puis
    ESCALADE, sans référence. Témoin : juridique, extraits pertinents, retenus."""
    off_topic = _real_passages(
        ("rgpd", "32"), ("rgpd", "33"), ("code-civil", "1211"), domain="financier"
    )
    on_topic = _real_passages(
        "fiche-responsabilite-plafonds",
        ("code-civil", "1231-3"),
        ("code-civil", "1170"),
        domain="juridique",
    )
    financier, juridique = FixedRetriever(off_topic), FixedRetriever(on_topic)
    crag = ByDomain(
        {
            "financier": orchestrator.crag_runner(financier, llm, CONFIG),
            "juridique": orchestrator.crag_runner(juridique, llm, CONFIG),
        }
    )
    # extraction en doublure (citations de CONTRACT_TEXT) : seul le CRAG appelle le modèle
    graph = compiled(Deps(extractor=FixedExtractor(clauses()), crag=crag))
    thread = f"llm-3-{run}"
    orchestrator.run_contract(graph, thread, CONTRACT_TEXT, analysis_date=ANALYSIS_DATE)
    values = graph.get_state({"configurable": {"thread_id": thread}}).values
    by_domain = {v.domain: v for v in values["verdicts"]}

    assert values.get("failures", []) == []
    fin = by_domain["financier"]
    assert fin.retrieval_status == "INSUFFISANT" and fin.evidence_ids == []
    assert fin.retrieval.passes == CONFIG.crag.max_passes and len(financier.queries) == 2
    assert (values["proposed_decision"], values["route"]) == ("ESCALADE", "human_review")
    # aucune réponse inventée : toute référence retenue a été rendue par la recherche
    returned = {"financier": off_topic, "juridique": on_topic}
    for verdict in values["verdicts"]:
        if verdict.domain in returned:
            assert set(verdict.evidence_ids) <= {p.reference for p in returned[verdict.domain]}
    jur = by_domain["juridique"]
    assert jur.retrieval_status == "OK" and jur.evidence_ids  # témoin : le juge n'écarte pas tout
    report(
        "3",
        run,
        modele=CONFIG.llm.model("light"),
        financier={"statut": fin.retrieval_status, "requetes": fin.retrieval.queries},
        juridique_temoin={"statut": jur.retrieval_status, "retenues": jur.evidence_ids},
        decision=values["proposed_decision"],
        tokens=sum(u.tokens_in + u.tokens_out for u in values["usage"]),
    )

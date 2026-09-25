"""Critères 3 et 10 avec le vrai modèle (fournisseur de la configuration). Payant.

Lancement : `uv run pytest --llm -m llm -s`. Chaque critère est répété 5 fois : 5 réussites
sur 5 exigées, sans relance automatique. Chaque essai imprime une ligne `LLM-RESULT` (JSON),
reportée au journal avec la date et les modèles.
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
from cdg.application.nodes.verify_extraction import problems_of
from cdg.domain.config import load_config
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


@pytest.mark.parametrize("run", RUNS)
def test_10_vrai_modele_aucune_citation_non_verifiee(llm, run):
    """Contrat sans clause de pénalités de retard : l'extraction réelle ne doit mener aux
    analystes qu'avec des citations toutes retrouvées mot pour mot dans le texte masqué ;
    sinon ré-extraction avec retour ciblé, puis ESCALADE avec rapport d'échec."""
    graph = compiled(Deps(extractor=LLMExtractor(llm), crag=FakeCrag()))
    thread = f"llm-10-{run}"
    orchestrator.run_contract(graph, thread, CONTRACT, PARTIES, analysis_date=ANALYSIS_DATE)
    values = graph.get_state({"configurable": {"thread_id": thread}}).values

    assert values.get("failures", []) == []  # le vrai modèle a répondu, rien n'a échoué
    attempts = values["extraction_attempts"]
    extracted = {c.kind: c for c in values["clauses"]}
    if values["verdicts"]:  # les analystes ont tourné : tout est vérifié
        assert problems_of(values["raw_text"], values["clauses"]) == []
        outcome = "analystes"
    else:
        assert values["proposed_decision"] == "ESCALADE"
        assert values["failure_report"]["stage"] == "extraction"
        assert values["failure_report"]["attempts"] == CONFIG.extraction.max_attempts
        outcome = "escalade"
    assert "Alpha" not in values["raw_text"] and "Bêta" not in values["raw_text"]
    report(
        "10",
        run,
        modele=CONFIG.llm.model("main"),
        issue=outcome,
        essais_extraction=attempts,
        retours=values.get("extraction_feedback", []),
        penalites_retard_presente=extracted["penalites_retard"].present,
        clauses_presentes=sorted(k for k, c in extracted.items() if c.present),
        tokens=sum(u.tokens_in + u.tokens_out for u in values["usage"]),
    )


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

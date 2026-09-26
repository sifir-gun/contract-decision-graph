"""Critères 3 et 10 avec le vrai modèle (fournisseur de la configuration). Payant.

Lancement : `uv run pytest --llm -m llm -s`. Chaque critère est répété 5 fois : 5 réussites
sur 5 exigées, sans relance automatique. Chaque essai imprime une ligne `LLM-RESULT` (JSON),
et la série du critère 10 une ligne `LLM-SERIE` (taux d'aboutissement aux analystes, seuil de
4 sur 5 par contrat), reportées au journal avec la date et les modèles.
"""

import json
import time
from pathlib import Path

import pytest
from doubles import ABSENT, ANALYSIS_DATE, CONTRACT_TEXT, FakeCrag, FixedExtractor, clauses
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
FIXTURES = Path(__file__).parent / "fixtures"
CONTRACT = (FIXTURES / "contrat-synthetique-llm.txt").read_text(encoding="utf-8")
PARTIES = ["Alpha Services Synthétiques", "Bêta Achats Synthétiques"]
CONTRACT_FULL = (FIXTURES / "contrat-synthetique-complet.txt").read_text(encoding="utf-8")
PARTIES_FULL = ["Gamma Transports Synthétiques", "Delta Industries Synthétiques"]


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

# Deux contrats de mesure, mesurés séparément. Valeurs attendues (présence, valeur,
# catégorie) : pour information, écarts consignés ; le critère porte sur les citations.
# - « valide » : chaque clause stipulée se cite mot pour mot ; ni pénalités d'exécution ni
#   délai de paiement n'y figurent, une extraction correcte les déclare absents ;
# - « complet » : les 10 types présents, et un piège, des pénalités de retard de paiement
#   dues par l'acheteur, qui ne sont ni des pénalités d'exécution ni un délai de paiement.
EXPECTED = {
    "responsabilite_acheteur": (True, 100.0, None),
    "responsabilite_fournisseur": (True, 150.0, None),
    "revision_prix": (True, 3.0, None),
    "penalites_execution": (False, None, None),
    "delai_paiement": (False, None, None),
    "duree_engagement": (True, 24.0, None),
    "preavis_resiliation": (True, 3.0, None),
    "donnees_personnelles": (True, None, None),
    "accord_traitement_donnees": (True, None, None),
    "transfert_hors_ue": (True, None, "sans_transfert"),
}
EXPECTED_FULL = {
    "responsabilite_acheteur": (True, 100.0, None),
    "responsabilite_fournisseur": (True, 120.0, None),
    "revision_prix": (True, 2.0, None),
    "penalites_execution": (True, 8.0, None),
    "delai_paiement": (True, 45.0, "fin_de_mois"),
    "duree_engagement": (True, 36.0, None),
    "preavis_resiliation": (True, 6.0, None),
    "donnees_personnelles": (True, None, None),
    "accord_traitement_donnees": (True, None, None),
    "transfert_hors_ue": (True, None, "clauses_contractuelles_types"),
}
CONTRACTS = {
    "valide": (CONTRACT, PARTIES, EXPECTED),
    "complet": (CONTRACT_FULL, PARTIES_FULL, EXPECTED_FULL),
}


# seuil du taux d'aboutissement aux analystes, par contrat (décision du 26/09/2026) ;
# l'invariant de sûreté, lui, reste exigé à chaque essai : 5 sur 5
MIN_REACHED = 4

# limite du compte pour le modèle principal (console Mistral, 25/09/2026) ; l'API ne compte
# que les tokens consommés, max_tokens n'est pas réservé (mesuré le même jour)
ACCOUNT_TOKENS_PER_MINUTE = 20_000


class Pacer:
    """Cadence des extractions : après un essai qui a consommé t tokens, attendre
    t × 60 / limite secondes avant le suivant. Pas une relance : seulement un espacement,
    pour ne pas provoquer soi-même un 429."""

    def __init__(self, tokens_per_minute: int):
        self.tokens_per_minute, self.next_at = tokens_per_minute, 0.0

    def wait(self) -> None:
        delay = self.next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def consumed(self, tokens: int) -> None:
        self.next_at = time.monotonic() + tokens * 60 / self.tokens_per_minute


@pytest.fixture(scope="module")
def pace():
    return Pacer(ACCOUNT_TOKENS_PER_MINUTE)


@pytest.fixture(scope="module")
def series_10():
    """Mesure de la série, par contrat : essais qui aboutissent aux analystes, citations
    toutes vérifiées. Un système qui escaladerait toujours passerait l'invariant avec 0/5 :
    d'où le seuil, vérifié par `test_10_taux_d_aboutissement_aux_analystes`. Le taux est
    consigné au journal."""
    outcomes: dict[str, list[dict]] = {name: [] for name in CONTRACTS}
    yield outcomes
    for name, lines in outcomes.items():
        if not lines:
            continue
        reached = [o for o in lines if o["issue"] == "analystes"]
        summary = {
            "critere": "10",
            "contrat": name,
            "mesure": "aboutissement aux analystes, citations toutes vérifiées",
            "taux": f"{len(reached)}/{len(lines)}",
            "au_premier_essai": sum(o["essais_extraction"] == 1 for o in reached),
            "issues": [o["issue"] for o in lines],
            "extractions_exactes": sum(not o["ecarts_de_valeur"] for o in reached),
        }
        print("\nLLM-SERIE " + json.dumps(summary, ensure_ascii=False))


def value_gaps(clauses, expected: dict) -> list[str]:
    extracted = {c.kind: c for c in clauses}
    gaps = []
    for kind, wanted in expected.items():
        c = extracted.get(kind)
        got = (c.present, c.value, c.category) if c else None
        if got != wanted:
            gaps.append(f"{kind} : attendu {wanted}, obtenu {got}")
    return gaps


@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("run", RUNS)
def test_10_vrai_modele_aucune_citation_non_verifiee(llm, series_10, pace, contract, run):
    """L'extraction réelle ne doit mener aux analystes qu'avec des citations toutes
    retrouvées mot pour mot dans le texte masqué ; sinon ré-extraction avec retour ciblé,
    puis ESCALADE avec rapport d'échec. L'issue de chaque essai entre dans la mesure."""
    text, parties, expected = CONTRACTS[contract]
    pace.wait()
    graph = compiled(Deps(extractor=LLMExtractor(llm), crag=FakeCrag()))
    thread = f"llm-10-{contract}-{run}"
    orchestrator.run_contract(graph, thread, text, parties, analysis_date=ANALYSIS_DATE)
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
        "ecarts_de_valeur": value_gaps(values.get("clauses", []), expected),
        "echecs": [f"{f.node} : {f.message[:120]}" for f in failures],
        "tokens": sum(u.tokens_in + u.tokens_out for u in values.get("usage", [])),
    }
    pace.consumed(line["tokens"])
    series_10[contract].append(line)
    report("10", run, contrat=contract, modele=CONFIG.llm.model("main"), **line)

    assert failures == []  # le vrai modèle a répondu, rien n'a échoué
    if outcome == "analystes":  # les analystes ont tourné : tout est vérifié
        assert problems_of(values["raw_text"], values["clauses"]) == []
    else:
        assert values["proposed_decision"] == "ESCALADE"
        assert values["failure_report"]["stage"] == "extraction"
        assert values["failure_report"]["attempts"] == CONFIG.extraction.max_attempts
    for party in parties:  # masquées avant le graphe
        assert party.split()[0] not in values["raw_text"]


@pytest.mark.parametrize("contract", CONTRACTS)
def test_10_taux_d_aboutissement_aux_analystes(series_10, contract):
    """Seuil de la mesure : au moins 4 essais sur 5 aboutissent aux analystes, citations
    toutes vérifiées. Jugé sur la série qui vient de tourner (définie plus haut, donc
    exécutée avant) ; une série incomplète ne peut pas être jugée : échec explicite."""
    lines = series_10[contract]
    if len(lines) != len(RUNS):
        pytest.fail(
            f"série {contract} incomplète : {len(lines)} essai(s) sur {len(RUNS)}, "
            "taux d'aboutissement non jugé",
            pytrace=False,
        )
    reached = sum(o["issue"] == "analystes" for o in lines)
    assert reached >= MIN_REACHED, (
        f"contrat {contract} : {reached}/{len(RUNS)} essais aboutissent aux analystes, "
        f"seuil {MIN_REACHED}/{len(RUNS)}"
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


# Règles d'abord : le CRAG ne cherche que pour les clauses qui portent un constat. Financier :
# deux pénalités (pénalités d'exécution absentes, délai de 90 jours date de facture), la
# révision plafonnée n'a rien à justifier. Juridique, témoin : plafond fournisseur à 50 %.
FLAGGED_3 = {
    "penalites_execution": ABSENT,
    "delai_paiement": 90.0,
    "responsabilite_fournisseur": 50.0,
}


@pytest.mark.parametrize("run", RUNS)
def test_3_vrai_juge_hors_corpus_insuffisant_puis_escalade(llm, run):
    """Financier : la recherche ne rend que des extraits hors sujet (sécurité des données,
    résiliation) ; le vrai juge doit les écarter aux 2 passes pour chaque clause qui porte
    un constat, d'où INSUFFISANT puis ESCALADE, sans référence. Témoin : juridique, extraits
    pertinents, retenus pour la clause qui porte un constat."""
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
    graph = compiled(Deps(extractor=FixedExtractor(clauses(**FLAGGED_3)), crag=crag))
    thread = f"llm-3-{run}"
    orchestrator.run_contract(graph, thread, CONTRACT_TEXT, analysis_date=ANALYSIS_DATE)
    values = graph.get_state({"configurable": {"thread_id": thread}}).values
    by_domain = {v.domain: v for v in values["verdicts"]}

    assert values.get("failures", []) == []
    fin = by_domain["financier"]
    assert fin.retrieval_status == "INSUFFISANT" and fin.evidence_ids == []
    # une requête par clause qui porte un constat, chacune épuise ses passes
    assert [c.kind for c in fin.retrieval.clauses] == ["penalites_execution", "delai_paiement"]
    assert all(c.passes == CONFIG.crag.max_passes for c in fin.retrieval.clauses)
    assert len(financier.queries) == CONFIG.crag.max_passes * 2
    lacking = [f for f in fin.findings if f.startswith("référentiel insuffisant")]
    assert [f.rsplit(" ", 1)[-1] for f in lacking] == ["penalites_execution", "delai_paiement"]
    assert (values["proposed_decision"], values["route"]) == ("ESCALADE", "human_review")
    # aucune réponse inventée : toute référence retenue a été rendue par la recherche
    returned = {"financier": off_topic, "juridique": on_topic}
    for verdict in values["verdicts"]:
        if verdict.domain in returned:
            assert set(verdict.evidence_ids) <= {p.reference for p in returned[verdict.domain]}
    jur = by_domain["juridique"]
    # témoin : le juge n'écarte pas tout, la clause qui porte un constat est justifiée
    assert [c.kind for c in jur.retrieval.clauses] == ["responsabilite_fournisseur"]
    assert jur.retrieval_status == "OK" and all(c.retained for c in jur.retrieval.clauses)
    report(
        "3",
        run,
        modele=CONFIG.llm.model("light"),
        financier={c.kind: c.queries for c in fin.retrieval.clauses},
        juridique_temoin={c.kind: c.retained for c in jur.retrieval.clauses},
        decision=values["proposed_decision"],
        tokens=sum(u.tokens_in + u.tokens_out for u in values["usage"]),
    )

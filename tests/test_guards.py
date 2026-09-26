"""Gardes d'échec de nœud (J3 tâche 10) : jamais de repli silencieux.

Un nœud en échec produit un `NodeFailure` dans `failures` : un analyste en échec ne fait
pas échouer le fan-out, `decision_gate` escalade ; un nœud à plusieurs sorties route vers
l'humain. Les erreurs transitoires d'un analyste sont d'abord reprises par RetryPolicy.
"""

import pytest
from doubles import (
    ANALYSIS_DATE,
    CONTRACT_TEXT,
    FakeCrag,
    FixedExtractor,
    clauses,
    context,
    make_deps,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.pregel._retry import _should_retry_on  # règle de référence
from langgraph.types import RetryPolicy

from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.domain.config import load_config
from cdg.domain.models import DOMAINS, NodeFailure
from cdg.ports.llm import LLMOutputError, LLMQuotaError, LLMTransientError

CONFIG = load_config()
# reprises immédiates : les tests ne dorment pas
FAST = CONFIG.model_copy(
    update={
        "analyst_retry": CONFIG.analyst_retry.model_copy(
            update={"initial_interval_seconds": 0.0}
        ),
        "extraction_retry": CONFIG.extraction_retry.model_copy(
            update={"initial_interval_seconds": 0.0}
        ),
    }
)


class FlakyCrag(FakeCrag):
    """CRAG qui échoue pour un domaine, selon une liste d'erreurs consommées dans l'ordre."""

    def __init__(self, errors: dict):
        super().__init__()
        self.errors = {d: list(e) for d, e in errors.items()}

    def __call__(self, domain, clauses, analysis_date):
        pending = self.errors.get(domain, [])
        if pending:
            error = (
                pending.pop(0) if len(pending) > 1 else pending[0]
            )  # la dernière persiste
            if error is not None:
                self.calls.append(domain)
                raise error
        return super().__call__(domain, clauses, analysis_date)


def run(deps, config=FAST, contract_id="c-garde"):
    graph = orchestrator.build_graph(config, deps).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    thread = {"configurable": {"thread_id": contract_id}}
    graph.invoke(
        {
            "contract_id": contract_id,
            "raw_text": CONTRACT_TEXT,
            "analysis_date": ANALYSIS_DATE,
            **context(config),
        },
        thread,
    )
    return graph.get_state(thread)


def deps(crag=None, extractor=None, audit_store=None):
    return make_deps(extractor, crag, audit_store)


# --- Analystes : un en panne, les 3 autres verdicts gardés, escalade ----------------------


def test_un_analyste_en_panne_escalade_et_les_trois_autres_verdicts_sont_gardes():
    crag = FlakyCrag({"financier": [ValueError("index corrompu")]})
    snapshot = run(deps(crag))
    values = snapshot.values
    assert sorted(v.domain for v in values["verdicts"]) == sorted(
        set(DOMAINS) - {"financier"}
    )
    [failure] = values["failures"]
    assert failure == NodeFailure(
        node="analyst",
        domain="financier",
        error="ValueError",
        message="index corrompu",
        attempts=1,
    )
    assert (values["proposed_decision"], values["route"]) == (
        "ESCALADE",
        "human_review",
    )
    assert values["failure_report"] == {
        "stage": "noeuds",
        "failures": [failure.model_dump()],
    }
    assert values.get("final_decision") is None
    [pending] = snapshot.interrupts  # l'humain voit le rapport d'échec
    assert pending.value["failure_report"]["failures"][0]["domain"] == "financier"


def test_erreur_transitoire_reprise_par_retry_policy_avant_la_garde():
    crag = FlakyCrag(
        {"financier": [ConnectionError("réseau"), ConnectionError("réseau"), None]}
    )
    values = run(deps(crag)).values
    assert CONFIG.analyst_retry.max_attempts == 3
    assert crag.calls.count("financier") == 3  # deux échecs, puis la réussite
    assert sorted(v.domain for v in values["verdicts"]) == sorted(DOMAINS)
    assert values.get("failures", []) == []


def test_erreur_transitoire_persistante_garde_apres_les_reprises():
    crag = FlakyCrag({"conformite": [ConnectionError("réseau")]})
    values = run(deps(crag)).values
    [failure] = values["failures"]
    assert (failure.domain, failure.error, failure.attempts) == (
        "conformite",
        "ConnectionError",
        3,
    )
    assert crag.calls.count("conformite") == 3
    assert values["proposed_decision"] == "ESCALADE"


def test_erreur_non_transitoire_sans_reprise():
    crag = FlakyCrag({"juridique": [ValueError("bogue")]})
    values = run(deps(crag)).values
    assert crag.calls.count("juridique") == 1
    assert values["failures"][0].attempts == 1


def test_deux_analystes_en_panne_deux_echecs_cumules():
    crag = FlakyCrag(
        {"juridique": [ValueError("a")], "operationnel": [ValueError("b")]}
    )
    values = run(deps(crag)).values
    assert sorted(f.domain for f in values["failures"]) == ["juridique", "operationnel"]
    assert len(values["verdicts"]) == 2


def test_blocage_dur_etabli_malgre_un_analyste_en_panne():
    # juridique bloque ; le financier en panne n'y change rien : NO_GO, rapport d'échec tracé
    crag = FlakyCrag({"financier": [ValueError("panne")]})
    values = run(
        deps(crag, FixedExtractor(clauses(responsabilite_acheteur=None)))
    ).values
    assert (values["proposed_decision"], values["final_decision"]) == ("NO_GO", "NO_GO")
    assert values["failure_report"]["stage"] == "noeuds"
    assert "margin" not in values  # agrégat incalculable sans les 4 verdicts


# --- Extraction en panne : vérification impossible, escalade ------------------------------


class BrokenExtractor(FixedExtractor):
    def __call__(self, raw_text, feedback):
        self.calls.append((raw_text, list(feedback)))
        raise LLMOutputError("mistral-small-2603 : réponse vide ou non structurée")


def test_extraction_en_panne_escalade_sans_analyste():
    extractor, crag = BrokenExtractor([]), FakeCrag()
    values = run(deps(crag, extractor)).values
    [failure] = values["failures"]
    assert (failure.node, failure.error) == ("extract_clauses", "LLMOutputError")
    assert len(extractor.calls) == 1  # pas de reprise sur l'extraction
    assert (values["proposed_decision"], values["route"]) == (
        "ESCALADE",
        "human_review",
    )
    assert crag.calls == [] and values["verdicts"] == []


# --- Nœuds à plusieurs sorties : route vers l'humain ------------------------------------


@pytest.mark.parametrize(
    "node", ["validate_input", "verify_extraction", "decision_gate"]
)
def test_noeud_a_route_en_panne_escalade_vers_l_humain(monkeypatch, node):
    def broken(state, decision_config):
        raise RuntimeError(f"panne de {node}")

    monkeypatch.setattr(orchestrator, node, broken)
    snapshot = run(deps())
    values = snapshot.values
    assert [(f.node, f.error) for f in values["failures"]] == [(node, "RuntimeError")]
    assert (values["proposed_decision"], values["route"]) == (
        "ESCALADE",
        "human_review",
    )
    assert values["failure_report"]["failures"][0]["message"] == f"panne de {node}"
    assert snapshot.next == ("human_review",)


# --- Nœuds à sortie unique après la décision : échec tracé, exécution menée à terme -------


@pytest.mark.parametrize("node", ["explain", "audit_seal"])
def test_noeud_final_en_panne_echec_trace(monkeypatch, node):
    def broken(state):
        raise RuntimeError(f"panne de {node}")

    monkeypatch.setattr(orchestrator, node, broken)
    snapshot = run(deps())
    values = snapshot.values
    assert values["final_decision"] == "GO"  # décision déjà rendue par decision_gate
    assert [f.node for f in values["failures"]] == [node]
    assert snapshot.next == ()


def test_rejet_en_panne_echec_trace(monkeypatch):
    def broken(state):
        raise RuntimeError("panne de reject")

    monkeypatch.setattr(orchestrator, "reject", broken)
    graph = orchestrator.build_graph(FAST, deps()).compile()
    out = graph.invoke(
        {
            "contract_id": "c",
            "raw_text": "  ",
            "analysis_date": ANALYSIS_DATE,
            **context(FAST),
        }
    )
    assert [f.node for f in out["failures"]] == ["reject"]


# --- La garde laisse passer le contrôle de LangGraph -----------------------------------


def test_la_garde_ne_capture_pas_une_interruption():
    def interrupted(state):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        orchestrator.guard("human_review", interrupted)({})


# --- Statut d'un thread : échecs exposés ---------------------------------------------


def test_statut_expose_les_echecs():
    crag = FlakyCrag({"financier": [ValueError("index corrompu")]})
    graph = orchestrator.build_graph(FAST, deps(crag)).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    status = orchestrator.run_contract(
        graph, "c-statut", CONTRACT_TEXT, analysis_date=ANALYSIS_DATE, config=FAST
    )
    assert status["statut"] == "suspendu"
    assert status["failures"] == [
        {
            "node": "analyst",
            "error": "ValueError",
            "message": "index corrompu",
            "attempts": 1,
            "domain": "financier",
        }
    ]


# --- Extraction : reprise sur erreur passagère seulement (429, 5xx, délai dépassé) -------


class FlakyExtractor(FixedExtractor):
    """Extraction qui échoue d'abord selon une liste d'erreurs, la dernière persistant."""

    def __init__(self, errors):
        super().__init__(clauses())
        self.errors = list(errors)

    def __call__(self, raw_text, feedback):
        if self.errors:
            error = self.errors.pop(0) if len(self.errors) > 1 else self.errors[0]
            if error is not None:
                self.calls.append((raw_text, list(feedback)))
                raise error
        return super().__call__(raw_text, feedback)


def test_extraction_erreur_passagere_reprise_avant_la_garde():
    transient = LLMTransientError("mistral-small-2603 : erreur passagère (HTTP 429)")
    extractor = FlakyExtractor([transient, transient, None])
    values = run(deps(extractor=extractor)).values
    assert CONFIG.extraction_retry.max_attempts == 3
    assert len(extractor.calls) == 3  # deux échecs, puis la réussite
    assert values.get("failures", []) == [] and len(values["verdicts"]) == 4


def test_extraction_erreur_passagere_persistante_escalade_apres_les_reprises():
    extractor = FlakyExtractor([LLMTransientError("HTTP 503")])
    values = run(deps(extractor=extractor)).values
    [failure] = values["failures"]
    assert (failure.node, failure.error, failure.attempts) == (
        "extract_clauses",
        "LLMTransientError",
        3,
    )
    assert len(extractor.calls) == 3 and values["proposed_decision"] == "ESCALADE"


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bogue"),
        ConnectionError("hors adaptateur : non traduite"),
        LLMQuotaError("quota nul sur le compte : vérifier l'offre du compte"),
    ],
)
def test_extraction_autre_erreur_sans_reprise(error):
    # seules les erreurs passagères traduites par l'adaptateur sont reprises (LLMTransientError)
    extractor = FlakyExtractor([error])
    values = run(deps(extractor=extractor)).values
    assert len(extractor.calls) == 1 and values["failures"][0].attempts == 1


def test_quota_nul_consigne_des_la_premiere_tentative():
    extractor = FlakyExtractor(
        [LLMQuotaError("quota nul : vérifier l'offre du compte")]
    )
    values = run(deps(extractor=extractor)).values
    [failure] = values["failures"]
    assert (failure.node, failure.error, failure.attempts) == (
        "extract_clauses",
        "LLMQuotaError",
        1,
    )
    assert "vérifier l'offre du compte" in failure.message
    assert values["proposed_decision"] == "ESCALADE"


def test_quota_nul_jamais_repris_sur_un_analyste():
    # la RetryPolicy des analystes reprend par défaut presque toute exception : pas celle-ci
    crag = FlakyCrag({"financier": [LLMQuotaError("quota nul")]})
    values = run(deps(crag)).values
    assert crag.calls.count("financier") == 1
    assert values["failures"][0].attempts == 1


# --- Règle de reprise de la garde : celle de LangGraph -------------------------------------


@pytest.mark.parametrize(
    "retry_on",
    [
        ValueError,  # une classe
        (ValueError, KeyError),  # une liste de classes
        lambda exc: isinstance(exc, KeyError),  # un prédicat
        RetryPolicy().retry_on,  # le prédicat par défaut de LangGraph
    ],
)
@pytest.mark.parametrize(
    "exc", [ValueError("v"), KeyError("k"), ConnectionError("c"), LLMQuotaError("q")]
)
def test_regle_de_reprise_identique_a_celle_de_langgraph(retry_on, exc):
    # la garde décide comme la RetryPolicy : sinon elle consignerait trop tôt, ou jamais
    policy = RetryPolicy(retry_on=retry_on)
    assert orchestrator.retries(policy, exc) == _should_retry_on(policy, exc)


def test_regle_de_reprise_classe_hors_exception_refusee():
    with pytest.raises(TypeError, match="classe d'exception attendue"):
        orchestrator.retries(RetryPolicy(retry_on=dict), ValueError())


# --- audit_seal en échec : consigné, l'exécution va à son terme ---------------------------


class BrokenStore:
    def append(self, seal):
        raise ConnectionError("base injoignable")

    def entries(self):
        return []


def test_audit_seal_en_echec_consigne_sans_empreinte():
    values = run(deps(audit_store=BrokenStore())).values
    [failure] = values["failures"]
    assert (failure.node, failure.error) == ("audit_seal", "ConnectionError")
    assert values["final_decision"] == "GO" and "chain_hash" not in values

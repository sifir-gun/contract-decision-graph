"""Schémas d'état et modèles métier (spec, « Schéma d'état »)."""

import operator
from datetime import date
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from cdg.domain.state import (
    CATEGORY_KINDS,
    DOMAIN_KINDS,
    DOMAINS,
    REQUIRED_KINDS,
    AgentVerdict,
    AnalystInput,
    Clause,
    ContractState,
    Decision,
    Domain,
    HumanDecision,
    NodeFailure,
    RetrievalTrace,
    Route,
    Usage,
)

# --- Clause -------------------------------------------------------------------


def test_clause_presente_avec_citation():
    c = Clause(kind="duree_engagement", present=True, quote="Durée : 24 mois.", value=24)
    assert c.value == 24.0


def test_clause_absente_citation_vide_et_valeur_nulle():
    c = Clause(kind="accord_traitement_donnees", present=False, quote="", value=None)
    assert not c.present


def test_clause_absente_refuse_une_citation():
    with pytest.raises(ValidationError, match="absente"):
        Clause(kind="penalites_retard", present=False, quote="texte", value=None)


@pytest.mark.parametrize("quote", ["", "   "])
def test_clause_presente_exige_une_citation(quote):
    with pytest.raises(ValidationError, match="présente"):
        Clause(kind="penalites_retard", present=True, quote=quote, value=5)


def test_clause_valeur_obligatoire_meme_nulle():
    with pytest.raises(ValidationError):
        Clause(kind="penalites_retard", present=False, quote="")


# --- AgentVerdict -------------------------------------------------------------


def _verdict(**overrides) -> dict:
    base = {
        "domain": "juridique",
        "score": 1.0,
        "hard_block": False,
        "findings": [],
        "evidence_ids": [],
        "retrieval_status": "OK",
    }
    return base | overrides


def test_verdict_valide():
    assert AgentVerdict(**_verdict(score=0.0)).score == 0.0


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_verdict_score_entre_0_et_1(score):
    with pytest.raises(ValidationError):
        AgentVerdict(**_verdict(score=score))


@pytest.mark.parametrize("field,value", [("domain", "fiscal"), ("retrieval_status", "PARTIEL")])
def test_verdict_valeurs_fermees(field, value):
    with pytest.raises(ValidationError):
        AgentVerdict(**_verdict(**{field: value}))


# --- HumanDecision, Usage -----------------------------------------------------


def test_decision_humaine_par_defaut_ne_leve_pas_de_blocage():
    h = HumanDecision(decision="NO_GO", reviewer="gt", reason="motif")
    assert h.overrides_block is False


@pytest.mark.parametrize("field", ["tokens_in", "tokens_out", "latency_ms"])
def test_usage_refuse_les_valeurs_negatives(field):
    base = {"node": "analyst", "model": "m", "tokens_in": 1, "tokens_out": 1, "latency_ms": 1}
    with pytest.raises(ValidationError):
        Usage(**(base | {field: -1}))


# --- Constantes et état ---------------------------------------------------------


def test_domaines_et_types_de_clauses():
    assert DOMAINS == ("juridique", "financier", "conformite", "operationnel")
    assert set(DOMAINS) == set(get_args(Domain))
    assert REQUIRED_KINDS == (
        "responsabilite_acheteur",
        "responsabilite_fournisseur",
        "revision_prix",
        "penalites_retard",
        "duree_engagement",
        "preavis_resiliation",
        "donnees_personnelles",
        "accord_traitement_donnees",
        "transfert_hors_ue",
    )
    assert CATEGORY_KINDS == {"transfert_hors_ue"}


def test_valeurs_de_route_et_de_decision():
    assert set(get_args(Route)) == {
        "extract_clauses",
        "reject",
        "analysts",
        "human_review",
        "explain",
    }
    assert set(get_args(Decision)) == {"GO", "GO_RESERVES", "NO_GO", "ESCALADE"}


def test_seuls_verdicts_usage_et_failures_ont_un_reducteur():
    hints = get_type_hints(ContractState, include_extras=True)
    reducers = {
        key
        for key, hint in hints.items()
        if any(meta is operator.add for meta in getattr(hint, "__metadata__", ()))
    }
    assert reducers == {"verdicts", "usage", "failures"}


def test_etat_prive_des_analystes():
    assert set(get_type_hints(AnalystInput)) == {"domain", "clauses", "analysis_date"}
    assert get_type_hints(AnalystInput)["analysis_date"] is date
    assert get_type_hints(ContractState)["analysis_date"] is date


# --- HumanDecision.source : décision humaine ou système ---------------------------


def test_source_humaine_par_defaut():
    assert HumanDecision(decision="GO", reviewer="relecteur-synth", reason="m").source == "humain"


def test_decision_systeme_no_go_acceptee():
    h = HumanDecision(
        decision="NO_GO", reviewer="systeme:expire", reason="timeout", source="systeme"
    )
    assert (h.source, h.decision) == ("systeme", "NO_GO")


@pytest.mark.parametrize("decision", ["GO", "GO_RESERVES", "ESCALADE"])
def test_decision_systeme_ne_peut_etre_que_no_go(decision):
    with pytest.raises(ValidationError, match="NO_GO"):
        HumanDecision(
            decision=decision, reviewer="systeme:expire", reason="timeout", source="systeme"
        )


def test_decision_systeme_exige_un_relecteur_systeme():
    with pytest.raises(ValidationError, match="systeme:"):
        HumanDecision(
            decision="NO_GO", reviewer="relecteur-synth", reason="timeout", source="systeme"
        )


def test_un_humain_ne_peut_pas_se_dire_systeme():
    with pytest.raises(ValidationError, match="réservé"):
        HumanDecision(decision="NO_GO", reviewer="systeme:expire", reason="m")


# --- Clauses par domaine, résumé du CRAG ----------------------------------------------


def test_chaque_type_de_clause_releve_d_un_seul_domaine():
    assert set(DOMAIN_KINDS) == set(DOMAINS)
    kinds = [k for d in DOMAINS for k in DOMAIN_KINDS[d]]
    assert sorted(kinds) == sorted(REQUIRED_KINDS)  # partition : ni oubli, ni doublon


def test_verdict_sans_resume_du_crag_par_defaut():
    v = AgentVerdict(
        domain="financier",
        score=1.0,
        hard_block=False,
        findings=[],
        evidence_ids=[],
        retrieval_status="OK",
    )
    assert v.retrieval is None


def test_resume_du_crag():
    trace = RetrievalTrace(queries=["q1", "q2"], passes=2, retained=[], expired=["L441-10"])
    assert trace.model_dump() == {
        "queries": ["q1", "q2"],
        "passes": 2,
        "retained": [],
        "expired": ["L441-10"],
    }
    with pytest.raises(ValidationError):
        RetrievalTrace(queries=[], passes=-1, retained=[], expired=[])


def test_echec_de_noeud():
    f = NodeFailure(node="analyst", error="ValueError", message="m", attempts=1, domain="financier")
    assert f.model_dump()["domain"] == "financier"
    assert NodeFailure(node="extract_clauses", error="E", message="m", attempts=1).domain is None
    with pytest.raises(ValidationError):
        NodeFailure(node="analyst", error="E", message="m", attempts=0)
    with pytest.raises(ValidationError):
        NodeFailure(node="analyst", error="E", message="m", attempts=1, domain="fiscal")

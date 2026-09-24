"""Nœuds purs, testés sans LangGraph."""

import pytest
from doubles import FakeCrag, FixedExtractor, clauses

from cdg.config import load_config
from cdg.nodes.analyst import analyst
from cdg.nodes.audit_seal import audit_seal
from cdg.nodes.explain import explain
from cdg.nodes.extract_clauses import extract_clauses
from cdg.nodes.reject import reject
from cdg.nodes.validate_input import validate_input
from cdg.nodes.verify_extraction import verify_extraction
from cdg.state import AgentVerdict

CONFIG = load_config()


# --- validate_input (minimal au J1, complet au J3) ------------------------------


@pytest.mark.parametrize("state", [{"raw_text": ""}, {"raw_text": "  \n "}, {}])
def test_validate_input_rejette_un_texte_vide(state):
    assert validate_input(state) == {"route": "reject", "reject_reason": "texte du contrat vide"}


def test_validate_input_accepte_et_initialise_les_essais():
    assert validate_input({"raw_text": "Contrat."}) == {
        "route": "extract_clauses",
        "extraction_attempts": 0,
    }


# --- extract_clauses ------------------------------------------------------------


def test_extract_clauses_incremente_les_essais_et_transmet_le_retour():
    extractor = FixedExtractor(clauses(), tokens_in=100, tokens_out=20)
    out = extract_clauses(
        {
            "raw_text": "Contrat.",
            "extraction_attempts": 1,
            "extraction_feedback": ["citation introuvable: revision_prix"],
        },
        extractor=extractor,
    )
    assert set(out) == {"clauses", "extraction_attempts", "usage"}
    assert out["extraction_attempts"] == 2 and len(out["clauses"]) == 8
    assert out["usage"][0].tokens_in == 100
    assert extractor.calls == [("Contrat.", ["citation introuvable: revision_prix"])]


def test_extract_clauses_premier_essai_sans_retour():
    extractor = FixedExtractor(clauses())
    out = extract_clauses({"raw_text": "Contrat.", "extraction_attempts": 0}, extractor=extractor)
    assert out["extraction_attempts"] == 1 and extractor.calls == [("Contrat.", [])]


# --- verify_extraction (bouchon au J1) ------------------------------------------


def test_verify_extraction_bouchon_route_vers_les_analystes():
    assert verify_extraction({"clauses": clauses()}) == {"route": "analysts"}


# --- analyst --------------------------------------------------------------------


def test_analyst_ne_renvoie_que_verdicts_et_usage():
    crag = FakeCrag(tokens_in=30)
    out = analyst({"domain": "financier", "clauses": clauses()}, crag=crag, decision_config=CONFIG)
    assert set(out) == {"verdicts", "usage"}
    [v] = out["verdicts"]
    assert isinstance(v, AgentVerdict) and v.domain == "financier"
    assert v.evidence_ids == ["financier-ref-1"] and out["usage"][0].tokens_in == 30
    assert crag.calls == ["financier"]


def test_analyst_applique_les_regles_du_domaine():
    out = analyst(
        {"domain": "juridique", "clauses": clauses(responsabilite_acheteur=None)},
        crag=FakeCrag(),
        decision_config=CONFIG,
    )
    assert out["verdicts"][0].hard_block


def test_analyst_transmet_le_statut_insuffisant():
    out = analyst(
        {"domain": "conformite", "clauses": clauses()},
        crag=FakeCrag({"conformite": "INSUFFISANT"}),
        decision_config=CONFIG,
    )
    [v] = out["verdicts"]
    assert v.retrieval_status == "INSUFFISANT" and v.evidence_ids == []


# --- bouchons -------------------------------------------------------------------


@pytest.mark.parametrize("node", [explain, audit_seal, reject])
def test_bouchons_ne_modifient_rien(node):
    assert node({"reject_reason": "texte du contrat vide"}) == {}

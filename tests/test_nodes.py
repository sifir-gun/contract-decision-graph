"""Nœuds purs, testés sans LangGraph."""

import pytest
from doubles import (
    ABSENT,
    ANALYSIS_DATE,
    CODE,
    CONTRACT_TEXT,
    FakeCrag,
    FixedExtractor,
    MemoryAuditStore,
    clauses,
    context,
    fixed_clock,
)

from cdg.application.deps import RetrievalResult
from cdg.application.nodes.analyst import analyst
from cdg.application.nodes.audit_seal import audit_seal
from cdg.application.nodes.extract_clauses import extract_clauses
from cdg.application.nodes.reject import reject
from cdg.application.nodes.validate_input import validate_input
from cdg.domain import audit
from cdg.domain.config import load_config
from cdg.domain.input_checks import rejection
from cdg.domain.models import (
    REQUIRED_KINDS,
    AgentVerdict,
    ClauseRetrieval,
    RetrievalTrace,
)

CONFIG = load_config()


# --- validate_input : taille, langue, résidus de données personnelles ----------------


def test_validate_input_accepte_un_contrat_masque_en_francais():
    state = {"raw_text": CONTRACT_TEXT, "analysis_date": ANALYSIS_DATE}
    out = validate_input(state, decision_config=CONFIG)
    assert out == {"route": "extract_clauses", "extraction_attempts": 0}


def test_domaine_entree_recevable_sans_motif():
    assert rejection(CONTRACT_TEXT, ANALYSIS_DATE, CONFIG.input) is None
    assert rejection("", ANALYSIS_DATE, CONFIG.input) == "texte du contrat vide"


@pytest.mark.parametrize("analysis_date", [None, "2026-09-25"])
def test_validate_input_rejette_sans_date_d_analyse(analysis_date):
    state = {"raw_text": CONTRACT_TEXT, "analysis_date": analysis_date}
    out = validate_input(state, decision_config=CONFIG)
    assert out == {"route": "reject", "reject_reason": "date d'analyse absente"}
    assert validate_input({"raw_text": CONTRACT_TEXT}, decision_config=CONFIG) == out


@pytest.mark.parametrize("state", [{"raw_text": ""}, {"raw_text": "  \n "}, {}])
def test_validate_input_rejette_un_texte_vide(state):
    out = validate_input(state, decision_config=CONFIG)
    assert out == {"route": "reject", "reject_reason": "texte du contrat vide"}


def test_validate_input_rejette_un_texte_trop_long():
    text = CONTRACT_TEXT * (CONFIG.input.max_chars // len(CONTRACT_TEXT) + 1)
    out = validate_input({"raw_text": text}, decision_config=CONFIG)
    assert out["route"] == "reject" and "trop long" in out["reject_reason"]


def test_validate_input_rejette_un_texte_trop_court_pour_la_langue():
    out = validate_input({"raw_text": "Contrat de prestation."}, decision_config=CONFIG)
    assert out["route"] == "reject" and "trop court" in out["reject_reason"]


def test_validate_input_rejette_un_texte_qui_n_est_pas_en_francais():
    english = (
        "This services agreement is made between the customer and the supplier. "
        "The supplier shall perform the services with reasonable care and skill, "
        "and the customer shall pay the invoices within thirty days of receipt. "
        "Either party may terminate this agreement by giving written notice."
    )
    out = validate_input({"raw_text": english}, decision_config=CONFIG)
    assert out["route"] == "reject" and "français" in out["reject_reason"]


def test_validate_input_rejette_un_texte_non_masque():
    text = CONTRACT_TEXT + "Contact : jeanne.martin@example.com, 01 99 00 45 67.\n"
    out = validate_input({"raw_text": text}, decision_config=CONFIG)
    assert out["route"] == "reject"
    assert out["reject_reason"] == "texte non masqué : EMAIL, TELEPHONE"


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
    assert out["extraction_attempts"] == 2 and len(out["clauses"]) == len(
        REQUIRED_KINDS
    )
    assert out["usage"][0].tokens_in == 100
    assert extractor.calls == [("Contrat.", ["citation introuvable: revision_prix"])]


def test_extract_clauses_premier_essai_sans_retour():
    extractor = FixedExtractor(clauses())
    out = extract_clauses(
        {"raw_text": "Contrat.", "extraction_attempts": 0}, extractor=extractor
    )
    assert out["extraction_attempts"] == 1 and extractor.calls == [("Contrat.", [])]


# --- analyst : règles d'abord, puis CRAG sur les seules clauses qui portent un constat ------


def analyse(domain, crag, **overrides):
    inp = {
        "domain": domain,
        "clauses": clauses(**overrides),
        "analysis_date": ANALYSIS_DATE,
    }
    return analyst(inp, crag=crag, decision_config=CONFIG)


def test_analyst_ne_renvoie_que_verdicts_et_usage():
    crag = FakeCrag(tokens_in=30)
    out = analyse("financier", crag, penalites_execution=ABSENT)
    assert set(out) == {"verdicts", "usage"}
    [v] = out["verdicts"]
    assert isinstance(v, AgentVerdict) and v.domain == "financier"
    assert v.evidence_ids == ["financier-ref-1"] and out["usage"][0].tokens_in == 30
    assert crag.calls == ["financier"]


def test_analyst_sans_constat_ne_cherche_rien_et_reste_ok():
    crag = FakeCrag(
        empty={"financier"}
    )  # même un corpus vide ne gêne pas : rien à justifier
    [v] = analyse("financier", crag)["verdicts"]
    assert crag.kinds == {"financier": []}
    assert (v.retrieval_status, v.score, v.findings, v.evidence_ids) == (
        "OK",
        1.0,
        [],
        [],
    )
    assert v.retrieval.clauses == []


def test_analyst_crag_sur_les_seules_clauses_qui_portent_un_constat():
    crag = FakeCrag()
    # pénalités d'exécution absentes (pénalité), délai absent (information), révision plafonnée
    [v] = analyse("financier", crag, penalites_execution=ABSENT, delai_paiement=ABSENT)[
        "verdicts"
    ]
    assert crag.kinds == {"financier": ["penalites_execution", "delai_paiement"]}
    assert [c.kind for c in v.retrieval.clauses] == [
        "penalites_execution",
        "delai_paiement",
    ]
    assert v.retrieval_status == "OK" and v.score == 0.6


def test_analyst_applique_les_regles_du_domaine():
    crag = FakeCrag()
    [v] = analyse("juridique", crag, responsabilite_acheteur=None)["verdicts"]
    assert v.hard_block and crag.kinds == {"juridique": ["responsabilite_acheteur"]}


def test_analyst_constat_sans_reference_insuffisant():
    crag = FakeCrag(empty={"conformite"})
    [v] = analyse("conformite", crag, transfert_hors_ue=ABSENT)["verdicts"]
    assert v.retrieval_status == "INSUFFISANT" and v.evidence_ids == []
    assert "la clause transfert_hors_ue" in v.findings[-1]


def test_analyst_ajoute_les_constats_et_le_resume_du_crag():
    clause = ClauseRetrieval(
        kind="penalites_execution",
        queries=["q1"],
        passes=1,
        retained=["Fiche"],
        expired=["L441-10"],
    )
    trace = RetrievalTrace(
        clauses=[clause],
        findings=[
            "référence expirée à la date d'analyse : L441-10"
        ],  # constats du CRAG
    )
    received = []

    def crag(domain, clauses, analysis_date):
        assert analysis_date == ANALYSIS_DATE
        received.extend(c.kind for c in clauses)
        return RetrievalResult(trace=trace, usage=[])

    [v] = analyse("financier", crag, penalites_execution=2.0)["verdicts"]
    assert received == ["penalites_execution"]
    assert v.findings[-1] == "référence expirée à la date d'analyse : L441-10"
    assert len(v.findings) == 2  # constat de la règle, puis celui du CRAG
    assert v.retrieval == trace and v.evidence_ids == ["Fiche"]
    assert v.retrieval_status == "OK"


# --- reject ---------------------------------------------------------------------


def test_reject_n_ecrit_rien_de_plus():
    # reject_reason (validate_input) suffit au scellement ; explain est testé à part
    assert reject({"reject_reason": "texte du contrat vide"}) == {}


# --- audit_seal ----------------------------------------------------------------------


def seal(state, store, thread_id="c-1"):
    return audit_seal(
        state,
        audit_store=store,
        clock=fixed_clock,
        decision_config=CONFIG,
        code_version=CODE,
        thread_id=thread_id,
    )


def test_audit_seal_ecrit_les_empreintes_du_journal():
    store = MemoryAuditStore()
    out = seal(
        {"contract_id": "c-1", "reject_reason": "texte vide", **context()}, store
    )
    [entry] = store.entries()
    assert out == {
        "config_hash": entry.config_hash,
        "decision_hash": entry.decision_hash,
        "chain_hash": entry.chain_hash,
        "sealing_findings": [],  # montrés au réviseur par la sortie de la revue
    }
    assert entry.config_hash == audit.config_hash(CONFIG)


def test_audit_seal_rejoue_rend_l_enregistrement_existant():
    store = MemoryAuditStore()
    state = {"contract_id": "c-1", "reject_reason": "texte vide", **context()}
    assert seal(state, store) == seal(state, store)
    assert len(store.entries()) == 1

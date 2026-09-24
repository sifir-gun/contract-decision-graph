"""Politique d'arbitrage humain : fonctions pures, sans LangGraph (spec, critère n° 12)."""

import json

import pytest
from doubles import verdicts

from cdg import policy
from cdg.config import load_config
from cdg.state import HumanDecision

CONFIG = load_config()
BLOCKED = verdicts(juridique={"score": 0.5, "hard_block": True})
CLEAN = verdicts()


def human(
    decision="GO",
    reviewer="relecteur-synth",
    reason="marge jugée acceptable",
    overrides_block=False,
) -> HumanDecision:
    return HumanDecision(
        decision=decision, reviewer=reviewer, reason=reason, overrides_block=overrides_block
    )


# --- Demande exposée à l'humain ---------------------------------------------------------


def test_demande_expose_le_contexte_de_decision():
    state = {
        "contract_id": "c-synth-001",
        "raw_text": "texte non fiable",
        "proposed_decision": "GO",
        "margin": 0.04,
        "failure_report": None,
        "verdicts": verdicts(juridique={"score": 0.5}),
    }
    request = policy.build_request(state, CONFIG)
    assert request["contract_id"] == "c-synth-001"
    assert (request["proposed_decision"], request["margin"]) == ("GO", 0.04)
    assert request["allowed_decisions"] == ["GO", "GO_RESERVES", "NO_GO"]
    assert request["verdicts"][0]["domain"] == "juridique"
    assert request["verdicts"][0]["score"] == 0.5
    assert "raw_text" not in request and "error" not in request
    json.dumps(request)  # charge utile sérialisable


def test_demande_apres_echec_d_extraction_sans_verdicts_ni_marge():
    state = {
        "contract_id": "c-synth-002",
        "proposed_decision": "ESCALADE",
        "failure_report": {"stage": "extraction", "problems": ["clause manquante: x"]},
    }
    request = policy.build_request(state, CONFIG)
    assert request["verdicts"] == [] and request["margin"] is None
    assert request["failure_report"]["stage"] == "extraction"


def test_demande_arrondit_les_scores():
    state = {
        "proposed_decision": "ESCALADE",
        "verdicts": verdicts(financier={"score": 0.1234567891}),
    }
    request = policy.build_request(state, CONFIG)
    assert request["verdicts"][1]["score"] == 0.123457


# --- Contrôle de la décision humaine ----------------------------------------------------


@pytest.mark.parametrize("decision", ["GO", "GO_RESERVES", "NO_GO"])
def test_decision_autorisee_acceptee_sans_blocage(decision):
    assert policy.check(human(decision), CLEAN, CONFIG) is None


def test_escalade_refusee_l_humain_doit_trancher():
    assert "ESCALADE" in policy.check(human("ESCALADE"), CLEAN, CONFIG)


@pytest.mark.parametrize("field", ["reviewer", "reason"])
def test_relecteur_et_motif_obligatoires(field):
    assert field in policy.check(human(**{field: "   "}), CLEAN, CONFIG)


# critère n° 12 : levée de blocage dur
@pytest.mark.parametrize("decision", ["GO", "GO_RESERVES"])
def test_12_levee_de_blocage_sans_overrides_block_refusee(decision):
    assert "overrides_block" in policy.check(human(decision), BLOCKED, CONFIG)


def test_12_levee_de_blocage_avec_overrides_block_et_motif_acceptee():
    h = human("GO", reason="plafond négocié par avenant", overrides_block=True)
    assert policy.check(h, BLOCKED, CONFIG) is None


def test_no_go_sur_blocage_accepte_sans_levee():
    assert policy.check(human("NO_GO"), BLOCKED, CONFIG) is None


def test_overrides_block_sans_blocage_a_lever_refuse():
    assert "overrides_block" in policy.check(human("GO", overrides_block=True), CLEAN, CONFIG)
    assert "overrides_block" in policy.check(human("NO_GO", overrides_block=True), BLOCKED, CONFIG)


def test_levee_interdite_par_la_configuration():
    cfg = CONFIG.model_copy(
        update={
            "human_policy": CONFIG.human_policy.model_copy(update={"allow_block_override": False})
        }
    )
    h = human("GO", overrides_block=True)
    assert "interdite" in policy.check(h, BLOCKED, cfg)
    assert policy.check(human("NO_GO"), BLOCKED, cfg) is None


# --- Réponse brute reçue à la reprise ---------------------------------------------------


def test_review_accepte_une_reponse_valide():
    payload = {"decision": "NO_GO", "reviewer": "relecteur-synth", "reason": "risque trop élevé"}
    decision, error = policy.review(payload, CLEAN, CONFIG)
    assert error is None and decision == HumanDecision(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "PEUT_ETRE", "reviewer": "r", "reason": "m"},  # décision inconnue
        {"decision": "GO", "reason": "m"},  # relecteur manquant
        "GO",  # pas un objet
        None,
    ],
)
def test_review_signale_une_reponse_mal_formee_sans_lever(payload):
    decision, error = policy.review(payload, CLEAN, CONFIG)
    assert decision is None and error.startswith("réponse invalide")


def test_review_applique_la_politique():
    payload = {"decision": "GO", "reviewer": "r", "reason": "m"}
    decision, error = policy.review(payload, BLOCKED, CONFIG)
    assert decision is None and "overrides_block" in error


def test_decision_systeme_d_expiration_acceptee_meme_sur_blocage():
    h = HumanDecision(
        decision="NO_GO", reviewer="systeme:expire", reason="timeout", source="systeme"
    )
    assert policy.check(h, BLOCKED, CONFIG) is None

"""Politique d'arbitrage humain : fonctions pures, sans LangGraph (spec, critère n° 12)."""

import json

import pytest
from doubles import ACTEUR_ANALYSTE, ACTEUR_RELECTEUR, OPERATEUR, answer, verdicts

from cdg.domain import policy
from cdg.domain.authorization import Actor
from cdg.domain.config import load_config
from cdg.domain.models import HumanReview

CONFIG = load_config()
BLOCKED = verdicts(juridique={"score": 0.5, "hard_block": True})
CLEAN = verdicts()


def human(
    decision="GO",
    reason="marge jugée acceptable",
    overrides_block=False,
    acteur=ACTEUR_RELECTEUR,
) -> HumanReview:
    return HumanReview(
        decision=decision,
        acteur=acteur,
        reason=reason,
        overrides_block=overrides_block,
    )


def check(h, verdicts, config, analyst):
    """Sans changement de configuration pendant l'analyse : la courante seule décide de
    la levée d'un blocage dur (cas du changement : tests/test_reprise_configuration.py)."""
    return policy.check(
        h,
        verdicts,
        config,
        analyst,
        analysis_allows_override=policy.analysis_allows_override({}, config),
    )


def review(payload, verdicts, config, analyst):
    return policy.review(
        payload,
        verdicts,
        config,
        analyst,
        analysis_allows_override=policy.analysis_allows_override({}, config),
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
    assert check(human(decision), CLEAN, CONFIG, ACTEUR_ANALYSTE) is None


def test_escalade_refusee_l_humain_doit_trancher():
    assert "ESCALADE" in check(human("ESCALADE"), CLEAN, CONFIG, ACTEUR_ANALYSTE)


def test_motif_obligatoire():
    assert "reason" in check(human(reason="   "), CLEAN, CONFIG, ACTEUR_ANALYSTE)


# critère n° 12 : levée de blocage dur
@pytest.mark.parametrize("decision", ["GO", "GO_RESERVES"])
def test_12_levee_de_blocage_sans_overrides_block_refusee(decision):
    assert "overrides_block" in check(human(decision), BLOCKED, CONFIG, ACTEUR_ANALYSTE)


def test_12_levee_de_blocage_avec_overrides_block_et_motif_acceptee():
    h = human("GO", reason="plafond négocié par avenant", overrides_block=True)
    assert check(h, BLOCKED, CONFIG, ACTEUR_ANALYSTE) is None


def test_no_go_sur_blocage_accepte_sans_levee():
    assert check(human("NO_GO"), BLOCKED, CONFIG, ACTEUR_ANALYSTE) is None


def test_overrides_block_sans_blocage_a_lever_refuse():
    assert "overrides_block" in check(
        human("GO", overrides_block=True), CLEAN, CONFIG, ACTEUR_ANALYSTE
    )
    assert "overrides_block" in check(
        human("NO_GO", overrides_block=True), BLOCKED, CONFIG, ACTEUR_ANALYSTE
    )


def test_levee_interdite_par_la_configuration():
    cfg = CONFIG.model_copy(
        update={
            "human_policy": CONFIG.human_policy.model_copy(
                update={"allow_block_override": False}
            )
        }
    )
    h = human("GO", overrides_block=True)
    assert "interdite" in check(h, BLOCKED, cfg, ACTEUR_ANALYSTE)
    assert check(human("NO_GO"), BLOCKED, cfg, ACTEUR_ANALYSTE) is None


# --- Réponse brute reçue à la reprise ---------------------------------------------------


def test_review_accepte_une_reponse_valide():
    payload = answer("NO_GO", "risque trop élevé")
    decision, error = review(payload, CLEAN, CONFIG, ACTEUR_ANALYSTE)
    assert error is None and decision == HumanReview(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        answer("PEUT_ETRE", "m"),  # décision inconnue
        {"decision": "GO", "reason": "m"},  # acteur manquant
        {**answer("GO", "m"), "reviewer": "Camille"},  # relecteur nommé : refusé
        "GO",  # pas un objet
        None,
    ],
)
def test_review_signale_une_reponse_mal_formee_sans_lever(payload):
    decision, error = review(payload, CLEAN, CONFIG, ACTEUR_ANALYSTE)
    assert decision is None and error.startswith("réponse invalide")


def test_review_applique_la_politique():
    payload = answer("GO", "m")
    decision, error = review(payload, BLOCKED, CONFIG, ACTEUR_ANALYSTE)
    assert decision is None and "overrides_block" in error


def test_decision_systeme_d_expiration_acceptee_meme_sur_blocage():
    h = HumanReview(
        decision="NO_GO", acteur=ACTEUR_ANALYSTE, reason="timeout", source="systeme"
    )
    assert check(h, BLOCKED, CONFIG, ACTEUR_ANALYSTE) is None


# --- quatre yeux, second contrôle (le premier est dans l'interface) ------------------------


def test_quatre_yeux_le_relecteur_qui_a_lance_l_analyse_est_redemande():
    h = human("NO_GO", acteur=ACTEUR_ANALYSTE)
    assert "a lancé l'analyse" in check(h, CLEAN, CONFIG, ACTEUR_ANALYSTE)


def test_quatre_yeux_contournement_par_la_cli_refuse_sauf_en_urgence():
    assert "autre canal" not in (
        check(human("NO_GO", acteur=OPERATEUR), CLEAN, CONFIG, ACTEUR_ANALYSTE) or ""
    )  # CLI hors du cluster : non authentifiée, aucune identité à comparer
    analyse_cli = Actor(canal="cli", authentifie=False, operateur="lot-nocturne")
    refused = check(human("NO_GO"), CLEAN, CONFIG, analyse_cli)
    assert "autre canal" in refused
    urgence = Actor(
        canal="cli", authentifie=False, operateur="astreinte-1", urgence=True
    )
    assert check(human("NO_GO", acteur=urgence), CLEAN, CONFIG, None) is None


def test_decision_systeme_hors_quatre_yeux():
    """L'expiration (NO_GO système) n'est pas une revue : pas de quatre yeux."""
    h = HumanReview(
        decision="NO_GO", acteur=ACTEUR_ANALYSTE, reason="timeout", source="systeme"
    )
    assert check(h, CLEAN, CONFIG, ACTEUR_ANALYSTE) is None

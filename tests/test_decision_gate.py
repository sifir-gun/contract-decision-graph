"""decision_gate : budget, blocages, INSUFFISANT, conflit, seuils, marge (spec, « decision_gate »)."""

import pytest
import yaml
from doubles import ABSENT, clauses, usage, verdict, verdicts

from cdg.application.nodes.decision_gate import decision_gate
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.domain.decision import aggregate, conflict, decide, total_tokens
from cdg.domain.justification import justify
from cdg.domain.models import DOMAINS, ClauseRetrieval, NodeFailure, RetrievalTrace
from cdg.domain.rules import RULES

CONFIG = load_config()
BUDGET = CONFIG.budget.max_tokens_per_contract


def gate(vs, tokens=0, config=CONFIG):
    return decision_gate(
        {"verdicts": vs, "usage": [usage(tokens_in=tokens)] if tokens else []}, config
    )


def config_with(**changes) -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return DecisionConfig.model_validate(data | changes)


# --- Critère d'acceptation n° 2 : blocage dur -----------------------------------


@pytest.mark.parametrize("blocked", DOMAINS)
def test_2_un_seul_blocage_dur_donne_no_go_quel_que_soit_le_score(blocked):
    out = gate(verdicts(**{blocked: {"hard_block": True}}))  # score moyen = 1,0
    assert out == {
        "proposed_decision": "NO_GO",
        "final_decision": "NO_GO",
        "margin": 0.25,
        "route": "explain",
    }


def test_2_blocage_dur_ignore_une_marge_faible():
    # sans le blocage : 0,79, soit GO avec une marge de 0,04, qui partirait en revue humaine
    vs = verdicts(
        juridique={"score": 0.5},
        operationnel={"score": 0.7},
        conformite={"hard_block": True},
    )
    out = gate(vs)
    assert out["route"] == "explain" and out["final_decision"] == "NO_GO"
    assert out["margin"] == 0.04


def test_blocage_dur_et_insuffisant_donnent_no_go():
    vs = verdicts(juridique={"hard_block": True}, conformite={"status": "INSUFFISANT"})
    assert gate(vs)["final_decision"] == "NO_GO"


def test_blocage_dur_et_budget_depasse_no_go_avec_rapport_budget():
    out = gate(verdicts(financier={"hard_block": True}), tokens=BUDGET + 1)
    assert out == {
        "proposed_decision": "NO_GO",
        "final_decision": "NO_GO",
        "margin": 0.25,
        "route": "explain",
        "failure_report": {"stage": "budget", "tokens": BUDGET + 1, "limit": BUDGET},
    }


# --- Budget ---------------------------------------------------------------------


def test_budget_depasse_escalade():
    out = gate(verdicts(), tokens=BUDGET + 1)
    assert out == {
        "proposed_decision": "ESCALADE",
        "margin": 0.25,
        "route": "human_review",
        "failure_report": {"stage": "budget", "tokens": BUDGET + 1, "limit": BUDGET},
    }


def test_budget_atteint_sans_depassement():
    out = gate(verdicts(), tokens=BUDGET)
    assert out["route"] == "explain" and "failure_report" not in out


def test_total_des_tokens_entrants_et_sortants():
    assert total_tokens([usage(100, 50), usage(10, 5)]) == 165
    assert total_tokens([]) == 0


# --- INSUFFISANT et conflit ------------------------------------------------------


def test_insuffisant_escalade_sans_decision_finale():
    out = gate(verdicts(conformite={"status": "INSUFFISANT"}))
    assert out == {
        "proposed_decision": "ESCALADE",
        "margin": 0.25,
        "route": "human_review",
    }


def test_conflit_escalade():
    out = gate(verdicts(operationnel={"score": 0.4}))  # écart 0,6 > 0,5
    assert out["proposed_decision"] == "ESCALADE" and out["route"] == "human_review"


def test_ecart_egal_au_seuil_n_est_pas_un_conflit():
    out = gate(verdicts(operationnel={"score": 0.5}))  # écart 0,5 : pas > 0,5
    assert out["proposed_decision"] == "GO"


def test_conflit_calcule_sur_les_seuls_domaines_ok():
    assert not conflict(
        verdicts(conformite={"score": 0.0, "status": "INSUFFISANT"}), 0.5
    )
    assert conflict(verdicts(conformite={"score": 0.0}), 0.5)


# --- Seuils et marge : scénarios de contrôle de la spec ---------------------------


@pytest.mark.parametrize(
    "scores,score,decision,margin,route",
    [
        ({"juridique": 0.5}, 0.85, "GO", 0.1, "explain"),
        ({"juridique": 0.5, "operationnel": 0.7}, 0.79, "GO", 0.04, "human_review"),
        (
            {"juridique": 0.5, "financier": 0.6, "operationnel": 0.7},
            0.69,
            "GO_RESERVES",
            0.06,
            "explain",
        ),
    ],
)
def test_scenarios_de_controle(scores, score, decision, margin, route):
    vs = verdicts(**{d: {"score": s} for d, s in scores.items()})
    a = aggregate(vs, CONFIG)
    assert (a.score, a.decision, a.margin) == (score, decision, margin)
    out = gate(vs)
    assert out["route"] == route and out["proposed_decision"] == decision
    assert ("final_decision" in out) == (route == "explain")


def test_score_pile_au_seuil_malgre_le_bruit_flottant():
    scores = {
        "juridique": 0.41,
        "financier": 0.47,
        "conformite": 0.47,
        "operationnel": 0.71,
    }
    raw = sum(CONFIG.weight(d) * s for d, s in scores.items())
    assert raw == 0.49999999999999994  # sans arrondi : NO_GO
    a = aggregate(verdicts(**{d: {"score": s} for d, s in scores.items()}), CONFIG)
    assert (a.score, a.decision, a.margin) == (0.5, "GO_RESERVES", 0.0)


def test_score_juste_sous_le_seuil_go():
    # 0,3 × 0,7 + 0,25 × 0,7 + 0,25 × 0,8 + 0,2 × 0,824995 = 0,749999, sans conflit
    vs = verdicts(
        juridique={"score": 0.7},
        financier={"score": 0.7},
        conformite={"score": 0.8},
        operationnel={"score": 0.824995},
    )
    a = aggregate(vs, CONFIG)
    assert (a.score, a.decision) == (0.749999, "GO_RESERVES")


def test_marge_egale_au_minimum_va_a_explain():
    vs = verdicts(
        juridique={"score": 0.5}, financier={"score": 0.8}
    )  # 0,80 : marge 0,05
    out = gate(vs)
    assert (out["margin"], out["route"]) == (0.05, "explain")


def test_min_margin_lu_dans_la_configuration():
    out = gate(verdicts(juridique={"score": 0.5}), config=config_with(min_margin=0.2))
    assert out["route"] == "human_review"


def test_seuil_no_go_atteignable_par_une_autre_configuration():
    # avec un écart de conflit à 1, des risques cumulés peuvent donner NO_GO par seuil
    vs = verdicts(**{d: {"score": 0.3} for d in DOMAINS})
    out = gate(vs, config=config_with(conflict_gap=1.0))
    assert out == {
        "proposed_decision": "NO_GO",
        "final_decision": "NO_GO",
        "margin": 0.2,
        "route": "explain",
    }


# --- Robustesse -------------------------------------------------------------------


def test_ordre_des_verdicts_sans_effet():
    vs = verdicts(
        juridique={"score": 0.5}, financier={"score": 0.6}, operationnel={"score": 0.7}
    )
    assert aggregate(vs, CONFIG) == aggregate(list(reversed(vs)), CONFIG)


def test_verdict_manquant_refuse():
    with pytest.raises(ValueError, match="un verdict par domaine"):
        aggregate(verdicts()[:3], CONFIG)


def test_verdict_en_double_refuse():
    with pytest.raises(ValueError, match="un verdict par domaine"):
        aggregate(verdicts() + [verdict("juridique")], CONFIG)


# --- human_policy.hard_block_review -------------------------------------------------


def with_hard_block_review() -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["human_policy"]["hard_block_review"] = True
    return DecisionConfig.model_validate(data)


def test_blocage_dur_en_revue_humaine_si_configure():
    out = gate(
        verdicts(juridique={"hard_block": True}), config=with_hard_block_review()
    )
    assert out == {
        "proposed_decision": "NO_GO",
        "margin": 0.25,
        "route": "human_review",
    }


def test_blocage_dur_en_revue_humaine_conserve_le_rapport_budget():
    out = gate(
        verdicts(juridique={"hard_block": True}),
        tokens=BUDGET + 1,
        config=with_hard_block_review(),
    )
    assert (out["route"], out["proposed_decision"]) == ("human_review", "NO_GO")
    assert out["failure_report"] == {
        "stage": "budget",
        "tokens": BUDGET + 1,
        "limit": BUDGET,
    }
    assert "final_decision" not in out


# --- Analyste en échec (garde de l'orchestrateur) : escalade --------------------------

FAILURE = NodeFailure(
    node="analyst", error="ValueError", message="m", attempts=1, domain="financier"
)


def gate_with_failure(vs, tokens=0, config=CONFIG):
    state = {"verdicts": vs, "failures": [FAILURE], "usage": [usage(tokens_in=tokens)]}
    return decision_gate(state, config)


def three_verdicts(**by_domain):
    return [v for v in verdicts(**by_domain) if v.domain != "financier"]


def test_analyste_en_echec_escalade_sans_marge():
    out = gate_with_failure(three_verdicts())
    assert out == {
        "proposed_decision": "ESCALADE",
        "failure_report": {"stage": "noeuds", "failures": [FAILURE.model_dump()]},
        "route": "human_review",
    }


def test_analyste_en_echec_et_budget_depasse_rapport_commun():
    out = gate_with_failure(three_verdicts(), tokens=BUDGET + 1)
    assert out["proposed_decision"] == "ESCALADE"
    assert out["failure_report"]["budget"] == {"tokens": BUDGET + 1, "limit": BUDGET}


def test_analyste_en_echec_n_empeche_pas_un_blocage_dur_etabli():
    out = gate_with_failure(three_verdicts(juridique={"hard_block": True}))
    assert (out["proposed_decision"], out["final_decision"], out["route"]) == (
        "NO_GO",
        "NO_GO",
        "explain",
    )
    assert out["failure_report"]["stage"] == "noeuds" and "margin" not in out


def test_analyste_en_echec_blocage_dur_en_revue_si_configure():
    config = config_with(
        human_policy={
            "allowed_decisions": ["GO", "GO_RESERVES", "NO_GO"],
            "allow_block_override": True,
            "hard_block_review": True,
        }
    )
    out = gate_with_failure(
        three_verdicts(juridique={"hard_block": True}), config=config
    )
    assert (out["proposed_decision"], out["route"]) == ("NO_GO", "human_review")
    assert "final_decision" not in out


# --- Domaine : proposition sans route ni état du graphe -------------------------------


def test_decide_rend_une_proposition_que_le_noeud_traduit():
    outcome = decide(verdicts(), [], [], CONFIG)
    assert (outcome.proposed, outcome.human_review, outcome.final) == (
        "GO",
        False,
        "GO",
    )
    outcome = decide(verdicts(financier={"status": "INSUFFISANT"}), [], [], CONFIG)
    assert (outcome.proposed, outcome.human_review, outcome.final) == (
        "ESCALADE",
        True,
        None,
    )


# --- Choix de conception : NO_GO réservé aux blocages durs --------------------------------


def test_pire_cumul_des_penalites_sans_blocage_reste_au_dessus_du_seuil_no_go():
    """Toutes les pénalités de score déclenchées, aucun blocage : au pire GO_RESERVES.
    Si une configuration rendait NO_GO atteignable sans blocage dur, ce test le signalerait."""
    worst = clauses(
        responsabilite_fournisseur=50,  # juridique : plafond fournisseur < 100 %
        penalites_execution=ABSENT,  # financier : pénalités d'exécution absentes
        delai_paiement=90,  # financier : délai non conforme
        transfert_hors_ue=ABSENT,  # conformité : localisation non précisée
        duree_engagement=48,  # opérationnel : engagement > 36 mois
        preavis_resiliation=12,  # opérationnel : préavis > 6 mois
    )
    vs = [_justified(RULES[d](worst, CONFIG)) for d in DOMAINS]
    assert not any(v.hard_block for v in vs)
    assert {v.domain: v.score for v in vs} == {
        "juridique": 0.5,
        "financier": 0.4,
        "conformite": 0.7,
        "operationnel": 0.4,
    }
    d = aggregate(vs, CONFIG)
    assert (d.score, d.decision, d.margin) == (0.505, "GO_RESERVES", 0.005)


def _justified(assessment):
    """Verdict dont chaque constat est justifié par une référence : statut OK."""
    trace = RetrievalTrace(
        clauses=[
            ClauseRetrieval(
                kind=k, queries=["q"], passes=1, retained=["réf"], expired=[]
            )
            for k in assessment.kinds_to_justify()
        ]
    )
    return justify(assessment, trace)

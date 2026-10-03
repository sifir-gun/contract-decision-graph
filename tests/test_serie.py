"""Mesures de la série réelle sur le jeu de démonstration (J5), sans LLM : issue d'un essai
comparée à l'issue attendue, écarts d'extraction, coût, latences, résumé (`serie.py`).
La série elle-même est dans `test_llm_jeu.py`."""

import pytest
from demo_set import load
from serie import (
    TARIFS,
    Pacer,
    analysts_wall_ms,
    classify,
    clause_gaps,
    cost_usd,
    describe,
    extraction_refusals,
    outcome_of,
    step_latencies,
    summarize,
    tokens_by_model,
)

from cdg.domain.config import load_config
from cdg.domain.models import Usage

_, CONTRACTS = load()
BY_ID = {c.id: c for c in CONTRACTS}
MAIN, LIGHT = "mistral-small-2603", "ministral-8b-2512"


def status(statut="termine", proposed=None, final=None, reject_reason=None):
    return {
        "statut": statut,
        "proposed_decision": proposed,
        "final_decision": final,
        "reject_reason": reject_reason,
    }


@pytest.mark.parametrize(
    ("cid", "obtained", "label"),
    [
        # GO automatique attendu
        ("demo-01-go-maintenance", status(proposed="GO", final="GO"), "conforme"),
        (
            "demo-01-go-maintenance",
            status(proposed="GO_RESERVES", final="GO_RESERVES"),
            "plus_prudente",
        ),
        (
            "demo-01-go-maintenance",
            status("suspendu", proposed="ESCALADE"),
            "plus_prudente",
        ),
        # NO_GO automatique attendu : une revue humaine n'accorde rien
        ("demo-06-no-go-conseil", status(proposed="NO_GO", final="NO_GO"), "conforme"),
        ("demo-06-no-go-conseil", status(proposed="GO", final="GO"), "plus_favorable"),
        ("demo-06-no-go-conseil", status("suspendu", proposed="GO"), "plus_prudente"),
        # revue attendue : ESCALADE, puis GO_RESERVES humain
        (
            "demo-09-escalade-mobilier",
            status("suspendu", proposed="ESCALADE"),
            "conforme",
        ),
        (
            "demo-09-escalade-mobilier",
            status(proposed="GO", final="GO"),
            "plus_favorable",
        ),
        # revue sautée, même décision : un écart, pas une faveur
        (
            "demo-09-escalade-mobilier",
            status(proposed="GO_RESERVES", final="GO_RESERVES"),
            "ecart",
        ),
        ("demo-09-escalade-mobilier", status("suspendu", proposed="NO_GO"), "ecart"),
        # tentative d'instruction : revue attendue avec NO_GO proposé
        ("demo-11-piege-injection", status("suspendu", proposed="NO_GO"), "conforme"),
        ("demo-11-piege-injection", status(proposed="NO_GO", final="NO_GO"), "ecart"),
        # rejet attendu
        (
            "demo-10-rejet-anglais",
            status(reject_reason="langue non reconnue comme français"),
            "conforme",
        ),
        ("demo-10-rejet-anglais", status(proposed="GO", final="GO"), "plus_favorable"),
    ],
)
def test_issue_comparee_a_l_issue_attendue(cid, obtained, label):
    assert classify(BY_ID[cid].expected, outcome_of(obtained)) == label


def test_issue_decrite():
    assert describe(outcome_of(status(proposed="GO", final="GO"))) == "GO, automatique"
    assert (
        describe(outcome_of(status("suspendu", proposed="ESCALADE")))
        == "ESCALADE, revue humaine"
    )
    assert describe(outcome_of(status(reject_reason="langue"))) == "rejet"


def test_statut_inattendu_erreur_explicite():
    with pytest.raises(ValueError, match="en_cours"):
        outcome_of(status("en_cours", proposed="GO"))


def test_ecarts_d_extraction_par_clause():
    contract = BY_ID["demo-01-go-maintenance"]
    assert clause_gaps(contract.clauses, contract.clauses) == []
    changed = [
        c.model_copy(update={"value": 2.0}) if c.kind == "revision_prix" else c
        for c in contract.clauses
        if c.kind != "transfert_hors_ue"
    ]
    assert clause_gaps(changed, contract.clauses) == [
        "revision_prix : attendu (True, 3.0, None), obtenu (True, 2.0, None)",
        (
            "transfert_hors_ue : attendu (True, None, 'clauses_contractuelles_types'), "
            "obtenu non rendu"
        ),
    ]


def usage(node, model, tokens_in=0, tokens_out=0, latency_ms=0):
    return Usage(
        node=node,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )


def test_tarifs_des_modeles_de_la_configuration():
    """Les séries lisent les tarifs communs avec les traces (config/tarifs.yaml)."""
    config = load_config().llm
    assert {config.model("main"), config.model("light")} <= set(TARIFS)


def test_cout_aux_tarifs_publies():
    used = [
        usage("extract_clauses", MAIN, 2_000, 500),
        usage("crag_grade:financier:revision_prix", LIGHT, 1_000, 10),
        usage("explain", MAIN, 800, 400),
    ]
    # Mistral Small 4 : 0,15 $ et 0,60 $ par million ; Ministral 3 8B : 0,15 $ et 0,15 $
    assert cost_usd(used) == pytest.approx(
        (2_800 * 0.15 + 900 * 0.60 + 1_010 * 0.15) / 1_000_000
    )
    assert tokens_by_model(used) == {MAIN: 3_700, LIGHT: 1_010}


def test_modele_sans_tarif_erreur_explicite():
    with pytest.raises(ValueError, match="tarif inconnu"):
        cost_usd([usage("explain", "modele-inconnu", 1, 1)])


def test_latences_par_etape():
    used = [
        usage("extract_clauses", MAIN, latency_ms=3_000),
        usage("extract_clauses", MAIN, latency_ms=2_500),  # nouvelle extraction
        usage("crag_grade:financier:revision_prix", LIGHT, latency_ms=700),
        usage("crag_rewrite:financier:revision_prix", LIGHT, latency_ms=300),
        usage("crag_grade:juridique:responsabilite_fournisseur", LIGHT, latency_ms=900),
        usage("explain", MAIN, latency_ms=1_500),
    ]
    assert step_latencies(used) == {
        "extraction_ms": 5_500,
        "explication_ms": 1_500,
        "analystes_llm_ms": {"financier": 1_000, "juridique": 900},
    }


def test_duree_de_l_etape_des_analystes():
    steps = [
        ("2026-09-26T10:00:00.000000+00:00", ("validate_input",)),
        ("2026-09-26T10:00:01.000000+00:00", ("extract_clauses",)),
        ("2026-09-26T10:00:04.000000+00:00", ("verify_extraction",)),
        (
            "2026-09-26T10:00:04.100000+00:00",
            ("analyst", "analyst", "analyst", "analyst"),
        ),
        ("2026-09-26T10:00:06.600000+00:00", ("decision_gate",)),
        ("2026-09-26T10:00:06.700000+00:00", ("explain",)),
    ]
    assert analysts_wall_ms(steps) == 2_500
    # extraction escaladée : aucun analyste
    assert analysts_wall_ms(steps[:3] + [steps[3][:1] + (("human_review",),)]) is None


CATEGORY = "catégorie contredite par la citation (« factures périodiques » : …)"
VALUE = "valeur absente de la citation (3 mois): preavis_resiliation"


def test_motifs_de_refus_de_chaque_extraction_refusee():
    # (nœuds suivants, retour ciblé dans l'état), dans l'ordre chronologique
    steps = [
        (("validate_input",), None),
        (("extract_clauses",), None),
        (("verify_extraction",), None),
        (("extract_clauses",), [CATEGORY]),  # première extraction refusée
        (("verify_extraction",), [CATEGORY]),
        (("analyst", "analyst", "analyst", "analyst"), [CATEGORY]),
    ]
    assert extraction_refusals(steps, None) == [[CATEGORY]]
    # la seconde refusée aussi, puis escaladée : ses motifs sont dans le rapport
    report = {"stage": "extraction", "attempts": 2, "problems": [VALUE]}
    escalated = [*steps[:5], (("human_review",), [CATEGORY])]
    assert extraction_refusals(escalated, report) == [[CATEGORY], [VALUE]]
    # aucune extraction refusée
    assert extraction_refusals(steps[:3] + [(("analyst",), None)], None) == []
    # un échec de nœud n'est pas un refus d'extraction
    assert extraction_refusals(steps[:3], {"stage": "noeuds", "failures": []}) == []


def line(contract, run, label, issue, cost, duration_ms, gaps=(), refusals=()):
    return {
        "contrat": contract,
        "essai": run,
        "classement": label,
        "issue": issue,
        "ecarts_extraction": list(gaps),
        "refus_extraction": list(refusals),
        "cout_usd": cost,
        "duree_ms": duration_ms,
    }


def test_resume_de_la_serie():
    lines = [
        line("demo-01", 1, "conforme", "GO, automatique", 0.002, 20_000),
        line(
            "demo-01",
            2,
            "conforme",
            "GO, automatique",
            0.003,
            22_000,
            refusals=[[VALUE]],
        ),
        line(
            "demo-01",
            3,
            "plus_prudente",
            "ESCALADE, revue humaine",
            0.004,
            30_000,
            ["revision_prix : attendu (True, 3.0, None), obtenu (True, None, None)"],
        ),
        line("demo-10", 1, "conforme", "rejet", 0.0, 50),
    ]
    summary = summarize(lines)
    assert summary["contrats"]["demo-01"] == {
        "essais": 3,
        "classement": {"conforme": 2, "plus_prudente": 1},
        "issues": {"GO, automatique": 2, "ESCALADE, revue humaine": 1},
        "essais_avec_ecart_d_extraction": 1,
        "extractions_refusees": 1,
        "cout_median_usd": 0.003,
        "duree_mediane_s": 22.0,
        "duree_max_s": 30.0,
    }
    assert summary["essais"] == 4
    assert summary["classement"] == {"conforme": 3, "plus_prudente": 1}
    assert summary["cout_total_usd"] == pytest.approx(0.009)


def test_cadence_sans_attente_avant_la_premiere_consommation():
    pace = Pacer(tokens_per_minute=20_000)
    assert pace.next_at == 0.0
    pace.consumed(10_000)  # 30 s d'espacement avant l'essai suivant
    assert pace.next_at > 0.0

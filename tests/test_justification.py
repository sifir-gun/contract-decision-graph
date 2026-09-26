"""Justification des constats par le corpus (décision du 26/09/2026) : statut du domaine et
verdict. Python pur, sans LLM ni CRAG : le résumé de la recherche est construit à la main.

Le corpus sert à justifier un constat. Une clause sans constat n'exige aucune référence ; une
clause qui déclenche une pénalité ou un blocage sans référence en vigueur rend le domaine
INSUFFISANT, avec un constat qui la nomme.
"""

import pytest
from doubles import ABSENT, clauses
from pydantic import ValidationError

from cdg.domain.config import load_config
from cdg.domain.justification import justify
from cdg.domain.models import AgentVerdict, ClauseRetrieval, RetrievalTrace
from cdg.domain.rules import RULES

CONFIG = load_config()


def assess(domain, **overrides):
    return RULES[domain](clauses(**overrides), CONFIG)


def trace(findings=(), **retained) -> RetrievalTrace:
    """Résumé du CRAG : références retenues par clause, constats propres au CRAG."""
    return RetrievalTrace(
        clauses=[
            ClauseRetrieval(
                kind=kind, queries=["q"], passes=1, retained=refs, expired=[]
            )
            for kind, refs in retained.items()
        ],
        findings=list(findings),
    )


def lacking(kind):
    return (
        "référentiel insuffisant : aucune référence en vigueur pour justifier le constat de "
        f"la clause {kind}"
    )


def test_sans_constat_aucune_reference_exigee():
    v = justify(assess("financier"), trace())
    assert (v.retrieval_status, v.score, v.hard_block) == ("OK", 1.0, False)
    assert (v.findings, v.evidence_ids, v.retrieval) == ([], [], trace())


def test_penalite_justifiee():
    a = assess("financier", penalites_execution=ABSENT)
    v = justify(a, trace(penalites_execution=["C. civ., art. 1231-5", "Fiche"]))
    assert (v.retrieval_status, v.score) == ("OK", 0.6)
    assert v.evidence_ids == ["C. civ., art. 1231-5", "Fiche"]
    assert v.findings == [f.text for f in a.findings]


def test_penalite_sans_reference_rend_le_domaine_insuffisant_en_nommant_la_clause():
    v = justify(
        assess("financier", penalites_execution=ABSENT),
        trace(penalites_execution=[]),
    )
    assert v.retrieval_status == "INSUFFISANT" and v.evidence_ids == []
    assert v.findings[-1] == lacking("penalites_execution")
    assert v.score == 0.6  # le statut ne touche pas le score


def test_blocage_sans_reference_insuffisant_et_blocage_garde():
    v = justify(assess("juridique", responsabilite_acheteur=None), trace())
    assert (v.retrieval_status, v.hard_block) == ("INSUFFISANT", True)
    assert v.findings[-1] == lacking("responsabilite_acheteur")


def test_seule_la_clause_sans_reference_est_nommee():
    a = assess("financier", penalites_execution=ABSENT, delai_paiement=90)
    v = justify(a, trace(penalites_execution=["Fiche"], delai_paiement=[]))
    assert v.retrieval_status == "INSUFFISANT" and v.evidence_ids == ["Fiche"]
    assert [f for f in v.findings if "insuffisant" in f] == [lacking("delai_paiement")]


def test_clause_a_justifier_absente_du_resume_insuffisant_par_prudence():
    v = justify(assess("operationnel", duree_engagement=48), trace())
    assert v.retrieval_status == "INSUFFISANT"
    assert v.findings[-1] == lacking("duree_engagement")


def test_constat_d_information_sans_reference_signale_sans_effet_sur_le_statut():
    a = assess("financier", delai_paiement=ABSENT)  # délai supplétif : aucune pénalité
    v = justify(a, trace(delai_paiement=[]))
    assert (v.retrieval_status, v.score) == ("OK", 1.0)
    assert v.findings[-1] == (
        "constat de la clause delai_paiement sans référence en vigueur : information seule, "
        "sans effet sur le statut"
    )


def test_constat_d_information_justifie():
    a = assess("financier", delai_paiement=ABSENT)
    v = justify(a, trace(delai_paiement=["C. com., art. L441-10"]))
    assert v.retrieval_status == "OK" and v.findings == [f.text for f in a.findings]


def test_ordre_des_constats_regles_puis_crag_puis_justification():
    a = assess("financier", penalites_execution=ABSENT)
    v = justify(a, trace(["référence expirée : L441-10"], penalites_execution=[]))
    assert v.findings == [
        "pénalités d'exécution absentes",
        "référence expirée : L441-10",
        lacking("penalites_execution"),
    ]


def test_references_dedoublonnees_dans_l_ordre_des_clauses():
    a = assess("financier", penalites_execution=ABSENT, delai_paiement=90)
    v = justify(a, trace(penalites_execution=["A", "B"], delai_paiement=["B", "C"]))
    assert v.evidence_ids == ["A", "B", "C"]


# --- Rattachement de chaque constat à sa clause (J4, explication par constat) -------------


def test_chaque_constat_rattache_a_sa_clause_crag_sans_clause():
    a = assess("financier", penalites_execution=ABSENT, delai_paiement=None)
    v = justify(
        a,
        trace(
            ["référence expirée : L441-10"],
            penalites_execution=[],
            delai_paiement=["X"],
        ),
    )
    assert v.finding_kinds == [
        "penalites_execution",
        "delai_paiement",
        None,  # constat du CRAG : propre à la recherche
        "penalites_execution",  # référentiel insuffisant
    ]
    assert len(v.finding_kinds) == len(v.findings)


def test_constat_d_information_sans_reference_rattache_a_sa_clause():
    a = assess("financier", delai_paiement=ABSENT)
    v = justify(a, trace(delai_paiement=[]))
    assert v.finding_kinds == ["delai_paiement", "delai_paiement"]


def test_rattachement_d_une_autre_longueur_que_les_constats_refuse():
    v = justify(assess("financier", penalites_execution=ABSENT), trace())
    with pytest.raises(ValidationError, match="finding_kinds"):
        AgentVerdict.model_validate(
            {**v.model_dump(), "finding_kinds": ["penalites_execution"]}
        )


def test_verdict_sans_rattachement_lisible():
    # verdicts d'avant le J4 (checkpoints) : aucun rattachement, constats non rattachés
    v = justify(assess("financier", penalites_execution=ABSENT), trace())
    legacy = v.model_dump(exclude={"finding_kinds"})
    assert AgentVerdict.model_validate(legacy).finding_kinds == []

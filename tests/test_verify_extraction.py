"""verify_extraction : citations mot pour mot dans le texte masqué, types attendus, essais."""

import pytest
from doubles import ABSENT, CONTRACT_TEXT, clauses

from cdg.application.nodes.verify_extraction import verify_extraction
from cdg.domain.config import load_config
from cdg.domain.models import Clause, NodeFailure
from cdg.domain.verification import check_extraction, normalize

CONFIG = load_config()


def verify(items, attempts=1, text=CONTRACT_TEXT):
    state = {"raw_text": text, "clauses": items, "extraction_attempts": attempts}
    return verify_extraction(state, decision_config=CONFIG)


def invented(kind="revision_prix", quote="Les prix sont fixes pour toute la durée."):
    return [
        c if c.kind != kind else Clause(kind=kind, present=True, quote=quote, value=2.0)
        for c in clauses()
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("L’acheteur « s’engage »  à payer", "L'acheteur \" s'engage \" à payer"),
        ("durée – 36 mois\n\tsans reconduction", "durée - 36 mois sans reconduction"),
        ("ﬁn du contrat", "fin du contrat"),  # ligature, NFKC
    ],
)
def test_normalisation(raw, expected):
    assert normalize(raw) == expected


def test_normalisation_garde_la_casse():
    assert normalize("Article") != normalize("article")


def test_extraction_conforme_route_vers_les_analystes():
    assert verify(clauses()) == {"route": "analysts"}


def test_citation_typographique_differente_acceptee():
    text = CONTRACT_TEXT + "L’acheteur s’engage à payer.\n"
    items = invented(quote="L'acheteur s'engage à payer.")
    assert verify(items, text=text) == {"route": "analysts"}


def test_clause_absente_non_verifiee():
    assert verify(clauses(revision_prix=ABSENT)) == {"route": "analysts"}


def test_citation_introuvable_premier_essai_reextraction():
    out = verify(invented(), attempts=1)
    assert out == {
        "route": "extract_clauses",
        "extraction_feedback": ["citation introuvable: revision_prix"],
    }


def test_citation_introuvable_apres_le_dernier_essai_escalade():
    out = verify(invented(), attempts=CONFIG.extraction.max_attempts)
    assert out == {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": {
            "stage": "extraction",
            "attempts": 2,
            "problems": ["citation introuvable: revision_prix"],
        },
    }


def test_types_manquants_et_en_double_signales():
    items = [c for c in clauses() if c.kind != "duree_engagement"] + [clauses()[0]]
    out = verify(items)
    assert out["extraction_feedback"] == [
        "clause manquante: duree_engagement",
        "clause en double: responsabilite_acheteur",
    ]


def test_citations_comparees_au_texte_masque():
    # le texte de l'état est masqué : une citation portant la donnée d'origine est introuvable
    text = CONTRACT_TEXT + "Contact : [EMAIL].\n"
    original = invented(quote="Contact : jeanne.martin@exemple.fr.")
    masked = invented(quote="Contact : [EMAIL].")
    assert verify(original, text=text)["extraction_feedback"] == [
        "citation introuvable: revision_prix"
    ]
    assert verify(masked, text=text) == {"route": "analysts"}


def test_categorie_de_transfert_obligatoire_si_presente():
    out = verify(clauses(categories={"transfert_hors_ue": None}))
    assert out["extraction_feedback"] == ["catégorie manquante: transfert_hors_ue"]


def test_categorie_inattendue_sur_un_autre_type():
    items = [
        c if c.kind != "revision_prix" else c.model_copy(update={"category": "certification"})
        for c in clauses()
    ]
    assert verify(items)["extraction_feedback"] == ["catégorie inattendue: revision_prix"]


def _with(kind, **update):
    return [c if c.kind != kind else c.model_copy(update=update) for c in clauses()]


def test_delai_chiffre_exige_son_point_de_depart():
    out = verify(_with("delai_paiement", category=None))
    assert out["extraction_feedback"] == ["catégorie manquante: delai_paiement"]


def test_delai_non_chiffre_sans_point_de_depart_accepte():
    assert verify(_with("delai_paiement", value=None, category=None)) == {"route": "analysts"}


@pytest.mark.parametrize(
    ("kind", "category"),
    [("transfert_hors_ue", "fin_de_mois"), ("delai_paiement", "sans_transfert")],
)
def test_categorie_d_un_autre_type_refusee(kind, category):
    out = verify(_with(kind, category=category))
    assert out["extraction_feedback"] == [f"catégorie invalide: {kind}"]


def test_extraction_en_echec_escalade_sans_verification():
    failure = NodeFailure(node="extract_clauses", error="LLMOutputError", message="m", attempts=1)
    state = {"raw_text": CONTRACT_TEXT, "extraction_attempts": 0, "failures": [failure]}
    out = verify_extraction(state, decision_config=CONFIG)
    assert out == {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": {"stage": "noeuds", "failures": [failure.model_dump()]},
    }


def test_domaine_trois_issues_de_la_verification():
    assert check_extraction(CONTRACT_TEXT, clauses(), 1, 2).outcome == "verified"
    retry = check_extraction(CONTRACT_TEXT, invented(), 1, 2)
    assert retry.outcome == "retry" and retry.failure_report is None and retry.problems
    final = check_extraction(CONTRACT_TEXT, invented(), 2, 2)
    assert final.outcome == "escalate"
    assert final.failure_report == {
        "stage": "extraction",
        "attempts": 2,
        "problems": final.problems,
    }

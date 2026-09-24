"""verify_extraction : citations mot pour mot dans le texte masqué, types attendus, essais."""

import pytest
from doubles import ABSENT, CONTRACT_TEXT, clauses

from cdg.config import load_config
from cdg.nodes.verify_extraction import normalize, verify_extraction
from cdg.state import Clause

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

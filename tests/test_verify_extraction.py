"""verify_extraction : citations mot pour mot dans le texte masqué, types attendus, essais."""

import pytest
import yaml
from doubles import ABSENT, CONTRACT_TEXT, clauses
from pydantic import ValidationError

from cdg.application.nodes.verify_extraction import verify_extraction
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.domain.models import Clause, NodeFailure
from cdg.domain.verification import check_extraction, normalize

CONFIG = load_config()


def verify(items, attempts=1, text=CONTRACT_TEXT):
    state = {"raw_text": text, "clauses": items, "extraction_attempts": attempts}
    return verify_extraction(state, decision_config=CONFIG)


def check(items, attempts, text=CONTRACT_TEXT):
    return check_extraction(
        text, items, attempts, 2, absence_terms=CONFIG.extraction.absence_terms
    )


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
        c
        if c.kind != "revision_prix"
        else c.model_copy(update={"category": "certification"})
        for c in clauses()
    ]
    assert verify(items)["extraction_feedback"] == [
        "catégorie inattendue: revision_prix"
    ]


def _with(kind, **update):
    return [c if c.kind != kind else c.model_copy(update=update) for c in clauses()]


def test_delai_chiffre_exige_son_point_de_depart():
    out = verify(_with("delai_paiement", category=None))
    assert out["extraction_feedback"] == ["catégorie manquante: delai_paiement"]


def test_delai_non_chiffre_sans_point_de_depart_accepte():
    assert verify(_with("delai_paiement", value=None, category=None)) == {
        "route": "analysts"
    }


@pytest.mark.parametrize(
    ("kind", "category"),
    [("transfert_hors_ue", "fin_de_mois"), ("delai_paiement", "sans_transfert")],
)
def test_categorie_d_un_autre_type_refusee(kind, category):
    out = verify(_with(kind, category=category))
    assert out["extraction_feedback"] == [f"catégorie invalide: {kind}"]


def test_extraction_en_echec_escalade_sans_verification():
    failure = NodeFailure(
        node="extract_clauses", error="LLMOutputError", message="m", attempts=1
    )
    state = {"raw_text": CONTRACT_TEXT, "extraction_attempts": 0, "failures": [failure]}
    out = verify_extraction(state, decision_config=CONFIG)
    assert out == {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": {"stage": "noeuds", "failures": [failure.model_dump()]},
    }


def test_domaine_trois_issues_de_la_verification():
    assert check(clauses(), 1).outcome == "verified"
    retry = check(invented(), 1)
    assert retry.outcome == "retry" and retry.failure_report is None and retry.problems
    final = check(invented(), 2)
    assert final.outcome == "escalate"
    assert final.failure_report == {
        "stage": "extraction",
        "attempts": 2,
        "problems": final.problems,
    }


# --- Correction 2 de la série 4 : vérification des absences (décision du 26/09) ------------

REVISION = (
    "Les prix sont révisés chaque année selon l'évolution des coûts, sans plafond."
)


MENTIONED = "clause déclarée absente, mais le contrat contient « prix sont révisés »: "


def test_clause_absente_mais_evoquee_reextraction_avec_retour_cible():
    text = CONTRACT_TEXT + REVISION + "\n"
    out = verify(clauses(revision_prix=ABSENT), attempts=1, text=text)
    assert out == {
        "route": "extract_clauses",
        "extraction_feedback": [MENTIONED + "revision_prix"],
    }


def test_clause_absente_mais_evoquee_au_dernier_essai_escalade():
    text = CONTRACT_TEXT + REVISION + "\n"
    out = verify(clauses(revision_prix=ABSENT), attempts=2, text=text)
    assert (out["route"], out["proposed_decision"]) == ("human_review", "ESCALADE")
    assert out["failure_report"] == {
        "stage": "extraction",
        "attempts": 2,
        "problems": [MENTIONED + "revision_prix"],
    }


def test_termes_compares_sans_casse_ni_typographie():
    text = CONTRACT_TEXT + "LA RESPONSABILITÉ DE L’ACHETEUR n'est pas limitée.\n"
    out = verify(clauses(responsabilite_acheteur=ABSENT), text=text)
    expected = (
        "clause déclarée absente, mais le contrat contient « responsabilité de "
        "l'acheteur »: responsabilite_acheteur"
    )
    assert out["extraction_feedback"] == [expected]


def test_clause_absente_non_evoquee_acceptee():
    # pénalités de retard de paiement de l'acheteur : pas des pénalités d'exécution
    text = CONTRACT_TEXT + (
        "Tout retard de paiement rend exigibles des pénalités de retard égales à trois "
        "fois le taux d'intérêt légal.\n"
    )
    assert verify(clauses(penalites_execution=ABSENT), text=text) == {
        "route": "analysts"
    }


def test_termes_d_absence_un_jeu_par_type_dans_la_configuration():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    del data["extraction"]["absence_terms"]["revision_prix"]
    with pytest.raises(ValidationError, match="types manquants \\['revision_prix'\\]"):
        DecisionConfig.model_validate(data)
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["extraction"]["absence_terms"]["preavis_resiliation"] = ["préavis", "Préavis"]
    with pytest.raises(ValidationError, match="terme répété pour preavis_resiliation"):
        DecisionConfig.model_validate(data)

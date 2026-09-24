"""Règles par domaine (spec, « Règles par domaine »). Python pur, sans LLM."""

import pytest
import yaml
from doubles import ABSENT, clauses

from cdg.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.rules import RULES
from cdg.state import DOMAINS, Clause

CONFIG = load_config()


def run(domain, status="OK", config=CONFIG, **overrides):
    return RULES[domain](clauses(**overrides), status, config)


def config_with(section: str, key: str, value) -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"][section][key] = value
    return DecisionConfig.model_validate(data)


def test_registre_couvre_les_quatre_domaines():
    assert set(RULES) == set(DOMAINS)


@pytest.mark.parametrize("domain", DOMAINS)
def test_contrat_favorable(domain):
    v = run(domain)
    assert (v.domain, v.score, v.hard_block, v.findings) == (domain, 1.0, False, [])
    assert v.retrieval_status == "OK" and v.evidence_ids == []


# --- juridique ------------------------------------------------------------------


def test_juridique_responsabilite_acheteur_illimitee_bloque():
    v = run("juridique", responsabilite_acheteur=None)
    assert v.hard_block and v.score == 1.0  # le blocage ne touche pas le score
    assert "illimitée" in v.findings[0]


def test_juridique_responsabilite_acheteur_absente_ne_bloque_pas():
    assert not run("juridique", responsabilite_acheteur=ABSENT).hard_block


@pytest.mark.parametrize(
    "cap,score",
    [
        (50, 0.5),
        (99.9, 0.5),
        (100, 1.0),
        (99.9999999, 1.0),  # arrondi avant comparaison
        (None, 1.0),  # illimitée : favorable
        (ABSENT, 1.0),
    ],
)
def test_juridique_plafond_fournisseur(cap, score):
    assert run("juridique", responsabilite_fournisseur=cap).score == score


# --- financier ------------------------------------------------------------------


def test_financier_revision_de_prix_non_plafonnee_bloque():
    v = run("financier", revision_prix=None)
    assert v.hard_block and "non plafonnée" in v.findings[0]


def test_financier_revision_de_prix_absente_ne_bloque_pas():
    assert not run("financier", revision_prix=ABSENT).hard_block


@pytest.mark.parametrize(
    "cap,score",
    [
        (ABSENT, 0.6),
        (4.99, 0.6),
        (5, 1.0),
        (None, 1.0),  # non plafonnées : favorable
    ],
)
def test_financier_penalites_de_retard(cap, score):
    assert run("financier", penalites_retard=cap).score == score


# --- conformite -----------------------------------------------------------------


def test_conformite_donnees_sans_accord_de_traitement_bloque():
    v = run("conformite", accord_traitement_donnees=ABSENT)
    assert v.hard_block and "art. 28" in v.findings[0]
    assert v.score == 1.0


@pytest.mark.parametrize("donnees,accord", [(None, None), (ABSENT, ABSENT), (ABSENT, None)])
def test_conformite_sans_blocage(donnees, accord):
    v = run("conformite", donnees_personnelles=donnees, accord_traitement_donnees=accord)
    assert not v.hard_block


# --- operationnel ---------------------------------------------------------------


@pytest.mark.parametrize("months,score", [(37, 0.7), (36, 1.0), (ABSENT, 1.0)])
def test_operationnel_duree_engagement(months, score):
    assert run("operationnel", duree_engagement=months).score == score


@pytest.mark.parametrize("months,score", [(7, 0.7), (6, 1.0), (ABSENT, 1.0)])
def test_operationnel_preavis(months, score):
    assert run("operationnel", preavis_resiliation=months).score == score


def test_operationnel_penalites_cumulees():
    v = run("operationnel", duree_engagement=48, preavis_resiliation=12)
    assert v.score == 0.4 and len(v.findings) == 2


@pytest.mark.parametrize("kind", ["duree_engagement", "preavis_resiliation"])
def test_operationnel_duree_non_chiffree_penalisee_par_prudence(kind):
    v = run("operationnel", **{kind: None})
    assert v.score == 0.7 and "non chiffré" in v.findings[0]


# --- transverses ----------------------------------------------------------------


@pytest.mark.parametrize("domain", DOMAINS)
def test_insuffisant_consigne_dans_les_constats(domain):
    v = run(domain, status="INSUFFISANT")
    assert v.retrieval_status == "INSUFFISANT"
    assert any("insuffisant" in f for f in v.findings)


def test_blocage_et_insuffisant_coexistent():
    v = run("juridique", status="INSUFFISANT", responsabilite_acheteur=None)
    assert v.hard_block and len(v.findings) == 2


def test_seuils_lus_dans_la_configuration():
    cfg = config_with("operationnel", "commitment_max_months", 48)
    assert run("operationnel", config=cfg, duree_engagement=40).score == 1.0
    assert run("operationnel", duree_engagement=40).score == 0.7


def test_score_borne_a_zero():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"]["operationnel"] |= {"commitment_score_penalty": 0.8, "notice_score_penalty": 0.8}
    cfg = DecisionConfig.model_validate(data)
    assert run("operationnel", config=cfg, duree_engagement=48, preavis_resiliation=12).score == 0.0


@pytest.mark.parametrize("domain", DOMAINS)
def test_clause_attendue_manquante_leve_une_erreur(domain):
    incomplete = [c for c in clauses() if c.kind != _kind_read_by(domain)]
    with pytest.raises(ValueError, match="clause"):
        RULES[domain](incomplete, "OK", CONFIG)


def test_clause_en_double_leve_une_erreur():
    doubled = clauses() + [Clause(kind="revision_prix", present=True, quote="bis", value=1.0)]
    with pytest.raises(ValueError, match="revision_prix"):
        RULES["financier"](doubled, "OK", CONFIG)


def _kind_read_by(domain: str) -> str:
    return {
        "juridique": "responsabilite_acheteur",
        "financier": "penalites_retard",
        "conformite": "donnees_personnelles",
        "operationnel": "preavis_resiliation",
    }[domain]

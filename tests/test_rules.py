"""Règles par domaine (spec, « Règles par domaine »). Python pur, sans LLM ni CRAG.

Les règles passent avant le CRAG : chaque constat est rattaché à la clause qui le déclenche,
avec son effet (blocage, pénalité ou information). La justification par le corpus est testée
dans `test_justification.py`.
"""

import pytest
import yaml
from doubles import ABSENT, clauses

from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.domain.models import DOMAINS, Clause
from cdg.domain.rules import RULES, Assessment, RuleFinding

CONFIG = load_config()


def run(domain, config=CONFIG, **overrides) -> Assessment:
    return RULES[domain](clauses(**overrides), config)


def texts(a: Assessment) -> list[str]:
    return [f.text for f in a.findings]


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
    assert v.kinds_to_justify() == []  # aucun constat : rien à justifier


# --- juridique ------------------------------------------------------------------


def test_juridique_responsabilite_acheteur_illimitee_bloque():
    v = run("juridique", responsabilite_acheteur=None)
    assert v.hard_block and v.score == 1.0  # le blocage ne touche pas le score
    assert "illimitée" in v.findings[0].text


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
    assert v.hard_block and "non plafonnée" in v.findings[0].text


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
def test_financier_penalites_d_execution(cap, score):
    assert run("financier", penalites_execution=cap).score == score


# --- conformite -----------------------------------------------------------------


def test_conformite_donnees_sans_accord_de_traitement_bloque():
    v = run("conformite", accord_traitement_donnees=ABSENT)
    assert v.hard_block and "art. 28" in v.findings[0].text
    assert v.score == 1.0


@pytest.mark.parametrize(
    "donnees,accord", [(None, None), (ABSENT, ABSENT), (ABSENT, None)]
)
def test_conformite_sans_blocage(donnees, accord):
    v = run(
        "conformite", donnees_personnelles=donnees, accord_traitement_donnees=accord
    )
    assert not v.hard_block


# --- conformite : transfert hors UE (RGPD, art. 44 à 46) ------------------------------


@pytest.mark.parametrize(
    "category",
    [
        "sans_transfert",
        "decision_adequation",
        "clauses_contractuelles_types",
        "clauses_contractuelles_ad_hoc_autorisees",  # autorisation mentionnée (art. 46, 3, a)
        "regles_entreprise_contraignantes",
        "code_conduite",
        "certification",
    ],
)
def test_transfert_encadre_ou_absent_sans_penalite(category):
    v = run("conformite", categories={"transfert_hors_ue": category})
    assert (v.hard_block, v.score) == (False, 1.0)


def test_clauses_ad_hoc_sans_autorisation_penalite_et_constat():
    v = run(
        "conformite", categories={"transfert_hors_ue": "clauses_contractuelles_ad_hoc"}
    )
    assert (v.hard_block, v.score) == (False, 0.7)
    expected = (
        "autorisation de l'autorité de contrôle à vérifier : transfert hors UE fondé sur des "
        "clauses contractuelles ad hoc, sans mention d'autorisation (art. 46, par. 3, a) RGPD)"
    )
    assert texts(v) == [expected]


def test_categories_a_verifier_lues_dans_la_configuration():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"]["conformite"]["transfer_authorization_to_verify"] = []
    cfg = DecisionConfig.model_validate(data)
    v = run(
        "conformite",
        config=cfg,
        categories={"transfert_hors_ue": "clauses_contractuelles_ad_hoc"},
    )
    assert v.hard_block  # ni garantie reconnue, ni catégorie à vérifier : prudence


def test_transfert_annonce_sans_garantie_bloque():
    v = run("conformite", categories={"transfert_hors_ue": "aucune_garantie"})
    assert v.hard_block and "sans garantie" in v.findings[0].text


def test_transfert_sans_categorie_bloque_par_prudence():
    v = run("conformite", categories={"transfert_hors_ue": None})
    assert v.hard_block


def test_localisation_non_precisee_penalisee_et_a_verifier():
    v = run("conformite", transfert_hors_ue=ABSENT)
    assert (v.hard_block, v.score) == (False, 0.7)
    assert "à vérifier" in v.findings[0].text


def test_localisation_sans_objet_sans_donnees_personnelles():
    v = run(
        "conformite",
        transfert_hors_ue=ABSENT,
        donnees_personnelles=ABSENT,
        accord_traitement_donnees=ABSENT,
    )
    assert (v.hard_block, v.score, v.findings) == (False, 1.0, [])


def test_garanties_reconnues_lues_dans_la_configuration():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"]["conformite"]["transfer_safeguards"] = ["decision_adequation"]
    cfg = DecisionConfig.model_validate(data)
    v = run("conformite", config=cfg, categories={"transfert_hors_ue": "certification"})
    assert v.hard_block


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
    assert v.score == 0.7 and "non chiffré" in v.findings[0].text


# --- transverses ----------------------------------------------------------------


def test_seuils_lus_dans_la_configuration():
    cfg = config_with("operationnel", "commitment_max_months", 48)
    assert run("operationnel", config=cfg, duree_engagement=40).score == 1.0
    assert run("operationnel", duree_engagement=40).score == 0.7


def test_score_borne_a_zero():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"]["operationnel"] |= {
        "commitment_score_penalty": 0.8,
        "notice_score_penalty": 0.8,
    }
    cfg = DecisionConfig.model_validate(data)
    assert (
        run(
            "operationnel", config=cfg, duree_engagement=48, preavis_resiliation=12
        ).score
        == 0.0
    )


@pytest.mark.parametrize("domain", DOMAINS)
def test_clause_attendue_manquante_leve_une_erreur(domain):
    incomplete = [c for c in clauses() if c.kind != _kind_read_by(domain)]
    with pytest.raises(ValueError, match="clause"):
        RULES[domain](incomplete, CONFIG)


def test_clause_en_double_leve_une_erreur():
    doubled = clauses() + [
        Clause(kind="revision_prix", present=True, quote="bis", value=1.0)
    ]
    with pytest.raises(ValueError, match="revision_prix"):
        RULES["financier"](doubled, CONFIG)


def _kind_read_by(domain: str) -> str:
    return {
        "juridique": "responsabilite_acheteur",
        "financier": "penalites_execution",
        "conformite": "donnees_personnelles",
        "operationnel": "preavis_resiliation",
    }[domain]


# --- financier : délai de paiement (C. com., art. L441-10) --------------------------------


def delay(value, basis="date_facture"):
    return run("financier", delai_paiement=value, categories={"delai_paiement": basis})


@pytest.mark.parametrize(
    ("value", "basis", "score"),
    [
        (60, "date_facture", 1.0),
        (61, "date_facture", 0.8),
        (45, "fin_de_mois", 1.0),
        (46, "fin_de_mois", 0.8),
        (50, "date_facture", 1.0),  # 50 jours date de facture : conforme
        (50, "fin_de_mois", 0.8),  # 50 jours fin de mois : non conforme
        (45, "facture_periodique", 1.0),
        (
            46,
            "facture_periodique",
            0.8,
        ),  # conforme en date de facture, pas en facture périodique
    ],
)
def test_financier_delai_de_paiement_selon_son_point_de_depart(value, basis, score):
    v = delay(value, basis)
    assert v.score == score and not v.hard_block
    if score < 1.0:
        label = {
            "date_facture": "date de facture",
            "fin_de_mois": "fin de mois",
            "facture_periodique": "après une facture périodique",
        }[basis]
        limit = 60 if basis == "date_facture" else 45
        expected = f"délai non conforme, à renégocier : {value} jours {label}, au-delà de {limit} jours"
        assert texts(v) == [expected]


def test_financier_delai_non_chiffre_penalite_par_prudence():
    v = delay(None, None)
    assert v.score == 0.8 and not v.hard_block
    assert texts(v) == [
        "délai de paiement non chiffré : pénalité par prudence, délai non conforme, à renégocier"
    ]


def test_financier_delai_absent_sans_penalite_avec_le_delai_supplétif():
    v = run("financier", delai_paiement=ABSENT)
    assert v.score == 1.0
    [finding] = texts(v)
    assert "trente jours après la date de réception des marchandises" in finding
    assert "C. com., art. L441-10" in finding


def test_financier_delai_sans_point_de_depart_seuil_le_plus_strict():
    # ne se produit pas après verify_extraction (catégorie exigée) : règle prudente malgré tout
    assert delay(50, None).score == 0.8


def test_financier_seuils_du_delai_dans_la_configuration():
    config = config_with("financier", "payment_delay_max_days_invoice", 90)
    assert run("financier", config=config, delai_paiement=80).score == 1.0


def test_financier_cumul_des_deux_penalites():
    v = run("financier", penalites_execution=ABSENT, delai_paiement=90)
    assert v.score == 0.4 and len(v.findings) == 2


def test_financier_seuil_des_factures_periodiques_dans_la_configuration():
    config = config_with("financier", "payment_delay_max_days_periodic_invoice", 50)
    assert (
        run(
            "financier",
            config=config,
            delai_paiement=50,
            categories={"delai_paiement": "facture_periodique"},
        ).score
        == 1.0
    )


# --- constats rattachés à leur clause (décision du 26/09/2026) --------------------------


@pytest.mark.parametrize(
    ("domain", "overrides", "categories", "expected"),
    [
        (
            "juridique",
            {"responsabilite_acheteur": None},
            {},
            [("responsabilite_acheteur", "blocage")],
        ),
        (
            "juridique",
            {"responsabilite_fournisseur": 50},
            {},
            [("responsabilite_fournisseur", "penalite")],
        ),
        ("financier", {"revision_prix": None}, {}, [("revision_prix", "blocage")]),
        (
            "financier",
            {"penalites_execution": ABSENT},
            {},
            [("penalites_execution", "penalite")],
        ),
        ("financier", {"delai_paiement": 90}, {}, [("delai_paiement", "penalite")]),
        (
            "financier",
            {"delai_paiement": None},
            {"delai_paiement": None},
            [("delai_paiement", "penalite")],
        ),
        (
            "financier",
            {"delai_paiement": ABSENT},
            {},
            [("delai_paiement", "information")],
        ),
        (
            "conformite",
            {"accord_traitement_donnees": ABSENT},
            {},
            [("accord_traitement_donnees", "blocage")],
        ),
        (
            "conformite",
            {},
            {"transfert_hors_ue": "clauses_contractuelles_types"},
            [("transfert_hors_ue", "information")],
        ),
        (
            "conformite",
            {},
            {"transfert_hors_ue": "clauses_contractuelles_ad_hoc"},
            [("transfert_hors_ue", "penalite")],
        ),
        (
            "conformite",
            {},
            {"transfert_hors_ue": "aucune_garantie"},
            [("transfert_hors_ue", "blocage")],
        ),
        (
            "conformite",
            {"transfert_hors_ue": ABSENT},
            {},
            [("transfert_hors_ue", "penalite")],
        ),
        (
            "operationnel",
            {"duree_engagement": 48},
            {},
            [("duree_engagement", "penalite")],
        ),
        (
            "operationnel",
            {"preavis_resiliation": None},
            {},
            [("preavis_resiliation", "penalite")],
        ),
    ],
)
def test_chaque_constat_rattache_a_sa_clause_avec_son_effet(
    domain, overrides, categories, expected
):
    v = run(domain, categories=categories, **overrides)
    assert [(f.kind, f.effect) for f in v.findings] == expected


def test_clauses_a_justifier_et_clauses_qui_exigent_une_reference():
    v = run("financier", penalites_execution=ABSENT, delai_paiement=ABSENT)
    assert v.kinds_to_justify() == ["penalites_execution", "delai_paiement"]
    assert v.kinds_requiring_reference() == [
        "penalites_execution"
    ]  # le délai informe seulement


def test_clause_a_justifier_une_seule_fois():
    v = run("operationnel", duree_engagement=48, preavis_resiliation=12)
    doubled = Assessment(domain="operationnel", findings=[*v.findings, v.findings[0]])
    assert doubled.kinds_to_justify() == ["duree_engagement", "preavis_resiliation"]


@pytest.mark.parametrize(
    "fields",
    [
        {"effect": "penalite"},  # pénalité sans montant
        {"effect": "blocage", "penalty": 0.2},  # montant hors pénalité
        {"effect": "information", "penalty": 0.2},
    ],
)
def test_constat_incoherent_refuse(fields):
    with pytest.raises(ValueError, match="pénalité"):
        RuleFinding(kind="duree_engagement", text="t", **fields)

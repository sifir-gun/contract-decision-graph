"""Règles par domaine (spec, « Règles par domaine »). Python pur, sans LLM ni CRAG.

Les règles passent avant le CRAG : chaque constat est rattaché à la clause qui le déclenche,
avec son effet (blocage, pénalité ou information). La justification par le corpus est testée
dans `test_justification.py`.
"""

import pytest
import yaml
from doubles import (
    ABSENT,
    CONTRACT_TEXT_SOUS_TRAITANCE,
    TEST_VALUES_SOUS_TRAITANCE,
    clauses,
    clauses_sous_traitance,
)

from cdg.domain import verification
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.domain.models import (
    CERTIFICATION_REQUIREMENTS,
    DESIGN_PROTECTIONS,
    DOMAINS,
    EXECUTION_SCHEDULES,
    Clause,
)
from cdg.domain.numeric import rounded
from cdg.domain.profiles import PROFILES, SOUS_TRAITANCE
from cdg.domain.rules import RULES, Assessment, RuleFinding, _common
from cdg.domain.rules.sous_traitance import RULES_SOUS_TRAITANCE
from cdg.domain.text import normalize

CONFIG = load_config()


def run(domain, config=CONFIG, **overrides) -> Assessment:
    return RULES["fournisseur"][domain](clauses(**overrides), config)


def texts(a: Assessment) -> list[str]:
    return [f.text for f in a.findings]


def config_with(section: str, key: str, value) -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["rules"][section][key] = value
    return DecisionConfig.model_validate(data)


def test_registre_un_jeu_de_regles_par_profil():
    assert set(RULES) == set(PROFILES)
    assert set(RULES["fournisseur"]) == set(DOMAINS)


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
        RULES["fournisseur"][domain](incomplete, CONFIG)


def test_clause_en_double_leve_une_erreur():
    doubled = clauses() + [
        Clause(kind="revision_prix", present=True, quote="bis", value=1.0)
    ]
    with pytest.raises(ValueError, match="revision_prix"):
        RULES["fournisseur"]["financier"](doubled, CONFIG)


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


# --- règles communes, partagées entre profils (tâche 5 de la PR 2 des profils) ----------------
# Les aides de `_common` prennent les libellés de leurs textes en paramètre : ces tests leur
# donnent ceux du profil de la sous-traitance, pas ceux du fournisseur, que le test des
# constats figés (tests/test_demo.py) garde déjà.

PARTIES = SOUS_TRAITANCE.parties  # « du donneur d'ordre », « du sous-traitant »


def stipulated(kind, value, present=True, category=None) -> Clause:
    quote = "Article synthétique." if present else ""
    return Clause(
        kind=kind, present=present, quote=quote, value=value, category=category
    )


def finding(kind, effect, text, penalty=None) -> RuleFinding:
    return RuleFinding(kind=kind, effect=effect, text=text, penalty=penalty)


def test_commun_responsabilite_illimitee_bloque_avec_la_partie_nommee():
    c = stipulated("responsabilite_donneur_ordre", None)
    assert _common.uncapped_liability(c, PARTIES.client_of) == [
        finding(
            "responsabilite_donneur_ordre",
            "blocage",
            "blocage : responsabilité du donneur d'ordre illimitée",
        )
    ]


@pytest.mark.parametrize("value", [100.0, 0.0])
def test_commun_responsabilite_plafonnee_sans_constat(value):
    c = stipulated("responsabilite_donneur_ordre", value)
    assert _common.uncapped_liability(c, PARTIES.client_of) == []


def test_commun_responsabilite_absente_sans_constat():
    c = stipulated("responsabilite_donneur_ordre", None, present=False)
    assert _common.uncapped_liability(c, PARTIES.client_of) == []


def cap(c, minimum=100.0, penalty=0.3):
    return _common.cap_below_minimum(
        c, PARTIES.contractor_of, "du montant du contrat", minimum, penalty
    )


def test_commun_plafond_sous_le_minimum_penalise_avec_les_libelles_donnes():
    c = stipulated("responsabilite_sous_traitant", 50.0)
    assert cap(c) == [
        finding(
            "responsabilite_sous_traitant",
            "penalite",
            "plafond de responsabilité du sous-traitant à 50 % du montant du contrat, "
            "sous le minimum de 100 %",
            penalty=0.3,
        )
    ]


def test_commun_plafond_ecrit_avec_la_forme_g_et_la_penalite_donnee():
    c = stipulated("responsabilite_sous_traitant", 99.9)
    [found] = cap(c, minimum=250.5, penalty=0.45)
    assert found.penalty == 0.45
    assert found.text.endswith(
        "à 99.9 % du montant du contrat, sous le minimum de 250.5 %"
    )


@pytest.mark.parametrize(
    "value",
    [
        100.0,  # au minimum
        99.9999999,  # arrondi avant comparaison
        150.0,
        None,  # illimitée : favorable (le blocage est celui de l'autre partie)
    ],
)
def test_commun_plafond_au_minimum_ou_illimite_sans_constat(value):
    assert cap(stipulated("responsabilite_sous_traitant", value)) == []


def test_commun_plafond_absent_sans_constat():
    assert cap(stipulated("responsabilite_sous_traitant", None, present=False)) == []


def test_commun_revision_non_plafonnee_bloque():
    c = stipulated("revision_prix", None)
    assert _common.uncapped_revision(c) == [
        finding("revision_prix", "blocage", "blocage : révision de prix non plafonnée")
    ]


@pytest.mark.parametrize(
    "c",
    [
        stipulated("revision_prix", 3.0),
        stipulated("revision_prix", None, present=False),
    ],
)
def test_commun_revision_plafonnee_ou_absente_sans_constat(c):
    assert _common.uncapped_revision(c) == []


LABEL = "pénalités de retard d'exécution"


def capped(c, minimum=5.0, penalty=0.4):
    return _common.capped_penalties(c, LABEL, minimum, penalty)


def test_commun_penalites_absentes_penalisees_avec_le_libelle_donne():
    c = stipulated("penalites_retard_execution", None, present=False)
    assert capped(c) == [
        finding(
            "penalites_retard_execution",
            "penalite",
            "pénalités de retard d'exécution absentes",
            penalty=0.4,
        )
    ]


def test_commun_penalites_sous_le_minimum_penalisees():
    c = stipulated("penalites_retard_execution", 2.0)
    assert capped(c, penalty=0.35) == [
        finding(
            "penalites_retard_execution",
            "penalite",
            "pénalités de retard d'exécution plafonnées à 2 % du montant du contrat, "
            "sous le minimum de 5 %",
            penalty=0.35,
        )
    ]


@pytest.mark.parametrize(
    "value",
    [
        5.0,  # au minimum
        4.9999999,  # arrondi avant comparaison
        10.0,
        None,  # non plafonnées : favorable
    ],
)
def test_commun_penalites_au_minimum_ou_non_plafonnees_sans_constat(value):
    assert capped(stipulated("penalites_retard_execution", value)) == []


# seuils distincts de ceux du fournisseur, et de ceux du plan : l'aide lit ce qu'on lui donne
LIMITS = {"date_facture": 70.0, "fin_de_mois": 50.0, "facture_periodique": 40.0}


def paid_within(value, basis=None, present=True) -> list[RuleFinding]:
    c = stipulated("delai_paiement", value, present=present, category=basis)
    return _common.payment_delay(c, LIMITS, 0.25)


def test_commun_delai_absent_informe_avec_le_delai_supplétif():
    [found] = paid_within(None, present=False)
    assert (found.kind, found.effect, found.penalty) == (
        "delai_paiement",
        "information",
        None,
    )
    assert "trente jours après la date de réception des marchandises" in found.text
    assert "C. com., art. L441-10, I" in found.text


def test_commun_delai_non_chiffre_penalise_par_prudence():
    assert paid_within(None) == [
        finding(
            "delai_paiement",
            "penalite",
            "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
            "à renégocier",
            penalty=0.25,
        )
    ]


@pytest.mark.parametrize(
    ("value", "basis", "label", "limit"),
    [
        (71.0, "date_facture", "date de facture", 70),
        (51.0, "fin_de_mois", "fin de mois", 50),
        (41.0, "facture_periodique", "après une facture périodique", 40),
        (41.0, None, "point de départ non précisé", 40),  # le seuil le plus strict
        (41.0, "autre", "point de départ non précisé", 40),
    ],
)
def test_commun_delai_au_dela_du_seuil_de_son_point_de_depart(
    value, basis, label, limit
):
    assert paid_within(value, basis) == [
        finding(
            "delai_paiement",
            "penalite",
            f"délai non conforme, à renégocier : {value:g} jours {label}, "
            f"au-delà de {limit} jours",
            penalty=0.25,
        )
    ]


@pytest.mark.parametrize(
    ("value", "basis"),
    [
        (70.0, "date_facture"),
        (50.0, "fin_de_mois"),
        (40.0, "facture_periodique"),
        (40.0, None),
        (40.0000001, "facture_periodique"),  # arrondi avant comparaison
    ],
)
def test_commun_delai_dans_le_seuil_sans_constat(value, basis):
    assert paid_within(value, basis) == []


# Un dictionnaire de seuils qui ne couvre pas exactement les trois points de départ est une
# erreur de programmation : refusé, jamais une bascule en silence sur le seuil le plus strict
# (décision du contrôleur, tâche 6). Les clés sont celles de `DELAY_BASIS_LABELS`.
@pytest.mark.parametrize(
    ("limits", "named"),
    [
        ({"date_facture": 70.0, "fin_de_mois": 50.0}, ["facture_periodique"]),
        ({"fin_de_mois": 50.0, "facture_periodique": 40.0}, ["date_facture"]),
        ({}, ["date_facture", "fin_de_mois", "facture_periodique"]),
        ({**LIMITS, "autre": 30.0}, ["autre"]),
        (
            {"date_facture": 70.0, "fin_de_mois": 50.0, "facture_perodique": 40.0},
            [
                "facture_periodique",
                "facture_perodique",
            ],  # une faute de frappe : les deux
        ),
    ],
)
@pytest.mark.parametrize(
    "c",
    [
        stipulated("delai_paiement", 90.0, category="date_facture"),
        stipulated("delai_paiement", None, category=None),
        stipulated("delai_paiement", None, present=False),
    ],
    ids=["chiffre", "non_chiffre", "absent"],
)
def test_commun_delai_refuse_des_seuils_qui_ne_sont_pas_les_trois_points_de_depart(
    c, limits, named
):
    with pytest.raises(ValueError, match="seuils du délai de paiement") as error:
        _common.payment_delay(c, limits, 0.25)
    assert all(key in str(error.value) for key in named)


def test_commun_delai_accepte_les_trois_points_de_depart_dans_un_autre_ordre():
    reordered = {
        "facture_periodique": 40.0,
        "date_facture": 70.0,
        "fin_de_mois": 50.0,
    }
    c = stipulated("delai_paiement", 71.0, category="date_facture")
    [found] = _common.payment_delay(c, reordered, 0.25)
    assert "au-delà de 70 jours" in found.text


# --- profil sous_traitance (tâche 6 de la PR 2 des profils) --------------------------------
# Les règles du profil, pas encore enregistrées dans `RULES` : le profil ne l'est pas. Les
# valeurs attendues sont celles de la section `sous_traitance.rules` de `config/decision.yaml`
# (valeurs de démonstration) ; `WIRED` donne à chaque réglage une valeur distincte, pour
# prouver que chaque constat lit son propre réglage.


def run_st(domain, config=CONFIG, **overrides) -> Assessment:
    return RULES_SOUS_TRAITANCE[domain](clauses_sous_traitance(**overrides), config)


def st_config(**changes_by_domain) -> DecisionConfig:
    """Configuration du projet dont certains réglages du profil sont changés, par section :
    `st_config(juridique={"subcontractor_cap_min_pct": 120})`."""
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    for section, changes in changes_by_domain.items():
        data["sous_traitance"]["rules"][section] |= changes
    return DecisionConfig.model_validate(data)


def category_of(kind, category) -> dict:
    return {"categories": {kind: category}}


# un constat par ligne du tableau de la fiche : (domaine, clauses changées, constat attendu)
ST_ROWS = [
    pytest.param(
        "juridique",
        {"responsabilite_donneur_ordre": None},
        finding(
            "responsabilite_donneur_ordre",
            "blocage",
            "blocage : responsabilité du donneur d'ordre illimitée",
        ),
        id="donneur_d_ordre_illimitee",
    ),
    pytest.param(
        "juridique",
        {"responsabilite_sous_traitant": 50},
        finding(
            "responsabilite_sous_traitant",
            "penalite",
            "plafond de responsabilité du sous-traitant à 50 % du montant du contrat, "
            "sous le minimum de 100 %",
            penalty=0.5,
        ),
        id="plafond_du_sous_traitant",
    ),
    pytest.param(
        "juridique",
        category_of("protection_design", "confidentialite_seule"),
        finding(
            "protection_design",
            "penalite",
            "dessins et documents remis protégés par la seule confidentialité, sans "
            "limite d'usage",
            penalty=0.3,
        ),
        id="design_confidentialite_seule",
    ),
    pytest.param(
        "juridique",
        category_of("protection_design", "reutilisation_autorisee"),
        finding(
            "protection_design",
            "blocage",
            "blocage : réutilisation des dessins et documents remis autorisée",
        ),
        id="design_reutilisation_autorisee",
    ),
    pytest.param(
        "juridique",
        {"protection_design": ABSENT},
        finding(
            "protection_design",
            "penalite",
            "protection des dessins et documents remis absente",
            penalty=0.3,
        ),
        id="design_absent",
    ),
    pytest.param(
        "financier",
        {"revision_prix": None},
        finding("revision_prix", "blocage", "blocage : révision de prix non plafonnée"),
        id="revision_non_plafonnee",
    ),
    pytest.param(
        "financier",
        {"delai_paiement": ABSENT},
        finding("delai_paiement", "information", _common.DEFAULT_DELAY_NOTE),
        id="paiement_non_stipule",
    ),
    pytest.param(
        "financier",
        {"delai_paiement": None, **category_of("delai_paiement", None)},
        finding(
            "delai_paiement",
            "penalite",
            "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
            "à renégocier",
            penalty=0.2,
        ),
        id="paiement_non_chiffre",
    ),
    pytest.param(
        "financier",
        {"delai_paiement": 90},
        finding(
            "delai_paiement",
            "penalite",
            "délai non conforme, à renégocier : 90 jours date de facture, au-delà de "
            "60 jours",
            penalty=0.2,
        ),
        id="paiement_date_de_facture",
    ),
    pytest.param(
        "financier",
        {"delai_paiement": 46, **category_of("delai_paiement", "fin_de_mois")},
        finding(
            "delai_paiement",
            "penalite",
            "délai non conforme, à renégocier : 46 jours fin de mois, au-delà de 45 jours",
            penalty=0.2,
        ),
        id="paiement_fin_de_mois",
    ),
    pytest.param(
        "financier",
        {"delai_paiement": 46, **category_of("delai_paiement", "facture_periodique")},
        finding(
            "delai_paiement",
            "penalite",
            "délai non conforme, à renégocier : 46 jours après une facture périodique, "
            "au-delà de 45 jours",
            penalty=0.2,
        ),
        id="paiement_facture_periodique",
    ),
    pytest.param(
        "financier",
        {"penalites_retard_execution": ABSENT},
        finding(
            "penalites_retard_execution",
            "penalite",
            "pénalités de retard d'exécution absentes",
            penalty=0.4,
        ),
        id="penalites_absentes",
    ),
    pytest.param(
        "financier",
        {"penalites_retard_execution": 2},
        finding(
            "penalites_retard_execution",
            "penalite",
            "pénalités de retard d'exécution plafonnées à 2 % du montant du contrat, "
            "sous le minimum de 5 %",
            penalty=0.4,
        ),
        id="penalites_sous_le_minimum",
    ),
    pytest.param(
        "conformite",
        category_of("obligation_certification", "norme_sans_preuves"),
        finding(
            "obligation_certification",
            "penalite",
            "norme nommée sans preuves de conformité à remettre",
            penalty=0.3,
        ),
        id="certification_norme_sans_preuves",
    ),
    pytest.param(
        "conformite",
        category_of("obligation_certification", "conformite_generale"),
        finding(
            "obligation_certification",
            "penalite",
            "conformité générale aux normes, sans norme nommée",
            penalty=0.3,
        ),
        id="certification_conformite_generale",
    ),
    pytest.param(
        "conformite",
        {"obligation_certification": ABSENT},
        finding(
            "obligation_certification",
            "blocage",
            "blocage : obligation de certification absente",
        ),
        id="certification_absente",
    ),
    pytest.param(
        "conformite",
        {"assurance_sous_traitant": 1_000_000},
        finding(
            "assurance_sous_traitant",
            "penalite",
            "assurance du sous-traitant de 1 000 000 € par sinistre, sous le minimum de "
            "2 000 000 €",
            penalty=0.3,
        ),
        id="assurance_sous_le_minimum",
    ),
    pytest.param(
        "conformite",
        {"assurance_sous_traitant": None},
        finding(
            "assurance_sous_traitant",
            "penalite",
            "assurance du sous-traitant non chiffrée",
            penalty=0.3,
        ),
        id="assurance_non_chiffree",
    ),
    pytest.param(
        "conformite",
        {"assurance_sous_traitant": ABSENT},
        finding(
            "assurance_sous_traitant",
            "penalite",
            "assurance du sous-traitant absente",
            penalty=0.3,
        ),
        id="assurance_absente",
    ),
    pytest.param(
        "operationnel",
        category_of("delai_execution", "delai_indicatif"),
        finding(
            "delai_execution",
            "penalite",
            "délai d'exécution indicatif, sans date qui engage le sous-traitant",
            penalty=0.4,
        ),
        id="delai_indicatif",
    ),
    pytest.param(
        "operationnel",
        {"delai_execution": ABSENT},
        finding("delai_execution", "penalite", "délai d'exécution absent", penalty=0.4),
        id="delai_absent",
    ),
]


@pytest.mark.parametrize(("domain", "overrides", "expected"), ST_ROWS)
def test_sous_traitance_constat_du_tableau(domain, overrides, expected):
    a = run_st(domain, **overrides)
    assert a.findings == [expected]
    assert a.hard_block == (expected.effect == "blocage")
    assert a.score == rounded(1.0 - (expected.penalty or 0.0))
    assert a.kinds_to_justify() == [expected.kind]


@pytest.mark.parametrize("domain", DOMAINS)
def test_contrat_sous_traitance_favorable_sans_constat(domain):
    a = run_st(domain)
    assert (a.domain, a.score, a.hard_block, a.findings) == (domain, 1.0, False, [])
    assert a.kinds_to_justify() == []


@pytest.mark.parametrize(
    ("domain", "overrides"),
    [
        ("juridique", {"responsabilite_donneur_ordre": 100}),  # plafonnée
        ("juridique", {"responsabilite_donneur_ordre": ABSENT}),
        ("juridique", {"responsabilite_sous_traitant": 100}),  # au minimum
        ("juridique", {"responsabilite_sous_traitant": 99.9999999}),  # arrondi
        ("juridique", {"responsabilite_sous_traitant": None}),  # illimitée : favorable
        ("juridique", {"responsabilite_sous_traitant": ABSENT}),
        ("juridique", category_of("protection_design", "usage_limite_au_projet")),
        ("financier", {"revision_prix": 3}),
        ("financier", {"revision_prix": ABSENT}),
        ("financier", {"delai_paiement": 60}),
        (
            "financier",
            {"delai_paiement": 45, **category_of("delai_paiement", "fin_de_mois")},
        ),
        (
            "financier",
            {
                "delai_paiement": 45,
                **category_of("delai_paiement", "facture_periodique"),
            },
        ),
        ("financier", {"penalites_retard_execution": 5}),  # au minimum
        ("financier", {"penalites_retard_execution": None}),  # non plafonnées
        ("conformite", category_of("obligation_certification", "norme_et_preuves")),
        ("conformite", {"assurance_sous_traitant": 2_000_000}),  # au minimum
        ("conformite", {"assurance_sous_traitant": 1_999_999.9999999}),  # arrondi
        ("conformite", {"assurance_sous_traitant": 5_000_000}),
        ("operationnel", category_of("delai_execution", "date_contractuelle")),
    ],
)
def test_sous_traitance_cas_sans_constat(domain, overrides):
    assert run_st(domain, **overrides).findings == []


def test_sous_traitance_plusieurs_constats_dans_l_ordre_du_profil():
    a = run_st(
        "financier",
        revision_prix=None,
        delai_paiement=90,
        penalites_retard_execution=ABSENT,
    )
    assert [(f.kind, f.effect) for f in a.findings] == [
        ("revision_prix", "blocage"),
        ("penalites_retard_execution", "penalite"),
        ("delai_paiement", "penalite"),
    ]
    assert a.hard_block and a.score == 0.4  # 1 - 0,4 - 0,2


# chaque réglage reçoit une valeur distincte de toutes les autres et de la démonstration
WIRED = st_config(
    juridique={
        "subcontractor_cap_min_pct": 120,
        "subcontractor_cap_score_penalty": 0.11,
        "design_score_penalty": 0.12,
    },
    financier={
        "execution_delay_penalties_min_cap_pct": 7,
        "execution_delay_penalties_score_penalty": 0.13,
        "payment_delay_max_days_invoice": 75,
        "payment_delay_max_days_end_of_month": 55,
        "payment_delay_max_days_periodic_invoice": 35,
        "payment_delay_score_penalty": 0.14,
    },
    conformite={
        "certification_score_penalty": 0.15,
        "insurance_min_eur": 2_500_000,
        "insurance_score_penalty": 0.16,
    },
    operationnel={"schedule_score_penalty": 0.17},
)


@pytest.mark.parametrize(
    ("domain", "overrides", "text", "penalty"),
    [
        (
            "juridique",
            {"responsabilite_sous_traitant": 110},
            (
                "plafond de responsabilité du sous-traitant à 110 % du montant du contrat, "
                "sous le minimum de 120 %"
            ),
            0.11,
        ),
        (
            "juridique",
            category_of("protection_design", "confidentialite_seule"),
            (
                "dessins et documents remis protégés par la seule confidentialité, sans "
                "limite d'usage"
            ),
            0.12,
        ),
        (
            "juridique",
            {"protection_design": ABSENT},
            "protection des dessins et documents remis absente",
            0.12,
        ),
        (
            "financier",
            {"penalites_retard_execution": ABSENT},
            "pénalités de retard d'exécution absentes",
            0.13,
        ),
        (
            "financier",
            {"penalites_retard_execution": 6},
            (
                "pénalités de retard d'exécution plafonnées à 6 % du montant du contrat, "
                "sous le minimum de 7 %"
            ),
            0.13,
        ),
        (
            "financier",
            {"delai_paiement": 76},
            (
                "délai non conforme, à renégocier : 76 jours date de facture, au-delà de "
                "75 jours"
            ),
            0.14,
        ),
        (
            "financier",
            {"delai_paiement": 56, **category_of("delai_paiement", "fin_de_mois")},
            "délai non conforme, à renégocier : 56 jours fin de mois, au-delà de 55 jours",
            0.14,
        ),
        (
            "financier",
            {
                "delai_paiement": 36,
                **category_of("delai_paiement", "facture_periodique"),
            },
            (
                "délai non conforme, à renégocier : 36 jours après une facture périodique, "
                "au-delà de 35 jours"
            ),
            0.14,
        ),
        (
            "financier",
            {"delai_paiement": None, **category_of("delai_paiement", None)},
            (
                "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
                "à renégocier"
            ),
            0.14,
        ),
        (
            "conformite",
            category_of("obligation_certification", "norme_sans_preuves"),
            "norme nommée sans preuves de conformité à remettre",
            0.15,
        ),
        (
            "conformite",
            {"assurance_sous_traitant": 2_400_000},
            (
                "assurance du sous-traitant de 2 400 000 € par sinistre, sous le minimum de "
                "2 500 000 €"
            ),
            0.16,
        ),
        (
            "conformite",
            {"assurance_sous_traitant": None},
            "assurance du sous-traitant non chiffrée",
            0.16,
        ),
        (
            "conformite",
            {"assurance_sous_traitant": ABSENT},
            "assurance du sous-traitant absente",
            0.16,
        ),
        (
            "operationnel",
            category_of("delai_execution", "delai_indicatif"),
            "délai d'exécution indicatif, sans date qui engage le sous-traitant",
            0.17,
        ),
        ("operationnel", {"delai_execution": ABSENT}, "délai d'exécution absent", 0.17),
    ],
)
def test_sous_traitance_seuils_et_penalites_lus_dans_la_configuration(
    domain, overrides, text, penalty
):
    [found] = run_st(domain, config=WIRED, **overrides).findings
    assert (found.text, found.effect, found.penalty) == (text, "penalite", penalty)


def test_sous_traitance_seuils_de_la_demonstration_ne_declenchent_plus_sous_wired():
    # sous WIRED, 61 jours date de facture et 2 000 000 € sont conformes : seuils relus
    assert run_st("financier", config=WIRED, delai_paiement=61).findings == []
    assert (
        run_st("conformite", config=WIRED, assurance_sous_traitant=2_500_000).findings
        == []
    )


def test_sous_traitance_le_montant_de_l_assurance_est_groupe_par_milliers():
    cfg = st_config(conformite={"insurance_min_eur": 5000})
    [found] = run_st("conformite", config=cfg, assurance_sous_traitant=4000).findings
    assert found.text.endswith("de 4000 € par sinistre, sous le minimum de 5000 €")
    cfg = st_config(conformite={"insurance_min_eur": 12_500_000})
    [found] = run_st("conformite", config=cfg, assurance_sous_traitant=999_999).findings
    assert found.text.endswith("sous le minimum de 12 500 000 €")


# Le texte d'un constat dit la catégorie, l'effet vient des listes de la configuration : une
# catégorie déplacée d'une liste à l'autre change l'effet, jamais la vérité du texte.
DESIGN_LISTS = ("design_accepted", "design_penalized", "design_blocking")
CERTIFICATION_LISTS = ("certification_accepted", "certification_penalized")
SCHEDULE_LISTS = ("schedule_accepted", "schedule_penalized")


def placed(section, names, categories, category, where) -> DecisionConfig:
    """Configuration où `category` est seule dans la liste `where`, les autres catégories
    étant acceptées (première liste de `names`)."""
    lists = {name: [] for name in names}
    lists[names[0]] = [c for c in categories if c != category]
    lists[where] = [*lists[where], category]
    return st_config(**{section: lists})


DESIGN_LABELS = {
    "usage_limite_au_projet": "dessins et documents remis limités au seul projet",
    "confidentialite_seule": "dessins et documents remis protégés par la seule "
    "confidentialité, sans limite d'usage",
    "reutilisation_autorisee": "réutilisation des dessins et documents remis autorisée",
}
CERTIFICATION_LABELS = {
    "norme_et_preuves": "norme nommée avec preuves de conformité à remettre",
    "norme_sans_preuves": "norme nommée sans preuves de conformité à remettre",
    "conformite_generale": "conformité générale aux normes, sans norme nommée",
}
SCHEDULE_LABELS = {
    "date_contractuelle": "délai d'exécution fixé par une date qui engage le "
    "sous-traitant",
    "delai_indicatif": "délai d'exécution indicatif, sans date qui engage le "
    "sous-traitant",
}


@pytest.mark.parametrize("category", DESIGN_PROTECTIONS)
@pytest.mark.parametrize("where", DESIGN_LISTS)
def test_sous_traitance_protection_design_selon_la_liste_reglee(category, where):
    cfg = placed("juridique", DESIGN_LISTS, DESIGN_PROTECTIONS, category, where)
    found = run_st(
        "juridique", config=cfg, **category_of("protection_design", category)
    ).findings
    label = DESIGN_LABELS[category]
    assert (
        found
        == {
            "design_accepted": [],
            "design_penalized": [finding("protection_design", "penalite", label, 0.3)],
            "design_blocking": [
                finding("protection_design", "blocage", f"blocage : {label}")
            ],
        }[where]
    )


@pytest.mark.parametrize("category", CERTIFICATION_REQUIREMENTS)
@pytest.mark.parametrize("where", CERTIFICATION_LISTS)
def test_sous_traitance_certification_selon_la_liste_reglee(category, where):
    cfg = placed(
        "conformite", CERTIFICATION_LISTS, CERTIFICATION_REQUIREMENTS, category, where
    )
    found = run_st(
        "conformite", config=cfg, **category_of("obligation_certification", category)
    ).findings
    expected = {
        "certification_accepted": [],
        "certification_penalized": [
            finding(
                "obligation_certification",
                "penalite",
                CERTIFICATION_LABELS[category],
                0.3,
            )
        ],
    }[where]
    assert found == expected


@pytest.mark.parametrize("category", EXECUTION_SCHEDULES)
@pytest.mark.parametrize("where", SCHEDULE_LISTS)
def test_sous_traitance_delai_d_execution_selon_la_liste_reglee(category, where):
    cfg = placed("operationnel", SCHEDULE_LISTS, EXECUTION_SCHEDULES, category, where)
    found = run_st(
        "operationnel", config=cfg, **category_of("delai_execution", category)
    ).findings
    expected = {
        "schedule_accepted": [],
        "schedule_penalized": [
            finding("delai_execution", "penalite", SCHEDULE_LABELS[category], 0.4)
        ],
    }[where]
    assert found == expected


@pytest.mark.parametrize(
    ("domain", "kind"),
    [
        ("juridique", "protection_design"),
        ("conformite", "obligation_certification"),
        ("operationnel", "delai_execution"),
    ],
)
@pytest.mark.parametrize("category", [None, "categorie_inconnue", "date_facture"])
def test_sous_traitance_categorie_hors_des_listes_refusee(domain, kind, category):
    # impossible après la vérification de l'extraction : erreur explicite, ni repli ni
    # acceptation en silence
    with pytest.raises(ValueError, match=f"clause {kind} : catégorie"):
        run_st(domain, **category_of(kind, category))


@pytest.mark.parametrize("domain", DOMAINS)
def test_regles_sans_section_sous_traitance_refusees(domain):
    sans_section = CONFIG.model_copy(update={"sous_traitance": None})
    with pytest.raises(
        ValueError,
        match="configuration sans section sous_traitance : profil sous_traitance non "
        "analysable",
    ):
        RULES_SOUS_TRAITANCE[domain](clauses_sous_traitance(), sans_section)


def test_regles_sous_traitance_un_jeu_complet_en_lecture_seule():
    assert set(RULES_SOUS_TRAITANCE) == set(DOMAINS)
    assert all(run_st(domain).domain == domain for domain in DOMAINS)
    with pytest.raises(TypeError):
        RULES_SOUS_TRAITANCE["juridique"] = RULES_SOUS_TRAITANCE["financier"]


@pytest.mark.parametrize(
    ("domain", "kind"),
    [(d, k) for d in DOMAINS for k in SOUS_TRAITANCE.domain_kinds[d]],
)
def test_sous_traitance_clause_attendue_manquante_leve_une_erreur(domain, kind):
    incomplete = [c for c in clauses_sous_traitance() if c.kind != kind]
    with pytest.raises(ValueError, match=f"clause {kind}"):
        RULES_SOUS_TRAITANCE[domain](incomplete, CONFIG)


@pytest.mark.parametrize("domain", DOMAINS)
def test_sous_traitance_une_regle_ne_lit_que_les_clauses_de_son_domaine(domain):
    own = [
        c
        for c in clauses_sous_traitance()
        if c.kind in SOUS_TRAITANCE.domain_kinds[domain]
    ]
    assert RULES_SOUS_TRAITANCE[domain](own, CONFIG).findings == []


def test_sous_traitance_clause_en_double_leve_une_erreur():
    doubled = clauses_sous_traitance() + [
        Clause(kind="revision_prix", present=True, quote="bis", value=1.0)
    ]
    with pytest.raises(ValueError, match="revision_prix"):
        RULES_SOUS_TRAITANCE["financier"](doubled, CONFIG)


def test_sous_traitance_clauses_du_fournisseur_refusees():
    with pytest.raises(ValueError, match="clause responsabilite_donneur_ordre"):
        RULES_SOUS_TRAITANCE["juridique"](clauses(), CONFIG)


# --- doublures du profil -------------------------------------------------------------------


def test_doublures_sous_traitance_clauses_favorables_chiffrees_avec_leur_unite():
    found = {c.kind: c for c in clauses_sous_traitance()}
    assert list(found) == list(SOUS_TRAITANCE.kinds)
    assert all(c.present for c in found.values())
    assert found["assurance_sous_traitant"].value == 3_000_000
    assert found["assurance_sous_traitant"].quote == (
        "Article synthétique n° 8 : la clause est fixée à 3 000 000 €."
    )
    assert found["responsabilite_sous_traitant"].quote.endswith("est fixée à 150 %.")
    assert found["delai_paiement"].quote.endswith("est fixée à 30 jours.")
    assert {k: c.category for k, c in found.items() if c.category} == {
        "protection_design": "usage_limite_au_projet",
        "delai_paiement": "date_facture",
        "obligation_certification": "norme_et_preuves",
        "delai_execution": "date_contractuelle",
    }
    assert found["protection_design"].quote == "Article synthétique n° 3."
    # une citation ne nomme pas son type : « obligation_certification » évoquerait
    # « certification » et interdirait de déclarer la clause absente
    assert not any(kind in c.quote for kind, c in found.items())


def test_doublures_sous_traitance_remplacement_absence_et_types_inconnus():
    found = {
        c.kind: c
        for c in clauses_sous_traitance(
            categories={"delai_execution": "delai_indicatif"},
            assurance_sous_traitant=ABSENT,
            revision_prix=None,
        )
    }
    assert not found["assurance_sous_traitant"].present
    assert found["assurance_sous_traitant"].quote == ""
    assert found["revision_prix"].present and found["revision_prix"].value is None
    assert found["delai_execution"].category == "delai_indicatif"
    with pytest.raises(TypeError, match="penalites_execution"):
        clauses_sous_traitance(penalites_execution=3)  # un type du fournisseur


def test_doublures_sous_traitance_le_texte_du_contrat_contient_chaque_citation():
    text = normalize(CONTRACT_TEXT_SOUS_TRAITANCE)
    quotes = [c.quote for c in clauses_sous_traitance() if c.present]
    for kind, values in TEST_VALUES_SOUS_TRAITANCE.items():
        for value in values:
            [c] = [c for c in clauses_sous_traitance(**{kind: value}) if c.kind == kind]
            quotes.append(c.quote)
    assert all(normalize(quote) in text for quote in quotes)


def test_doublures_sous_traitance_passent_les_controles_de_l_extraction():
    found = clauses_sous_traitance()
    units = SOUS_TRAITANCE.value_units
    st = CONFIG.sous_traitance
    assert verification.value_mismatches(found, units) == []
    assert verification.category_mismatches(found, st.category_terms) == []
    # le texte n'évoque aucune clause : chacune peut être déclarée absente
    absent = [
        Clause(kind=k, present=False, quote="", value=None)
        for k in SOUS_TRAITANCE.kinds
    ]
    assert (
        verification.mentioned_absences(
            CONTRACT_TEXT_SOUS_TRAITANCE, absent, st.absence_terms
        )
        == []
    )

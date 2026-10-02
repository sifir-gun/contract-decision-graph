"""Saisie d'un contrat à analyser, commune à l'interface web et au serveur MCP : contrat du
jeu ou texte fourni, parties à masquer, identifiant et date d'analyse. Une règle, deux
portes ; les messages sont ceux que l'interface montrait déjà."""

import re
from datetime import UTC, date, datetime

import pytest

from cdg.application import demo_set, saisie
from cdg.application.saisie import InputError

PIEGE = "demo-11-piege-injection"
DECLAREES = ["Phi Restauration Synthétique", "Chi Collectivités Synthétiques"]
DEMO_REFUSE = (
    "démonstration : l'extraction est simulée pour les contrats du jeu seulement ; "
    "choisissez-en un"
)


def never() -> str:
    raise AssertionError("texte lu alors que la saisie devait être refusée avant")


# --- contrat du jeu ----------------------------------------------------------------------


def test_contrat_du_jeu_texte_et_identifiant():
    entry = saisie.from_demo_set(PIEGE, [], demo=False)
    _, contracts = demo_set.load()
    assert entry.text == contracts[PIEGE].text()
    assert entry.base == PIEGE


def test_contrat_du_jeu_parties_declarees_en_demonstration():
    """L'extraction simulée dépend des parties déclarées : en démonstration, elles
    l'emportent sur celles saisies."""
    assert saisie.from_demo_set(PIEGE, ["X"], demo=True).parties == DECLAREES


def test_contrat_du_jeu_parties_saisies_sinon_declarees_en_mode_reel():
    assert saisie.from_demo_set(PIEGE, ["X"], demo=False).parties == ["X"]
    assert saisie.from_demo_set(PIEGE, [], demo=False).parties == DECLAREES
    assert saisie.from_demo_set(PIEGE, ["  ", ""], demo=False).parties == DECLAREES


@pytest.mark.parametrize("demo", [True, False])
def test_contrat_du_jeu_inconnu_refuse(demo):
    with pytest.raises(InputError, match="^contrat inconnu du jeu de démonstration$"):
        saisie.from_demo_set("demo-99-inconnu", [], demo=demo)


# --- texte fourni ------------------------------------------------------------------------


def test_texte_fourni_tel_quel_parties_nettoyees():
    entry = saisie.from_text(
        lambda: "Contrat\nArticle 1", [" Alpha ", "", "Beta"], demo=False
    )
    assert entry == saisie.ContractInput(
        text="Contrat\nArticle 1", parties=["Alpha", "Beta"], base="contrat"
    )


def test_texte_fourni_refuse_en_demonstration_avant_d_etre_lu():
    with pytest.raises(InputError, match=f"^{re.escape(DEMO_REFUSE)}$"):
        saisie.from_text(never, [], demo=True)


@pytest.mark.parametrize("text", ["", "  \n\t "])
def test_texte_vide_refuse(text):
    with pytest.raises(InputError, match="^le texte du contrat est vide$"):
        saisie.from_text(lambda: text, [], demo=False)


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x1b", "\x7f", "\x0b"])
def test_caractere_de_controle_refuse(char):
    with pytest.raises(
        InputError,
        match="^le texte contient un caractère de contrôle : ce n'est pas du texte brut$",
    ):
        saisie.from_text(lambda: f"Contrat{char}suite", [], demo=False)


def test_tabulation_et_fins_de_ligne_admises():
    text = "Contrat\tA\r\nArticle 1\n"
    assert saisie.from_text(lambda: text, [], demo=False).text == text


def test_erreur_de_saisie_est_une_valueerror():
    assert issubclass(InputError, ValueError)


# --- identifiant et date -----------------------------------------------------------------

AT = datetime(2026, 10, 2, 9, 5, 7, tzinfo=UTC)


@pytest.mark.parametrize("wanted", ["", "   "])
def test_identifiant_par_defaut_base_horodatee_et_suffixe(wanted):
    generated = saisie.contract_id(wanted, "contrat", AT)
    assert re.fullmatch(r"contrat-20261002-090507-[0-9a-f]{4}", generated)


def test_identifiant_saisi_verifie_par_la_regle_du_domaine():
    assert saisie.contract_id(" c-1 ", "contrat", AT) == "c-1"
    with pytest.raises(InputError, match="identifiant de contrat invalide"):
        saisie.contract_id("../c", "contrat", AT)


def test_date_d_analyse_facultative_et_verifiee():
    assert saisie.analysis_date("") is None
    assert saisie.analysis_date(" 2026-09-25 ") == date(2026, 9, 25)
    with pytest.raises(InputError, match="^date invalide : AAAA-MM-JJ attendu$"):
        saisie.analysis_date("25/09/2026")

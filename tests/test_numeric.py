"""Arrondi unique : tout flottant comparé à un seuil ou sérialisé passe par là."""

import math

import pytest

from cdg.domain.numeric import DECIMALS, rounded, shown


def test_six_decimales():
    assert DECIMALS == 6
    assert rounded(0.1234564) == 0.123456


def test_absorbe_le_bruit_flottant_avant_comparaison():
    assert 0.1 + 0.2 != 0.3
    assert rounded(0.1 + 0.2) == 0.3
    assert rounded(0.7499999999) >= 0.75


def test_zero_negatif_normalise():
    r = rounded(-0.0000001)
    assert r == 0.0 and math.copysign(1.0, r) == 1.0


def test_entier_rendu_en_flottant():
    assert rounded(60000) == 60000.0 and isinstance(rounded(1), float)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_refuse_les_valeurs_non_finies(value):
    with pytest.raises(ValueError, match="non fini"):
        rounded(value)


def test_grand_nombre_affiche_groupe():
    assert shown(2000000.0) == "2 000 000"
    assert shown(150.0) == "150"
    assert shown(2.5) == "2.5"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (9999.0, "9999"),  # sous 10 000 : identique à `:g`
        (10000.0, "10 000"),
        (123456.0, "123 456"),
        (1234567.0, "1 234 567"),
        (0.0, "0"),
        (45.0, "45"),
        (1.5, "1.5"),
        # non entier de 10 000 ou plus : groupé aussi, sans la forme `:g`
        (10000.5, "10 000.5"),
        (1234567.25, "1 234 567.25"),
    ],
)
def test_affichage_groupe_a_partir_de_dix_mille(value, expected):
    assert shown(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # arrondi d'abord (`rounded`), puis écrit : jamais « 2e+06 »
        (2000000.0000001, "2 000 000"),
        (2000000.0, "2 000 000"),  # un entier
        (2.0000001, "2"),  # un décimal sous 10 000
        (9999.9999999, "10 000"),  # arrondi à 10 000 : groupé
        (12345.6789012, "12 345.678901"),  # un décimal au-dessus, six décimales
    ],
)
def test_affichage_arrondi_avant_d_etre_ecrit(value, expected):
    assert shown(value) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_affichage_refuse_les_valeurs_non_finies(value):
    with pytest.raises(ValueError, match="non fini"):
        shown(value)


@pytest.mark.parametrize("value", [0.0, 1.0, 0.5, 45.0, 60.0, 100.0, 9999.0, 2.5, 1e-5])
def test_affichage_identique_a_g_sous_dix_mille(value):
    assert shown(value) == f"{value:g}"

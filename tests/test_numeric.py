"""Arrondi unique : tout flottant comparé à un seuil ou sérialisé passe par là."""

import math

import pytest

from cdg.numeric import DECIMALS, rounded


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

"""Fusion par rangs réciproques (Reciprocal Rank Fusion, Cormack, Clarke et Büttcher,
2009) : recherche hybride, plein texte et vecteurs (ADR 006, PR 2, technique 3).

Score d'un élément : somme, sur les listes où il figure, de 1 / (k + rang), le rang
commençant à 1. Calcul exact (fractions) : aucun flottant comparé, ordre reproductible.
"""

from fractions import Fraction

import pytest

from cdg.domain.fusion import reciprocal_rank_fusion, rrf_scores


def test_score_somme_des_rangs_reciproques():
    scores = rrf_scores([["a", "b", "c"], ["c", "a"]], k=60)
    assert scores == {
        "a": Fraction(1, 61) + Fraction(1, 62),
        "b": Fraction(1, 62),
        "c": Fraction(1, 63) + Fraction(1, 61),
    }


def test_ordre_par_score_decroissant():
    assert reciprocal_rank_fusion([["a", "b", "c"], ["c", "a"]], k=60) == [
        "a",
        "c",
        "b",
    ]


def test_un_element_present_dans_les_deux_listes_passe_devant():
    # second partout bat premier d'une seule liste
    assert reciprocal_rank_fusion([["x", "a"], ["y", "a"]], k=60)[0] == "a"


def test_egalite_departagee_par_l_ordre_d_apparition():
    """À score égal, l'ordre d'apparition, la première liste (vecteurs) d'abord."""
    assert reciprocal_rank_fusion([["a"], ["b"]], k=60) == ["a", "b"]
    assert reciprocal_rank_fusion([["b"], ["a"]], k=60) == ["b", "a"]


def test_listes_vides():
    assert reciprocal_rank_fusion([[], []], k=60) == []
    assert reciprocal_rank_fusion([["a"], []], k=60) == ["a"]


def test_doublon_dans_une_liste_refuse():
    with pytest.raises(ValueError, match="doublon"):
        reciprocal_rank_fusion([["a", "a"]], k=60)


def test_constante_invalide_refusee():
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion([["a"]], k=0)

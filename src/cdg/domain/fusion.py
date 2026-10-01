"""Fusion par rangs réciproques (Reciprocal Rank Fusion, Cormack, Clarke et Büttcher,
2009), pour la recherche hybride : plein texte et vecteurs (ADR 006).

Score d'un élément : somme, sur les listes où il figure, de 1 / (k + rang), le rang
commençant à 1. Seuls les rangs comptent, jamais les scores propres à chaque liste
(distance cosinus, rang plein texte), qui ne se comparent pas. Calcul exact (fractions) :
aucun flottant comparé, ordre reproductible ; à score égal, l'ordre d'apparition, la
première liste d'abord.
"""

from collections.abc import Sequence
from fractions import Fraction


def rrf_scores(rankings: Sequence[Sequence[str]], k: int) -> dict[str, Fraction]:
    """Score de chaque élément, dans l'ordre de sa première apparition."""
    if k < 1:
        raise ValueError(f"constante k de la fusion invalide : {k}, au moins 1")
    scores: dict[str, Fraction] = {}
    for ranking in rankings:
        if len(set(ranking)) != len(ranking):
            raise ValueError(f"doublon dans une liste à fusionner : {list(ranking)}")
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, Fraction(0)) + Fraction(1, k + rank)
    return scores


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int) -> list[str]:
    """Éléments des listes, par score décroissant ; tri stable : à score égal, l'ordre
    de première apparition."""
    scores = rrf_scores(rankings, k)
    return sorted(scores, key=lambda key: -scores[key])

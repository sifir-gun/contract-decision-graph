"""Mesure de la recherche seule, en fonctions pures : rappel et précision au rang k, au
niveau de la référence (article ou fiche), puisque le CRAG retient des références.

- R_k : références distinctes des k premiers extraits rendus, dans l'ordre de rang ;
- rappel@k = |attendues ∩ R_k| / |attendues| ;
- précision@k = |attendues ∩ R_k| / |R_k|, nulle si aucun extrait n'est rendu ;
- taux d'échec@k = 1 − rappel@k : la mesure de l'article d'Anthropic « Contextual
  Retrieval » (1 − recall@20).

Le jeu d'évaluation et la recherche sont dans `application/evaluation.py` (ADR 006).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from cdg.domain.numeric import rounded


def top_references(references: Sequence[str], k: int) -> list[str]:
    """Références distinctes des k premiers extraits, dans l'ordre de rang."""
    if k < 1:
        raise ValueError(f"rang k invalide : {k}, au moins 1")
    return list(dict.fromkeys(references[:k]))


@dataclass(frozen=True)
class Scores:
    recall: float
    precision: float


def scores(expected: Collection[str], found: Collection[str]) -> Scores:
    """Rappel et précision des références trouvées (R_k) au regard des attendues."""
    wanted, got = set(expected), set(found)
    if not wanted:
        raise ValueError("aucune référence attendue : rappel indéfini")
    hits = len(wanted & got)
    return Scores(
        recall=rounded(hits / len(wanted)),
        precision=rounded(hits / len(got)) if got else 0.0,
    )


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("moyenne d'aucune valeur")
    return rounded(sum(values) / len(values))


def first_ranks(
    expected: Sequence[str], references: Sequence[str]
) -> dict[str, int | None]:
    """Rang, à partir de 1, du premier extrait de chaque référence attendue ; None si
    aucun extrait rendu n'en vient."""
    ranks: dict[str, int] = {}
    for rank, reference in enumerate(references, start=1):
        ranks.setdefault(reference, rank)
    return {reference: ranks.get(reference) for reference in expected}

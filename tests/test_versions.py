"""Versions successives d'un texte du corpus (docs/spec-versions-corpus.md) : la date
d'analyse choisit la version en vigueur ; une version supplantée est écartée."""

from dataclasses import dataclass
from datetime import date

from cdg.domain import corpus

D19, D27 = date(2019, 4, 26), date(2027, 1, 1)


@dataclass(frozen=True)
class V:
    reference: str
    valid_from: date | None
    valid_until: date | None


OLD, NEW = V("A", D19, D27), V("A", D27, None)


# --- règle pure ---------------------------------------------------------------------------


def test_in_force_bornes():
    assert corpus.in_force(D19, D27, D19) and not corpus.in_force(D19, D27, D27)
    assert corpus.in_force(D27, None, D27) and not corpus.in_force(D27, None, D19)
    assert corpus.in_force(None, None, date(1900, 1, 1))


def test_drop_superseded_garde_la_version_en_vigueur():
    other = V("B", None, None)
    items = [NEW, other, OLD]
    assert corpus.drop_superseded(items, date(2026, 12, 31)) == [other, OLD]
    assert corpus.drop_superseded(items, D27) == [NEW, other]


def test_drop_superseded_sans_version_en_vigueur_garde_tout():
    assert corpus.drop_superseded([NEW, OLD], date(2019, 1, 1)) == [NEW, OLD]
    expired = V("C", D19, D27)
    assert corpus.drop_superseded([expired], date(2027, 3, 1)) == [expired]

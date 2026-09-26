"""Outils communs aux règles : lecture d'une clause, constats rattachés à leur clause.

Les règles passent avant le CRAG et ne lisent pas le statut de récupération. Chaque constat
porte la clause qui le déclenche et son effet ; la justification par le corpus vient ensuite
(`domain/justification.py`).
"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, model_validator

from cdg.domain.config import DecisionConfig, Penalty
from cdg.domain.models import Clause, Domain
from cdg.domain.numeric import rounded

# blocage ou pénalité : le constat exige une référence en vigueur ; information : il est
# justifié si le corpus le permet, sans effet sur le statut
Effect = Literal["blocage", "penalite", "information"]


class RuleFinding(BaseModel):
    """Constat d'une règle, rattaché à la clause qui le déclenche."""

    kind: str  # clause qui déclenche le constat
    text: str
    effect: Effect
    penalty: Penalty | None = None  # pénalité de score, pour l'effet « penalite » seulement

    @model_validator(mode="after")
    def _penalite_selon_l_effet(self) -> "RuleFinding":
        if (self.effect == "penalite") != (self.penalty is not None):
            raise ValueError(
                f"constat {self.kind} : une pénalité de score va avec l'effet « penalite », "
                f"et seulement avec lui (effet {self.effect}, pénalité {self.penalty})"
            )
        return self

    @property
    def requires_reference(self) -> bool:
        return self.effect != "information"


class Assessment(BaseModel):
    """Issue des règles d'un domaine, avant le CRAG : les constats, d'où le score et le
    blocage. Un blocage ne touche pas le score."""

    domain: Domain
    findings: list[RuleFinding]

    @property
    def hard_block(self) -> bool:
        return any(f.effect == "blocage" for f in self.findings)

    @property
    def score(self) -> float:
        """1,0 moins les pénalités, borné à [0, 1]."""
        penalties = sum(f.penalty for f in self.findings if f.penalty is not None)
        return rounded(min(1.0, max(0.0, 1.0 - penalties)))

    def kinds_to_justify(self) -> list[str]:
        """Clauses qui portent un constat, dans l'ordre des constats : le CRAG ne cherche
        que pour elles."""
        return list(dict.fromkeys(f.kind for f in self.findings))

    def kinds_requiring_reference(self) -> list[str]:
        return list(dict.fromkeys(f.kind for f in self.findings if f.requires_reference))


RuleFn = Callable[[list[Clause], DecisionConfig], Assessment]


def block(kind: str, text: str) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="blocage")


def penalize(kind: str, text: str, penalty: float) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="penalite", penalty=penalty)


def note(kind: str, text: str) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="information")


def clause(clauses: list[Clause], kind: str) -> Clause:
    matches = [c for c in clauses if c.kind == kind]
    if len(matches) != 1:
        raise ValueError(f"clause {kind} : {len(matches)} occurrence(s), une seule attendue")
    return matches[0]


def below(value: float, threshold: float) -> bool:
    return rounded(value) < rounded(threshold)


def above(value: float, threshold: float) -> bool:
    return rounded(value) > rounded(threshold)

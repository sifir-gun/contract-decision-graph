"""Outils communs aux règles : lecture d'une clause, calcul du score, verdict."""

from collections.abc import Callable

from cdg.config import DecisionConfig
from cdg.numeric import rounded
from cdg.state import AgentVerdict, Clause, Domain, RetrievalStatus

RuleFn = Callable[[list[Clause], RetrievalStatus, DecisionConfig], AgentVerdict]


def clause(clauses: list[Clause], kind: str) -> Clause:
    matches = [c for c in clauses if c.kind == kind]
    if len(matches) != 1:
        raise ValueError(f"clause {kind} : {len(matches)} occurrence(s), une seule attendue")
    return matches[0]


def below(value: float, threshold: float) -> bool:
    return rounded(value) < rounded(threshold)


def above(value: float, threshold: float) -> bool:
    return rounded(value) > rounded(threshold)


def verdict(
    domain: Domain,
    status: RetrievalStatus,
    *,
    hard_block: bool,
    penalties: list[float],
    findings: list[str],
) -> AgentVerdict:
    """Score : 1,0 moins les pénalités, borné à [0, 1]. Un blocage ne touche pas le score."""
    if status == "INSUFFISANT":
        findings = [
            *findings,
            f"référentiel insuffisant pour le domaine {domain} : constats non étayés par le corpus",
        ]
    score = rounded(min(1.0, max(0.0, 1.0 - sum(penalties))))
    return AgentVerdict(
        domain=domain,
        score=score,
        hard_block=hard_block,
        findings=findings,
        evidence_ids=[],
        retrieval_status=status,
    )

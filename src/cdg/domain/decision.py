"""Décision du gate, déterministe et sans LLM. L'issue la plus conservatrice l'emporte.

Ordre : 1. blocage dur, 2. analyste en échec, 3. budget, 4. INSUFFISANT, 5. conflit,
6. seuils puis marge. Le nœud `decision_gate` ne fait qu'adapter l'état à `decide`, puis
la proposition à l'état (route, clés écrites).
"""

from dataclasses import dataclass
from typing import Any

from cdg.domain.config import DecisionConfig
from cdg.domain.models import DOMAINS, AgentVerdict, Decision, NodeFailure, Usage
from cdg.domain.numeric import rounded


@dataclass(frozen=True)
class Aggregate:
    """Résultat de l'agrégation des 4 verdicts ; score et marge arrondis."""

    decision: Decision
    score: float
    margin: float
    hard_block: bool


def total_tokens(usage: list[Usage]) -> int:
    """Tokens consommés : somme de tokens_in et tokens_out."""
    return sum(u.tokens_in + u.tokens_out for u in usage)


def conflict(verdicts: list[AgentVerdict], gap: float) -> bool:
    """Écart de score entre domaines supérieur au seuil, sur domaines en OK."""
    scores = [v.score for v in verdicts if v.retrieval_status == "OK"]
    return len(scores) >= 2 and rounded(max(scores) - min(scores)) > rounded(gap)


def aggregate(verdicts: list[AgentVerdict], config: DecisionConfig) -> Aggregate:
    """Score pondéré, marge et décision ; un verdict par domaine exigé."""
    received = sorted(v.domain for v in verdicts)
    if received != sorted(DOMAINS):
        raise ValueError(f"un verdict par domaine attendu, reçu : {received}")
    by_domain = {v.domain: v for v in verdicts}

    # somme dans l'ordre fixe des domaines :
    # indépendante de l'ordre d'arrivée des branches
    score = rounded(sum(config.weight(d) * by_domain[d].score for d in DOMAINS))
    go = rounded(config.thresholds.go)
    go_reserves = rounded(config.thresholds.go_reserves)
    margin = rounded(min(abs(score - go), abs(score - go_reserves)))
    hard_block = any(v.hard_block for v in verdicts)

    if hard_block:
        decision: Decision = "NO_GO"
    # étapes 4 et 5 gardées distinctes pour suivre l'ordre de la spec (audit)
    elif any(v.retrieval_status == "INSUFFISANT" for v in verdicts):
        decision = "ESCALADE"  # 4. INSUFFISANT
    elif conflict(verdicts, config.conflict_gap):
        decision = "ESCALADE"  # 5. conflit
    elif score >= go:
        decision = "GO"
    elif score >= go_reserves:
        decision = "GO_RESERVES"
    else:
        decision = "NO_GO"
    return Aggregate(decision=decision, score=score, margin=margin, hard_block=hard_block)


def failure_report(failures: list[NodeFailure]) -> dict[str, Any]:
    """Rapport d'échec de nœud(s), captés par les gardes de l'orchestrateur."""
    return {"stage": "noeuds", "failures": [f.model_dump() for f in failures]}


@dataclass(frozen=True)
class GateOutcome:
    """Proposition du gate. Sans revue humaine, elle devient la décision finale."""

    proposed: Decision
    human_review: bool
    margin: float | None = None  # absente si l'agrégat est incalculable
    failure_report: dict[str, Any] | None = None

    @property
    def final(self) -> Decision | None:
        return None if self.human_review else self.proposed


def _hard_block(
    config: DecisionConfig, margin: float | None, report: dict[str, Any] | None
) -> GateOutcome:
    """1. NO_GO établi, marge ignorée ; un rapport d'échec (budget, nœuds) tracé quand même.
    Avec `hard_block_review`, NO_GO est seulement proposé : seul un humain peut le lever."""
    return GateOutcome(
        proposed="NO_GO",
        human_review=config.human_policy.hard_block_review,
        margin=margin,
        failure_report=report,
    )


def decide(
    verdicts: list[AgentVerdict],
    failures: list[NodeFailure],
    usage: list[Usage],
    config: DecisionConfig,
) -> GateOutcome:
    used = total_tokens(usage)
    limit = config.budget.max_tokens_per_contract
    over = used > limit
    budget_report = {"stage": "budget", "tokens": used, "limit": limit}

    if failures:  # analyste en échec : verdicts incomplets, agrégat incalculable
        report = failure_report(failures)
        if over:
            report["budget"] = {"tokens": used, "limit": limit}
        if any(v.hard_block for v in verdicts):  # 1. le blocage établi suffit
            return _hard_block(config, None, report)
        return GateOutcome(proposed="ESCALADE", human_review=True, failure_report=report)

    d = aggregate(verdicts, config)
    if d.hard_block:  # 1. NO_GO établi, marge ignorée
        return _hard_block(config, d.margin, budget_report if over else None)
    if over:  # 3. budget
        return GateOutcome(
            proposed="ESCALADE", human_review=True, margin=d.margin, failure_report=budget_report
        )
    # 4. INSUFFISANT, 5. conflit, 6. seuils : déjà ordonnés par aggregate
    low_margin = rounded(d.margin) < rounded(config.min_margin)
    return GateOutcome(
        proposed=d.decision,
        human_review=d.decision == "ESCALADE" or low_margin,
        margin=d.margin,
    )

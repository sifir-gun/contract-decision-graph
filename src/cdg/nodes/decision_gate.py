"""decision_gate : verdict déterministe, sans LLM. L'issue la plus conservatrice l'emporte.

Ordre : 1. blocage dur, 2. budget, 3. INSUFFISANT, 4. conflit, 5. seuils puis marge.
"""

from dataclasses import dataclass

from cdg.config import DecisionConfig
from cdg.numeric import rounded
from cdg.state import DOMAINS, AgentVerdict, ContractState, Decision, Usage


@dataclass(frozen=True)
class Aggregate:
    decision: Decision
    score: float
    margin: float
    hard_block: bool


def total_tokens(usage: list[Usage]) -> int:
    return sum(u.tokens_in + u.tokens_out for u in usage)


def conflict(verdicts: list[AgentVerdict], gap: float) -> bool:
    """Écart de score entre domaines supérieur au seuil, sur les seuls domaines en OK."""
    scores = [v.score for v in verdicts if v.retrieval_status == "OK"]
    return len(scores) >= 2 and rounded(max(scores) - min(scores)) > rounded(gap)


def aggregate(verdicts: list[AgentVerdict], config: DecisionConfig) -> Aggregate:
    received = sorted(v.domain for v in verdicts)
    if received != sorted(DOMAINS):
        raise ValueError(f"un verdict par domaine attendu, reçu : {received}")
    by_domain = {v.domain: v for v in verdicts}

    # somme dans l'ordre fixe des domaines : indépendante de l'ordre d'arrivée des branches
    score = rounded(sum(config.weight(d) * by_domain[d].score for d in DOMAINS))
    go, go_reserves = rounded(config.thresholds.go), rounded(config.thresholds.go_reserves)
    margin = rounded(min(abs(score - go), abs(score - go_reserves)))
    hard_block = any(v.hard_block for v in verdicts)

    if hard_block:
        decision: Decision = "NO_GO"
    elif any(v.retrieval_status == "INSUFFISANT" for v in verdicts):
        decision = "ESCALADE"
    elif conflict(verdicts, config.conflict_gap):
        decision = "ESCALADE"
    elif score >= go:
        decision = "GO"
    elif score >= go_reserves:
        decision = "GO_RESERVES"
    else:
        decision = "NO_GO"
    return Aggregate(decision=decision, score=score, margin=margin, hard_block=hard_block)


def decision_gate(state: ContractState, decision_config: DecisionConfig) -> dict:
    # pas « config » : LangGraph réserve ce nom de paramètre au RunnableConfig
    config = decision_config
    d = aggregate(state["verdicts"], config)
    used = total_tokens(state.get("usage", []))
    limit = config.budget.max_tokens_per_contract
    over = used > limit
    budget_report = {"stage": "budget", "tokens": used, "limit": limit}

    if d.hard_block:                     # 1. NO_GO établi, marge ignorée
        update = {"proposed_decision": "NO_GO", "final_decision": "NO_GO",
                  "margin": d.margin, "route": "explain"}
        if over:
            update["failure_report"] = budget_report   # dépassement tracé quand même
        return update
    if over:                             # 2. budget
        return {"proposed_decision": "ESCALADE", "margin": d.margin,
                "failure_report": budget_report, "route": "human_review"}
    # 3. INSUFFISANT, 4. conflit, 5. seuils : déjà ordonnés par aggregate
    if d.decision == "ESCALADE" or rounded(d.margin) < rounded(config.min_margin):
        return {"proposed_decision": d.decision, "margin": d.margin, "route": "human_review"}
    return {"proposed_decision": d.decision, "final_decision": d.decision,
            "margin": d.margin, "route": "explain"}

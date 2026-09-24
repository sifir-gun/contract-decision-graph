"""Règles du domaine financier : révision de prix et pénalités de retard."""

from cdg.config import DecisionConfig
from cdg.rules._common import below, clause, verdict
from cdg.state import AgentVerdict, Clause, RetrievalStatus


def financier(
    clauses: list[Clause], retrieval_status: RetrievalStatus, config: DecisionConfig
) -> AgentVerdict:
    cfg = config.rules.financier
    hard_block, penalties, findings = False, [], []

    revision = clause(clauses, "revision_prix")
    if revision.present and revision.value is None:
        hard_block = True
        findings.append("blocage : révision de prix non plafonnée")

    penalites = clause(clauses, "penalites_retard")
    if not penalites.present:
        penalties.append(cfg.late_penalties_score_penalty)
        findings.append("pénalités de retard absentes")
    elif penalites.value is not None and below(penalites.value, cfg.late_penalties_min_cap_pct):
        penalties.append(cfg.late_penalties_score_penalty)
        findings.append(
            f"pénalités de retard plafonnées à {penalites.value:g} %, "
            f"sous le minimum de {cfg.late_penalties_min_cap_pct:g} %"
        )

    return verdict(
        "financier", retrieval_status, hard_block=hard_block, penalties=penalties, findings=findings
    )

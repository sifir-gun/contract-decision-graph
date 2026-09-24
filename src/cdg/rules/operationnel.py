"""Règles du domaine opérationnel : durée d'engagement et préavis de résiliation."""

from cdg.config import DecisionConfig
from cdg.rules._common import above, clause, verdict
from cdg.state import AgentVerdict, Clause, RetrievalStatus


def operationnel(
    clauses: list[Clause], retrieval_status: RetrievalStatus, config: DecisionConfig
) -> AgentVerdict:
    cfg = config.rules.operationnel
    penalties, findings = [], []

    checks = [
        (
            "duree_engagement",
            "durée d'engagement",
            "non chiffrée",
            cfg.commitment_max_months,
            cfg.commitment_score_penalty,
        ),
        (
            "preavis_resiliation",
            "préavis de résiliation",
            "non chiffré",
            cfg.notice_max_months,
            cfg.notice_score_penalty,
        ),
    ]
    for kind, label, unquantified, max_months, penalty in checks:
        c = clause(clauses, kind)
        if not c.present:
            continue
        if c.value is None:  # présente mais non chiffrée : pénalité par prudence
            penalties.append(penalty)
            findings.append(f"{label} {unquantified} : pénalité appliquée par prudence")
        elif above(c.value, max_months):
            penalties.append(penalty)
            findings.append(f"{label} de {c.value:g} mois, au-delà de {max_months:g} mois")

    return verdict(
        "operationnel", retrieval_status, hard_block=False, penalties=penalties, findings=findings
    )

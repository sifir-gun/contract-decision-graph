"""Règles du domaine opérationnel : durée d'engagement et préavis de résiliation."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import Clause
from cdg.domain.rules._common import Assessment, above, clause, penalize


def operationnel(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.operationnel
    findings = []

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
            text = f"{label} {unquantified} : pénalité appliquée par prudence"
            findings.append(penalize(kind, text, penalty))
        elif above(c.value, max_months):
            text = f"{label} de {c.value:g} mois, au-delà de {max_months:g} mois"
            findings.append(penalize(kind, text, penalty))

    return Assessment(domain="operationnel", findings=findings)

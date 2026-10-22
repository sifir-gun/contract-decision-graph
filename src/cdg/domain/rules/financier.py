"""Règles du domaine financier : révision de prix, pénalités d'exécution, délai de paiement."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import Clause
from cdg.domain.rules._common import (
    Assessment,
    capped_penalties,
    clause,
    payment_delay,
    uncapped_revision,
)


def financier(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.financier
    limits = {
        "date_facture": cfg.payment_delay_max_days_invoice,
        "fin_de_mois": cfg.payment_delay_max_days_end_of_month,
        "facture_periodique": cfg.payment_delay_max_days_periodic_invoice,
    }
    findings = [
        *uncapped_revision(clause(clauses, "revision_prix")),
        *capped_penalties(
            clause(clauses, "penalites_execution"),
            "pénalités d'exécution",
            cfg.execution_penalties_min_cap_pct,
            cfg.execution_penalties_score_penalty,
        ),
        *payment_delay(
            clause(clauses, "delai_paiement"), limits, cfg.payment_delay_score_penalty
        ),
    ]
    return Assessment(domain="financier", findings=findings)

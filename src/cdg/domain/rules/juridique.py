"""Règles du domaine juridique : responsabilités de l'acheteur et du fournisseur."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import Clause
from cdg.domain.profiles import spec
from cdg.domain.rules._common import (
    Assessment,
    cap_below_minimum,
    clause,
    uncapped_liability,
)

PARTIES = spec("fournisseur").parties
CAP_BASIS = "du montant annuel"


def juridique(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.juridique
    findings = [
        *uncapped_liability(
            clause(clauses, "responsabilite_acheteur"), PARTIES.client_of
        ),
        *cap_below_minimum(
            clause(clauses, "responsabilite_fournisseur"),
            PARTIES.contractor_of,
            CAP_BASIS,
            cfg.supplier_cap_min_pct,
            cfg.supplier_cap_score_penalty,
        ),
    ]
    return Assessment(domain="juridique", findings=findings)

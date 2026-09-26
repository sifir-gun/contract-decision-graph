"""Règles du domaine juridique : responsabilités de l'acheteur et du fournisseur."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import Clause
from cdg.domain.rules._common import Assessment, below, block, clause, penalize


def juridique(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.juridique
    findings = []

    acheteur = clause(clauses, "responsabilite_acheteur")
    if acheteur.present and acheteur.value is None:
        findings.append(
            block(
                "responsabilite_acheteur",
                "blocage : responsabilité de l'acheteur illimitée",
            )
        )

    fournisseur = clause(clauses, "responsabilite_fournisseur")
    if (
        fournisseur.present
        and fournisseur.value is not None
        and below(fournisseur.value, cfg.supplier_cap_min_pct)
    ):
        findings.append(
            penalize(
                "responsabilite_fournisseur",
                f"plafond de responsabilité du fournisseur à {fournisseur.value:g} % "
                f"du montant annuel, sous le minimum de {cfg.supplier_cap_min_pct:g} %",
                cfg.supplier_cap_score_penalty,
            )
        )

    return Assessment(domain="juridique", findings=findings)

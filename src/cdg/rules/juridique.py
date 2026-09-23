"""Règles du domaine juridique : responsabilités de l'acheteur et du fournisseur."""

from cdg.config import DecisionConfig
from cdg.rules._common import below, clause, verdict
from cdg.state import AgentVerdict, Clause, RetrievalStatus


def juridique(clauses: list[Clause], retrieval_status: RetrievalStatus,
              config: DecisionConfig) -> AgentVerdict:
    cfg = config.rules.juridique
    hard_block, penalties, findings = False, [], []

    acheteur = clause(clauses, "responsabilite_acheteur")
    if acheteur.present and acheteur.value is None:
        hard_block = True
        findings.append("blocage : responsabilité de l'acheteur illimitée")

    fournisseur = clause(clauses, "responsabilite_fournisseur")
    if (fournisseur.present and fournisseur.value is not None
            and below(fournisseur.value, cfg.supplier_cap_min_pct)):
        penalties.append(cfg.supplier_cap_score_penalty)
        findings.append(f"plafond de responsabilité du fournisseur à {fournisseur.value:g} % "
                        f"du montant annuel, sous le minimum de {cfg.supplier_cap_min_pct:g} %")

    return verdict("juridique", retrieval_status, hard_block=hard_block,
                   penalties=penalties, findings=findings)

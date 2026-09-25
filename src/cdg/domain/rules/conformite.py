"""Règles du domaine conformité : données personnelles et transferts hors UE (RGPD)."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import AgentVerdict, Clause, RetrievalStatus
from cdg.domain.rules._common import clause, verdict


def conformite(
    clauses: list[Clause], retrieval_status: RetrievalStatus, config: DecisionConfig
) -> AgentVerdict:
    cfg = config.rules.conformite
    hard_block, penalties, findings = False, [], []

    donnees = clause(clauses, "donnees_personnelles")
    accord = clause(clauses, "accord_traitement_donnees")
    if donnees.present and not accord.present:
        hard_block = True
        findings.append(
            "blocage : données personnelles traitées sans accord de traitement (art. 28 RGPD)"
        )

    # transfert hors UE (art. 44 à 46) : la règle juge ce que dit le contrat, jamais une
    # liste de pays ; une catégorie absente est traitée comme « aucune garantie »
    transfert = clause(clauses, "transfert_hors_ue")
    if transfert.present:
        if transfert.category in cfg.transfer_safeguards:
            findings.append(f"transfert hors UE encadré par une garantie : {transfert.category}")
        elif transfert.category in cfg.transfer_authorization_to_verify:
            penalties.append(cfg.transfer_authorization_score_penalty)
            findings.append(
                "autorisation de l'autorité de contrôle à vérifier : transfert hors UE fondé "
                "sur des clauses contractuelles ad hoc, sans mention d'autorisation "
                "(art. 46, par. 3, a) RGPD)"
            )
        elif transfert.category != "sans_transfert":
            hard_block = True
            findings.append(
                "blocage : transfert hors UE annoncé sans garantie reconnue (art. 44 à 46 RGPD)"
            )
    elif donnees.present:
        penalties.append(cfg.unlocated_data_score_penalty)
        findings.append("localisation des données non précisée : à vérifier (art. 44 RGPD)")

    return verdict(
        "conformite",
        retrieval_status,
        hard_block=hard_block,
        penalties=penalties,
        findings=findings,
    )

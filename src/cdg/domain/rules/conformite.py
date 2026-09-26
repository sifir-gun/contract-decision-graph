"""Règles du domaine conformité : données personnelles et transferts hors UE (RGPD)."""

from cdg.domain.config import DecisionConfig
from cdg.domain.models import Clause
from cdg.domain.rules._common import Assessment, block, clause, note, penalize


def conformite(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.conformite
    findings = []

    donnees = clause(clauses, "donnees_personnelles")
    accord = clause(clauses, "accord_traitement_donnees")
    if donnees.present and not accord.present:  # rattaché à l'accord manquant
        findings.append(
            block(
                "accord_traitement_donnees",
                "blocage : données personnelles traitées sans accord de traitement (art. 28 RGPD)",
            )
        )

    # transfert hors UE (art. 44 à 46) : la règle juge ce que dit le contrat, jamais une
    # liste de pays ; une catégorie absente est traitée comme « aucune garantie »
    transfert, kind = clause(clauses, "transfert_hors_ue"), "transfert_hors_ue"
    if transfert.present:
        if transfert.category in cfg.transfer_safeguards:
            text = f"transfert hors UE encadré par une garantie : {transfert.category}"
            findings.append(note(kind, text))
        elif transfert.category in cfg.transfer_authorization_to_verify:
            text = (
                "autorisation de l'autorité de contrôle à vérifier : transfert hors UE fondé "
                "sur des clauses contractuelles ad hoc, sans mention d'autorisation "
                "(art. 46, par. 3, a) RGPD)"
            )
            findings.append(
                penalize(kind, text, cfg.transfer_authorization_score_penalty)
            )
        elif transfert.category != "sans_transfert":
            text = "blocage : transfert hors UE annoncé sans garantie reconnue (art. 44 à 46 RGPD)"
            findings.append(block(kind, text))
    elif donnees.present:  # rattaché à la stipulation de localisation manquante
        text = "localisation des données non précisée : à vérifier (art. 44 RGPD)"
        findings.append(penalize(kind, text, cfg.unlocated_data_score_penalty))

    return Assessment(domain="conformite", findings=findings)

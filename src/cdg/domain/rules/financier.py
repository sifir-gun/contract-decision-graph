"""Règles du domaine financier : révision de prix, pénalités d'exécution, délai de paiement."""

from cdg.domain.config import DecisionConfig, FinancierRules
from cdg.domain.models import AgentVerdict, Clause, RetrievalStatus
from cdg.domain.rules._common import above, below, clause, verdict

DELAY_BASIS_LABELS = {"date_facture": "date de facture", "fin_de_mois": "fin de mois"}
# délai supplétif, cité quand le contrat n'en stipule pas (version de L441-10 en vigueur du
# 26/04/2019 au 01/01/2027) ; à revoir avec la version suivante du texte
DEFAULT_DELAY_NOTE = (
    "délai de paiement non stipulé : sauf dispositions contraires, le délai de règlement ne "
    "peut dépasser trente jours après la date de réception des marchandises ou d'exécution "
    "de la prestation demandée (C. com., art. L441-10, I)"
)


def _payment_delay(delay: Clause, cfg: FinancierRules) -> tuple[list[float], list[str]]:
    if not delay.present:  # pas de pénalité : le délai supplétif s'applique
        return [], [DEFAULT_DELAY_NOTE]
    if delay.value is None:  # présent mais non chiffré : pénalité par prudence
        finding = (
            "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
            "à renégocier"
        )
        return [cfg.payment_delay_score_penalty], [finding]
    if delay.category == "date_facture":
        limit = cfg.payment_delay_max_days_invoice
    else:  # fin de mois, ou point de départ inconnu : le seuil le plus strict
        limit = cfg.payment_delay_max_days_end_of_month
    if not above(delay.value, limit):
        return [], []
    label = DELAY_BASIS_LABELS.get(delay.category, "point de départ non précisé")
    finding = (
        f"délai non conforme, à renégocier : {delay.value:g} jours {label}, "
        f"au-delà de {limit:g} jours"
    )
    return [cfg.payment_delay_score_penalty], [finding]


def financier(
    clauses: list[Clause], retrieval_status: RetrievalStatus, config: DecisionConfig
) -> AgentVerdict:
    cfg = config.rules.financier
    hard_block, penalties, findings = False, [], []

    revision = clause(clauses, "revision_prix")
    if revision.present and revision.value is None:
        hard_block = True
        findings.append("blocage : révision de prix non plafonnée")

    execution = clause(clauses, "penalites_execution")
    if not execution.present:
        penalties.append(cfg.execution_penalties_score_penalty)
        findings.append("pénalités d'exécution absentes")
    elif execution.value is not None and below(
        execution.value, cfg.execution_penalties_min_cap_pct
    ):
        penalties.append(cfg.execution_penalties_score_penalty)
        findings.append(
            f"pénalités d'exécution plafonnées à {execution.value:g} % du montant du contrat, "
            f"sous le minimum de {cfg.execution_penalties_min_cap_pct:g} %"
        )

    delay_penalties, delay_findings = _payment_delay(clause(clauses, "delai_paiement"), cfg)
    penalties += delay_penalties
    findings += delay_findings

    return verdict(
        "financier", retrieval_status, hard_block=hard_block, penalties=penalties, findings=findings
    )

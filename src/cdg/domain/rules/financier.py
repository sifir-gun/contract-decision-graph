"""Règles du domaine financier : révision de prix, pénalités d'exécution, délai de paiement."""

from cdg.domain.config import DecisionConfig, FinancierRules
from cdg.domain.models import Clause
from cdg.domain.rules._common import (
    Assessment,
    RuleFinding,
    above,
    below,
    block,
    clause,
    note,
    penalize,
)

DELAY_BASIS_LABELS = {
    "date_facture": "date de facture",
    "fin_de_mois": "fin de mois",
    "facture_periodique": "après une facture périodique",
}
# délai supplétif, cité quand le contrat n'en stipule pas (version de L441-10 en vigueur du
# 26/04/2019 au 01/01/2027) ; à revoir avec la version suivante du texte
DEFAULT_DELAY_NOTE = (
    "délai de paiement non stipulé : sauf dispositions contraires, le délai de règlement ne "
    "peut dépasser trente jours après la date de réception des marchandises ou d'exécution "
    "de la prestation demandée (C. com., art. L441-10, I)"
)


def _payment_delay(delay: Clause, cfg: FinancierRules) -> list[RuleFinding]:
    kind, penalty = "delai_paiement", cfg.payment_delay_score_penalty
    if not delay.present:  # pas de pénalité : le délai supplétif s'applique
        return [note(kind, DEFAULT_DELAY_NOTE)]
    if delay.value is None:  # présent mais non chiffré : pénalité par prudence
        text = (
            "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
            "à renégocier"
        )
        return [penalize(kind, text, penalty)]
    limits = {
        "date_facture": cfg.payment_delay_max_days_invoice,
        "fin_de_mois": cfg.payment_delay_max_days_end_of_month,
        "facture_periodique": cfg.payment_delay_max_days_periodic_invoice,
    }
    # point de départ inconnu (impossible après vérification) : le seuil le plus strict
    basis = delay.category or ""  # sans point de départ : aucune clé ne correspond
    limit = limits.get(basis, min(limits.values()))
    if not above(delay.value, limit):
        return []
    label = DELAY_BASIS_LABELS.get(basis, "point de départ non précisé")
    text = (
        f"délai non conforme, à renégocier : {delay.value:g} jours {label}, "
        f"au-delà de {limit:g} jours"
    )
    return [penalize(kind, text, penalty)]


def financier(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = config.rules.financier
    findings = []

    revision = clause(clauses, "revision_prix")
    if revision.present and revision.value is None:
        findings.append(
            block("revision_prix", "blocage : révision de prix non plafonnée")
        )

    execution = clause(clauses, "penalites_execution")
    kind, penalty = "penalites_execution", cfg.execution_penalties_score_penalty
    if not execution.present:
        findings.append(penalize(kind, "pénalités d'exécution absentes", penalty))
    elif execution.value is not None and below(
        execution.value, cfg.execution_penalties_min_cap_pct
    ):
        text = (
            f"pénalités d'exécution plafonnées à {execution.value:g} % du montant du contrat, "
            f"sous le minimum de {cfg.execution_penalties_min_cap_pct:g} %"
        )
        findings.append(penalize(kind, text, penalty))

    findings += _payment_delay(clause(clauses, "delai_paiement"), cfg)

    return Assessment(domain="financier", findings=findings)

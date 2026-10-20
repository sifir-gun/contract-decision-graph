"""Règles du profil `sous_traitance` (spec des profils, « Clauses et règles du profil
`sous_traitance` ») : une fonction pure par domaine, comme celles du fournisseur.

Les règles qui existent déjà côté fournisseur (responsabilités, révision de prix, pénalités
d'exécution, délai de paiement) sont les aides de `_common`, appelées avec les libellés du
profil. Les autres lisent leurs catégories dans les listes de la configuration, jamais en
dur : le texte d'un constat dit la catégorie, l'effet vient de la liste qui la porte.

Le profil n'est pas encore enregistré (`profiles.SPECS`, `rules.RULES`) : ce module nomme
`profiles.SOUS_TRAITANCE`, et `RULES_SOUS_TRAITANCE` attend son enregistrement.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from cdg.domain.config import DecisionConfig, SousTraitanceRules
from cdg.domain.models import Clause, Domain
from cdg.domain.numeric import shown
from cdg.domain.profiles import SOUS_TRAITANCE
from cdg.domain.rules._common import (
    Assessment,
    RuleFinding,
    RuleFn,
    below,
    block,
    cap_below_minimum,
    capped_penalties,
    clause,
    payment_delay,
    penalize,
    uncapped_liability,
    uncapped_revision,
)

PARTIES = SOUS_TRAITANCE.parties
CAP_BASIS = "du montant du contrat"
PENALTIES_LABEL = "pénalités de retard d'exécution"

# ce que dit chaque catégorie, dans le texte d'un constat
DESIGN_LABELS = {
    "usage_limite_au_projet": "dessins et documents remis limités au seul projet",
    "confidentialite_seule": (
        "dessins et documents remis protégés par la seule confidentialité, sans limite "
        "d'usage"
    ),
    "reutilisation_autorisee": "réutilisation des dessins et documents remis autorisée",
}
CERTIFICATION_LABELS = {
    "norme_et_preuves": "norme nommée avec preuves de conformité à remettre",
    "norme_sans_preuves": "norme nommée sans preuves de conformité à remettre",
    "conformite_generale": "conformité générale aux normes, sans norme nommée",
}
SCHEDULE_LABELS = {
    "date_contractuelle": (
        "délai d'exécution fixé par une date, un planning ou un délai ferme qui engage "
        "le sous-traitant"
    ),
    "delai_indicatif": "délai d'exécution indicatif, sans date qui engage le sous-traitant",
}


def _rules(config: DecisionConfig) -> SousTraitanceRules:
    if config.sous_traitance is None:
        raise ValueError(
            "configuration sans section sous_traitance : profil sous_traitance "
            "non analysable"
        )
    return config.sous_traitance.rules


def _by_category(
    c: Clause,
    labels: Mapping[str, str],
    penalty: float,
    *,
    accepted: Sequence[str],
    penalized: Sequence[str],
    blocking: Sequence[str] = (),
) -> list[RuleFinding]:
    """Constat d'une clause présente selon la liste réglée qui porte sa catégorie : rien si
    elle est acceptée, une pénalité si elle est pénalisée, un blocage si elle est bloquante.
    Une catégorie absente de toutes les listes ne peut pas arriver (la vérification de
    l'extraction l'exige, la configuration les place toutes) : si elle arrive pourtant,
    erreur explicite, ni acceptation ni constat par défaut."""
    category = c.category
    if category is not None:
        if category in blocking:
            return [block(c.kind, f"blocage : {labels[category]}")]
        if category in penalized:
            return [penalize(c.kind, labels[category], penalty)]
        if category in accepted:
            return []
    raise ValueError(
        f"clause {c.kind} : catégorie absente ou hors des listes réglées de la "
        "configuration"
    )


def juridique_st(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = _rules(config).juridique
    findings = [
        *uncapped_liability(
            clause(clauses, "responsabilite_donneur_ordre"), PARTIES.client_of
        ),
        *cap_below_minimum(
            clause(clauses, "responsabilite_sous_traitant"),
            PARTIES.contractor_of,
            CAP_BASIS,
            cfg.subcontractor_cap_min_pct,
            cfg.subcontractor_cap_score_penalty,
        ),
    ]
    design = clause(clauses, "protection_design")
    if not design.present:
        text = "protection des dessins et documents remis absente"
        findings.append(penalize(design.kind, text, cfg.design_score_penalty))
    else:
        findings += _by_category(
            design,
            DESIGN_LABELS,
            cfg.design_score_penalty,
            accepted=cfg.design_accepted,
            penalized=cfg.design_penalized,
            blocking=cfg.design_blocking,
        )
    return Assessment(domain="juridique", findings=findings)


def financier_st(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = _rules(config).financier
    limits = {
        "date_facture": cfg.payment_delay_max_days_invoice,
        "fin_de_mois": cfg.payment_delay_max_days_end_of_month,
        "facture_periodique": cfg.payment_delay_max_days_periodic_invoice,
    }
    findings = [
        *uncapped_revision(clause(clauses, "revision_prix")),
        *capped_penalties(
            clause(clauses, "penalites_retard_execution"),
            PENALTIES_LABEL,
            cfg.execution_delay_penalties_min_cap_pct,
            cfg.execution_delay_penalties_score_penalty,
        ),
        *payment_delay(
            clause(clauses, "delai_paiement"), limits, cfg.payment_delay_score_penalty
        ),
    ]
    return Assessment(domain="financier", findings=findings)


def _insurance(c: Clause, minimum: float, penalty: float) -> list[RuleFinding]:
    """Assurance du sous-traitant : absente, non chiffrée ou sous le minimum garanti par
    sinistre, pénalité ; les montants sont écrits par `shown` (« 2 000 000 € »)."""
    if not c.present:
        return [penalize(c.kind, "assurance du sous-traitant absente", penalty)]
    if c.value is None:
        return [penalize(c.kind, "assurance du sous-traitant non chiffrée", penalty)]
    if below(c.value, minimum):
        text = (
            f"assurance du sous-traitant de {shown(c.value)} € par sinistre, "
            f"sous le minimum de {shown(minimum)} €"
        )
        return [penalize(c.kind, text, penalty)]
    return []


def conformite_st(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = _rules(config).conformite
    findings = []

    certification = clause(clauses, "obligation_certification")
    if not certification.present:  # la clause absente bloque, sans réglage (spec)
        text = "blocage : obligation de certification absente"
        findings.append(block(certification.kind, text))
    else:
        findings += _by_category(
            certification,
            CERTIFICATION_LABELS,
            cfg.certification_score_penalty,
            accepted=cfg.certification_accepted,
            penalized=cfg.certification_penalized,
        )

    findings += _insurance(
        clause(clauses, "assurance_sous_traitant"),
        cfg.insurance_min_eur,
        cfg.insurance_score_penalty,
    )
    return Assessment(domain="conformite", findings=findings)


def operationnel_st(clauses: list[Clause], config: DecisionConfig) -> Assessment:
    cfg = _rules(config).operationnel
    schedule = clause(clauses, "delai_execution")
    if not schedule.present:  # absent : pénalisé comme un délai indicatif
        text = "délai d'exécution absent"
        findings = [penalize(schedule.kind, text, cfg.schedule_score_penalty)]
    else:
        findings = _by_category(
            schedule,
            SCHEDULE_LABELS,
            cfg.schedule_score_penalty,
            accepted=cfg.schedule_accepted,
            penalized=cfg.schedule_penalized,
        )
    return Assessment(domain="operationnel", findings=findings)


# règles du profil, par domaine, au format de `rules.RULES[profil]`
RULES_SOUS_TRAITANCE: Mapping[Domain, RuleFn] = MappingProxyType(
    {
        "juridique": juridique_st,
        "financier": financier_st,
        "conformite": conformite_st,
        "operationnel": operationnel_st,
    }
)

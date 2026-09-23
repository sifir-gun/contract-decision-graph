"""Fabriques de données synthétiques et doublures pour les tests."""

from cdg.state import DOMAINS, REQUIRED_KINDS, AgentVerdict, Clause, Usage

# Contrat synthétique favorable : aucune règle déclenchée.
FAVORABLE = {
    "responsabilite_acheteur": 100.0,      # plafonnée à 100 % du montant annuel
    "responsabilite_fournisseur": 150.0,   # plafond fournisseur au-dessus du minimum
    "revision_prix": 3.0,                  # révision plafonnée à 3 %
    "penalites_retard": 10.0,              # pénalités plafonnées à 10 %
    "duree_engagement": 24.0,              # mois
    "preavis_resiliation": 3.0,            # mois
    "donnees_personnelles": None,          # traitement présent
    "accord_traitement_donnees": None,     # accord présent
}

ABSENT = object()   # marqueur : la clause ne figure pas dans le contrat


def clauses(**overrides) -> list[Clause]:
    """Les 8 clauses attendues, favorables par défaut.

    `kind=valeur` remplace la valeur d'une clause présente (None compris) ;
    `kind=ABSENT` la rend absente.
    """
    unknown = set(overrides) - set(REQUIRED_KINDS)
    if unknown:
        raise TypeError(f"types de clauses inconnus : {sorted(unknown)}")
    result = []
    for kind in REQUIRED_KINDS:
        value = overrides.get(kind, FAVORABLE[kind])
        if value is ABSENT:
            result.append(Clause(kind=kind, present=False, quote="", value=None))
        else:
            result.append(Clause(kind=kind, present=True,
                                 quote=f"Article synthétique : {kind}.", value=value))
    return result


def verdict(domain, score=1.0, hard_block=False, status="OK") -> AgentVerdict:
    return AgentVerdict(domain=domain, score=score, hard_block=hard_block, findings=[],
                        evidence_ids=[], retrieval_status=status)


def verdicts(**by_domain) -> list[AgentVerdict]:
    """Un verdict favorable par domaine ; `domaine=dict(...)` en surcharge un."""
    return [verdict(d, **by_domain.get(d, {})) for d in DOMAINS]


def usage(tokens_in=0, tokens_out=0, node="double") -> Usage:
    return Usage(node=node, model="double", tokens_in=tokens_in, tokens_out=tokens_out,
                 latency_ms=0)

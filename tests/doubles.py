"""Fabriques de données synthétiques et doublures pour les tests."""

from cdg.state import REQUIRED_KINDS, Clause

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

"""Outils communs aux règles : lecture d'une clause, constats rattachés à leur clause.

Les règles passent avant le CRAG et ne lisent pas le statut de récupération. Chaque constat
porte la clause qui le déclenche et son effet ; la justification par le corpus vient ensuite
(`domain/justification.py`).
"""

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, model_validator

from cdg.domain.config import DecisionConfig, Penalty
from cdg.domain.models import Clause, Domain
from cdg.domain.numeric import rounded

# blocage ou pénalité : le constat exige une référence en vigueur ; information : il est
# justifié si le corpus le permet, sans effet sur le statut
Effect = Literal["blocage", "penalite", "information"]


class RuleFinding(BaseModel):
    """Constat d'une règle, rattaché à la clause qui le déclenche."""

    kind: str  # clause qui déclenche le constat
    text: str
    effect: Effect
    penalty: Penalty | None = (
        None  # pénalité de score, pour l'effet « penalite » seulement
    )

    @model_validator(mode="after")
    def _penalite_selon_l_effet(self) -> "RuleFinding":
        if (self.effect == "penalite") != (self.penalty is not None):
            raise ValueError(
                f"constat {self.kind} : une pénalité de score va avec l'effet « penalite », "
                f"et seulement avec lui (effet {self.effect}, pénalité {self.penalty})"
            )
        return self

    @property
    def requires_reference(self) -> bool:
        return self.effect != "information"


class Assessment(BaseModel):
    """Issue des règles d'un domaine, avant le CRAG : les constats, d'où le score et le
    blocage. Un blocage ne touche pas le score."""

    domain: Domain
    findings: list[RuleFinding]

    @property
    def hard_block(self) -> bool:
        return any(f.effect == "blocage" for f in self.findings)

    @property
    def score(self) -> float:
        """1,0 moins les pénalités, borné à [0, 1]."""
        penalties = sum(f.penalty for f in self.findings if f.penalty is not None)
        return rounded(min(1.0, max(0.0, 1.0 - penalties)))

    def kinds_to_justify(self) -> list[str]:
        """Clauses qui portent un constat, dans l'ordre des constats : le CRAG ne cherche
        que pour elles."""
        return list(dict.fromkeys(f.kind for f in self.findings))

    def kinds_requiring_reference(self) -> list[str]:
        return list(
            dict.fromkeys(f.kind for f in self.findings if f.requires_reference)
        )


RuleFn = Callable[[list[Clause], DecisionConfig], Assessment]


def block(kind: str, text: str) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="blocage")


def penalize(kind: str, text: str, penalty: float) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="penalite", penalty=penalty)


def note(kind: str, text: str) -> RuleFinding:
    return RuleFinding(kind=kind, text=text, effect="information")


def clause(clauses: list[Clause], kind: str) -> Clause:
    matches = [c for c in clauses if c.kind == kind]
    if len(matches) != 1:
        raise ValueError(
            f"clause {kind} : {len(matches)} occurrence(s), une seule attendue"
        )
    return matches[0]


def below(value: float, threshold: float) -> bool:
    return rounded(value) < rounded(threshold)


def above(value: float, threshold: float) -> bool:
    return rounded(value) > rounded(threshold)


# --- Règles partagées entre profils ---------------------------------------------------------
# Une règle commune lit une clause et rend ses constats ; les libellés qui changent d'un profil
# à l'autre (parties, base du plafond, nom des pénalités) sont des paramètres. Le texte d'un
# constat est celui du profil `fournisseur` quand on lui passe les libellés du fournisseur
# (`tests/test_demo.py`, constats figés).

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


def uncapped_liability(c: Clause, party_of: str) -> list[RuleFinding]:
    """Blocage : responsabilité stipulée sans plafond. `party_of` : « de l'acheteur »."""
    if c.present and c.value is None:
        return [block(c.kind, f"blocage : responsabilité {party_of} illimitée")]
    return []


def cap_below_minimum(
    c: Clause, party_of: str, basis: str, minimum: float, penalty: float
) -> list[RuleFinding]:
    """Pénalité : plafond de responsabilité chiffré sous le minimum (une responsabilité
    illimitée ou absente est favorable). `party_of` : « du fournisseur » ; `basis` : « du
    montant annuel »."""
    if c.present and c.value is not None and below(c.value, minimum):
        text = (
            f"plafond de responsabilité {party_of} à {c.value:g} % {basis}, "
            f"sous le minimum de {minimum:g} %"
        )
        return [penalize(c.kind, text, penalty)]
    return []


def uncapped_revision(c: Clause) -> list[RuleFinding]:
    """Blocage : révision de prix stipulée sans plafond."""
    if c.present and c.value is None:
        return [block(c.kind, "blocage : révision de prix non plafonnée")]
    return []


def capped_penalties(
    c: Clause, label: str, minimum: float, penalty: float
) -> list[RuleFinding]:
    """Pénalité : pénalités absentes, ou plafonnées sous le minimum (non plafonnées, elles
    sont favorables). `label` : « pénalités d'exécution »."""
    if not c.present:
        return [penalize(c.kind, f"{label} absentes", penalty)]
    if c.value is not None and below(c.value, minimum):
        text = (
            f"{label} plafonnées à {c.value:g} % du montant du contrat, "
            f"sous le minimum de {minimum:g} %"
        )
        return [penalize(c.kind, text, penalty)]
    return []


def payment_delay(
    c: Clause, limits: Mapping[str, float], penalty: float
) -> list[RuleFinding]:
    """Délai de paiement (C. com., art. L441-10) : `limits` donne le plafond en jours de
    chaque point de départ (`DELAY_BASIS_LABELS`)."""
    if not c.present:  # pas de pénalité : le délai supplétif s'applique
        return [note(c.kind, DEFAULT_DELAY_NOTE)]
    if c.value is None:  # présent mais non chiffré : pénalité par prudence
        text = (
            "délai de paiement non chiffré : pénalité par prudence, délai non conforme, "
            "à renégocier"
        )
        return [penalize(c.kind, text, penalty)]
    # point de départ inconnu (impossible après vérification) : le seuil le plus strict
    basis = c.category or ""  # sans point de départ : aucune clé ne correspond
    limit = limits.get(basis, min(limits.values()))
    if not above(c.value, limit):
        return []
    label = DELAY_BASIS_LABELS.get(basis, "point de départ non précisé")
    text = (
        f"délai non conforme, à renégocier : {c.value:g} jours {label}, "
        f"au-delà de {limit:g} jours"
    )
    return [penalize(c.kind, text, penalty)]

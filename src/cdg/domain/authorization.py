"""Autorisation (PR D2, ADR 005) : règles pures, sans framework ni horloge.

- Acteur : qui agit, et par quel canal. Interface authentifiée : l'émetteur et
  l'identifiant stable (sub) du jeton vérifié. Interface locale (poste, sans
  authentification) et CLI : non authentifiées ; la CLI nomme un opérateur non
  nominatif, et marque l'accès d'urgence. Jamais de nom ni d'e-mail : l'acteur est
  scellé.
- Rôles : tirés des groupes du jeton vérifié, par une correspondance réglable. L'analyste
  lance une analyse ; le relecteur tranche une revue et expire les contrats en attente ;
  la lecture est ouverte aux deux.
- Quatre yeux : la revue est refusée à qui a lancé l'analyse, et à tout autre canal que
  celui de l'analyse (changer de porte ne change pas de personne), sauf en accès
  d'urgence, tracé et scellé. Les outils locaux, sans identité, n'existent pas dans le
  cluster (démarrage et rendu refusés).
- Second facteur, réglable pour le relecteur : une valeur amr ou acr acceptée.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from cdg.domain.identity import Identity

ROLES = ("analyste", "relecteur")
ACTIONS: dict[str, tuple[str, ...]] = {
    "lire": ("analyste", "relecteur"),
    "analyser": ("analyste",),
    "trancher": ("relecteur",),
    "expirer": ("relecteur",),
}
# identifiant d'opérateur de la CLI : non nominatif (astreinte-1, lot-nocturne), jamais
# une adresse ni un nom avec des espaces
OPERATOR = re.compile(r"[a-z0-9][a-z0-9-]{1,62}")


class AuthorizationConfigError(Exception):
    """Correspondance des rôles inutilisable : l'interface ne démarre pas."""


class Actor(BaseModel):
    """Qui agit, par quel canal ; scellé tel quel (journal d'audit v2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canal: Literal["interface", "locale", "cli"]
    authentifie: bool
    iss: str | None = None
    sub: str | None = None
    operateur: str | None = None
    urgence: bool = False  # accès d'urgence par la CLI : tracé et scellé

    @model_validator(mode="after")
    def _coherent(self) -> "Actor":
        identity = (self.iss, self.sub)
        if self.canal == "interface":
            if not (self.authentifie and self.iss and self.sub):
                raise ValueError("interface : authentifiée, avec iss et sub")
            if self.operateur or self.urgence:
                raise ValueError("interface : ni opérateur ni accès d'urgence")
        elif self.authentifie or any(identity):
            raise ValueError(f"{self.canal} : non authentifié, sans iss ni sub")
        if self.canal == "locale" and (self.operateur or self.urgence):
            raise ValueError("interface locale : ni opérateur ni accès d'urgence")
        if self.canal == "cli" and not OPERATOR.fullmatch(self.operateur or ""):
            raise ValueError(
                "CLI : un opérateur non nominatif (minuscules, chiffres, tirets)"
            )
        return self


def interface_actor(identity: Identity) -> Actor:
    """Acteur de l'interface authentifiée : l'identité vérifiée, sans le nom affiché."""
    return Actor(
        canal="interface", authentifie=True, iss=identity.issuer, sub=identity.subject
    )


# --- rôles ---------------------------------------------------------------------------------


def check_mapping(mapping: Mapping[str, Sequence[str]]) -> None:
    """Chaque rôle connu, chacun avec au moins un groupe."""
    unknown = sorted(set(mapping) - set(ROLES))
    if unknown:
        raise AuthorizationConfigError(f"rôle inconnu : {', '.join(unknown)}")
    for role in ROLES:
        if not mapping.get(role):
            raise AuthorizationConfigError(f"rôle {role} sans groupe")


def roles(identity: Identity, mapping: Mapping[str, Iterable[str]]) -> set[str]:
    """Rôles de l'identité vérifiée : ceux dont un groupe figure dans le jeton."""
    groups = set(identity.groups)
    return {role for role, names in mapping.items() if groups & set(names)}


def required_roles(action: str) -> tuple[str, ...]:
    return ACTIONS[action]


def permitted(granted: set[str], action: str) -> bool:
    return bool(granted & set(ACTIONS[action]))


# --- quatre yeux ---------------------------------------------------------------------------


def four_eyes(analyst: Actor | None, reviewer: Actor) -> str | None:
    """None si la revue est admise, sinon le motif du refus."""
    if reviewer.urgence:
        return None  # accès d'urgence : admis, tracé au journal des accès et scellé
    if not reviewer.authentifie:
        return None  # outils locaux : aucune identité à comparer, hors du cluster
    if analyst is None:
        return (
            "analyste inconnu (analyse antérieure aux rôles) : revue refusée ; à "
            "trancher en accès d'urgence ou à expirer"
        )
    if analyst.canal != reviewer.canal:
        return (
            f"analyse lancée par un autre canal ({analyst.canal}) : revue refusée "
            "(quatre yeux), sauf en accès d'urgence"
        )
    if (analyst.iss, analyst.sub) == (reviewer.iss, reviewer.sub):
        return "le relecteur a lancé l'analyse : revue refusée (quatre yeux)"
    return None


# --- second facteur ------------------------------------------------------------------------


@dataclass(frozen=True)
class SecondFactor:
    """Preuves acceptées d'un second facteur ; aucune : non exigé."""

    amr: tuple[str, ...] = ()
    acr: tuple[str, ...] = ()

    @property
    def required(self) -> bool:
        return bool(self.amr or self.acr)


def second_factor_proven(identity: Identity, policy: SecondFactor) -> bool:
    if not policy.required:
        return True
    if set(identity.amr) & set(policy.amr):
        return True
    return identity.acr is not None and identity.acr in policy.acr

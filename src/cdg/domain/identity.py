"""Identité d'un utilisateur authentifié (PR D1, ADR 005) : règles pures sur un jeton
d'identité OIDC, appliquées par l'adaptateur qui le vérifie (`adapters/oidc.py`).

- Liste fermée d'algorithmes : ceux que publie le fournisseur, parmi les algorithmes
  asymétriques ; jamais « none », jamais un algorithme symétrique (un secret partagé
  laisserait signer quiconque le connaît, et la clé publique servirait de secret).
- Revendications obligatoires : exp, iat, iss, aud, sub.
- Émetteur et clés en HTTPS ; HTTP admis seulement sur la boucle locale (tests).
- L'identité retenue ne garde ni e-mail ni nom : l'émetteur et l'identifiant stable (sub)
  désignent l'utilisateur ; le nom affiché ne sert qu'à l'affichage, jamais scellé.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

ASYMMETRIC_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub")
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class IdentityConfigurationError(Exception):
    """Fournisseur d'identité inutilisable tel qu'il est configuré ou publié."""


class InvalidClaims(Exception):
    """Revendications d'un jeton vérifié inexploitables (sub vide, groupes mal formés)."""


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    groups: tuple[str, ...]
    display_name: str  # affichage seulement : jamais scellé, jamais journalisé
    amr: tuple[str, ...] = ()
    acr: str | None = None


def allowed_algorithms(published: Iterable[str]) -> list[str]:
    """Algorithmes admis : ceux que publie le fournisseur, s'ils sont asymétriques."""
    allowed: list[str] = []
    for algorithm in published:
        if algorithm in ASYMMETRIC_ALGORITHMS and algorithm not in allowed:
            allowed.append(algorithm)
    if not allowed:
        raise IdentityConfigurationError(
            "le fournisseur ne publie aucun algorithme de signature asymétrique admis "
            f"(id_token_signing_alg_values_supported : {list(published)})"
        )
    return allowed


def check_issuer(url: str) -> None:
    """Émetteur ou adresse de clés : HTTPS, ou HTTP sur la boucle locale seulement."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in LOOPBACK:
        return
    raise IdentityConfigurationError(
        f"adresse du fournisseur d'identité refusée : {url} (https exigé, http "
        "seulement sur la boucle locale)"
    )


def _strings(value: Any, claim: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise InvalidClaims(f"revendication {claim} mal formée")


def from_claims(claims: Mapping[str, Any]) -> Identity:
    """Identité d'un jeton déjà vérifié (signature, émetteur, audience, expiration)."""
    issuer, subject = claims.get("iss"), claims.get("sub")
    if not isinstance(issuer, str) or not issuer:
        raise InvalidClaims("revendication iss vide")
    if not isinstance(subject, str) or not subject:
        raise InvalidClaims("revendication sub vide")
    acr = claims.get("acr")
    if acr is not None and not isinstance(acr, str):
        raise InvalidClaims("revendication acr mal formée")
    shown = claims.get("preferred_username") or claims.get("name") or subject
    return Identity(
        issuer=issuer,
        subject=subject,
        groups=_strings(claims.get("groups"), "groups"),
        display_name=shown if isinstance(shown, str) else subject,
        amr=_strings(claims.get("amr"), "amr"),
        acr=acr,
    )

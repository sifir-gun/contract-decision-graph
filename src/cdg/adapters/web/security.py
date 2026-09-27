"""Sécurité de l'interface web, sans dépendance ajoutée.

- Écoute locale par défaut : toute autre adresse exige une option explicite, et affiche un
  avertissement (l'interface n'a pas d'authentification).
- Hôtes admis : l'en-tête Host doit nommer l'adresse d'écoute, contre le rebond DNS (une
  page tierce qui ferait résoudre son propre nom vers 127.0.0.1).
- En-têtes : CSP stricte sans script ni style en ligne, et les en-têtes usuels.
- CSRF : un cookie aléatoire (HttpOnly, SameSite=Strict) et, dans chaque formulaire qui
  modifie, son HMAC par un secret du processus ; l'en-tête Origin, s'il est présent,
  doit être celui de l'interface.
- Taille des envois : bornée avant toute lecture, sous le seuil au-delà duquel l'analyseur
  de formulaires écrirait un envoi sur disque.
"""

import hashlib
import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

from cdg.domain.config import DecisionConfig

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
    "cache-control": "no-store",  # dossiers de contrats : jamais en cache
}
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
# au-delà, l'analyseur de formulaires (python-multipart, par Starlette) écrit un fichier
# envoyé dans un fichier temporaire : le texte original toucherait le disque
SPOOL_BYTES = 1024 * 1024
FORM_OVERHEAD_BYTES = 64 * 1024  # autres champs et délimiteurs d'un formulaire


class WebConfigError(Exception):
    """Réglage de l'interface refusé : l'interface ne démarre pas."""


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _wildcard(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def bind_warning(host: str, *, allow_non_local: bool) -> str | None:
    """None pour une écoute locale ; sinon l'avertissement à afficher, si l'option
    explicite est donnée, ou une erreur."""
    if _loopback(host):
        return None
    if not allow_non_local:
        raise WebConfigError(
            f"écoute sur {host} refusée : l'interface n'a pas d'authentification. "
            "Écoute locale par défaut (127.0.0.1) ; --ecoute-non-locale pour passer "
            "outre, en connaissance de cause."
        )
    return (
        f"AVERTISSEMENT : l'interface écoute sur {host}, sans authentification. Toute "
        "personne qui joint cette adresse peut lancer des analyses payantes, trancher "
        "les revues et lire les contrats masqués."
    )


def allowed_hosts(host: str) -> list[str] | None:
    """Noms admis dans l'en-tête Host ; None : tous (écoute sur toutes les adresses,
    avec l'option explicite)."""
    if _loopback(host):
        return list(LOOPBACK_NAMES)
    if _wildcard(host):
        return None
    return [host, *LOOPBACK_NAMES]


def host_name(header: str) -> str | None:
    """Nom d'hôte d'un en-tête Host, port retiré, IPv6 sans crochets."""
    try:
        return urlsplit(f"//{header}").hostname
    except ValueError:
        return None


def max_body_bytes(config: DecisionConfig) -> int:
    """Taille maximale d'un envoi : le plus long contrat admis par validate_input, en
    UTF-8 (4 octets par caractère au plus), plus les autres champs."""
    return config.input.max_chars * 4 + FORM_OVERHEAD_BYTES


def check_body_limit(config: DecisionConfig) -> int:
    limit = max_body_bytes(config)
    if limit >= SPOOL_BYTES:
        raise WebConfigError(
            f"taille maximale d'un envoi ({limit} octets, d'après input.max_chars) au-delà "
            f"du seuil d'écriture sur disque des formulaires ({SPOOL_BYTES} octets) : le "
            "texte original d'un contrat toucherait le disque"
        )
    return limit


class Csrf:
    COOKIE = "cdg_csrf"
    FIELD = "csrf"

    def __init__(self, secret: bytes | None = None):
        self._secret = secret if secret is not None else secrets.token_bytes(32)

    @staticmethod
    def new_cookie() -> str:
        return secrets.token_urlsafe(32)

    def token(self, cookie: str) -> str:
        return hmac.new(self._secret, cookie.encode(), hashlib.sha256).hexdigest()

    def valid(self, cookie: str | None, token: str | None) -> bool:
        if not cookie or not token:
            return False
        return hmac.compare_digest(self.token(cookie), token)


def same_origin(origin: str | None, scheme: str, host: str) -> bool:
    """Vrai sans en-tête Origin (le jeton protège seul), ou s'il nomme l'interface."""
    return origin is None or origin == f"{scheme}://{host}"

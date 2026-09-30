"""Vérification des jetons d'identité OIDC (PR D1, ADR 005) : seul module qui importe
PyJWT. La découverte du fournisseur (`/.well-known/openid-configuration`) donne l'adresse
des clés publiques et les algorithmes publiés ; les clés sont gardées en cache, et relues
sur une clé inconnue (rotation), au plus une fois par période. Les requêtes suivent le
proxy de l'environnement (HTTPS_PROXY : le proxy de sortie dans le cluster), jamais une
redirection.
"""

import http.client
import json
import ssl
import threading
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError

import jwt
from jwt import PyJWKClient
from jwt import exceptions as errors

from cdg.domain import identity
from cdg.ports.identity import IdentityRejected, ProviderUnavailable

LEEWAY_SECONDS = 30  # tolérance d'horloge sur exp, iat et nbf : courte (ADR 005)
KEYS_LIFESPAN_SECONDS = 300  # clés publiques gardées cinq minutes, puis relues
REFRESH_COOLDOWN_SECONDS = (
    30  # clé inconnue : une relecture des clés par période, au plus
)
TIMEOUT_SECONDS = 5
DISCOVERY = "/.well-known/openid-configuration"

# motif de refus de chaque erreur de PyJWT, de la plus précise à la plus générale
_DECODE_ERRORS: tuple[tuple[type[Exception], str], ...] = (
    (errors.ExpiredSignatureError, "jeton_expire"),
    (errors.ImmatureSignatureError, "jeton_pas_encore_valide"),
    (errors.InvalidIssuedAtError, "jeton_pas_encore_valide"),
    (errors.InvalidIssuerError, "emetteur_refuse"),
    (errors.InvalidAudienceError, "audience_refusee"),
    (errors.MissingRequiredClaimError, "revendication_absente"),
    (errors.InvalidSignatureError, "signature_invalide"),
    (errors.InvalidAlgorithmError, "algorithme_refuse"),
    (errors.DecodeError, "jeton_illisible"),
    (errors.InvalidTokenError, "jeton_invalide"),
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class _Provider:
    algorithms: list[str]
    keys: PyJWKClient
    end_session: str | None


class OidcVerifier:
    def __init__(self, issuer: str, audience: str, *, ca_file: str | None = None):
        identity.check_issuer(issuer)
        self.issuer, self.audience = issuer, audience
        self._context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        self._lock = threading.Lock()
        self._provider: _Provider | None = None

    def _fetch(self, url: str) -> dict[str, Any]:
        handlers: list[Any] = [_NoRedirect()]
        if self._context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self._context))
        try:
            with urllib.request.build_opener(*handlers).open(
                url, timeout=TIMEOUT_SECONDS
            ) as response:
                data = json.load(response)
        except (URLError, TimeoutError, http.client.HTTPException, OSError) as exc:
            raise ProviderUnavailable(
                f"fournisseur d'identité injoignable : {type(exc).__name__}"
            ) from None
        if not isinstance(data, dict):
            raise identity.IdentityConfigurationError(
                f"découverte du fournisseur illisible : {url}"
            )
        return data

    def _configuration(self) -> _Provider:
        with self._lock:
            if self._provider is None:
                found = self._fetch(self.issuer.rstrip("/") + DISCOVERY)
                if found.get("issuer") != self.issuer:
                    raise identity.IdentityConfigurationError(
                        f"émetteur de la découverte différent : {found.get('issuer')!r}"
                        f" au lieu de {self.issuer!r}"
                    )
                keys_url = found.get("jwks_uri")
                if not isinstance(keys_url, str):
                    raise identity.IdentityConfigurationError("jwks_uri absent")
                identity.check_issuer(keys_url)
                end_session = found.get("end_session_endpoint")
                if isinstance(end_session, str):
                    identity.check_issuer(end_session)
                else:
                    end_session = None
                self._provider = _Provider(
                    algorithms=identity.allowed_algorithms(
                        found.get("id_token_signing_alg_values_supported") or []
                    ),
                    keys=PyJWKClient(
                        keys_url,
                        cache_jwk_set=True,
                        lifespan=KEYS_LIFESPAN_SECONDS,
                        timeout=TIMEOUT_SECONDS,
                        ssl_context=self._context,
                        cooldown_duration=REFRESH_COOLDOWN_SECONDS,
                    ),
                    end_session=end_session,
                )
            return self._provider

    def verify(self, token: str) -> identity.Identity:
        provider = self._configuration()
        try:
            header = jwt.get_unverified_header(token)
        except errors.PyJWTError:
            raise IdentityRejected("jeton_illisible") from None
        if header.get("alg") not in provider.algorithms:
            raise IdentityRejected("algorithme_refuse")
        try:
            key = provider.keys.get_signing_key_from_jwt(token)
        except errors.PyJWKClientConnectionError:
            raise ProviderUnavailable("clés du fournisseur injoignables") from None
        except (errors.PyJWKClientError, errors.PyJWKError):
            raise IdentityRejected("cle_inconnue") from None
        except errors.PyJWTError:
            raise IdentityRejected("jeton_illisible") from None
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=provider.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": list(identity.REQUIRED_CLAIMS)},
            )
        except errors.PyJWTError as exc:
            reason = next(r for kind, r in _DECODE_ERRORS if isinstance(exc, kind))
            raise IdentityRejected(reason) from None
        try:
            return identity.from_claims(claims)
        except identity.InvalidClaims:
            raise IdentityRejected("revendication_invalide") from None

    def end_session_endpoint(self) -> str | None:
        return self._configuration().end_session

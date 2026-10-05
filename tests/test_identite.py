"""Vérification du jeton d'identité par l'application (PR D1, zéro confiance) : oauth2-proxy
transmet le jeton d'identité signé, l'application en vérifie la signature (clés publiques
du fournisseur, découverte OIDC), l'émetteur, l'audience et l'expiration avant de croire
l'identité. Liste fermée d'algorithmes : ceux que publie le fournisseur, jamais « none »,
jamais un algorithme symétrique. Revendications obligatoires : exp, iat, iss, aud, sub.

Un faux fournisseur OIDC, local, sert la découverte et les clés publiques ; les jetons sont
signés ici, par la bibliothèque de chiffrement du projet."""

import base64
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cdg.adapters import oidc
from cdg.domain import identity
from cdg.ports.identity import IdentityRejected, ProviderUnavailable

AUDIENCE = "cdg-interface"


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return data | {"kid": kid, "use": "sig", "alg": "RS256"}


class Provider:
    """Faux fournisseur OIDC : découverte, clés publiques, compte des récupérations."""

    def __init__(self) -> None:
        self.keys = {"cle-1": new_key()}
        self.algorithms = ["RS256"]
        self.jwks_fetches = 0
        self.issuer_override: str | None = None
        self.keys_status = 200  # code rendu pour les clés (panne simulée sinon)
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_GET(self) -> None:
                if self.path == "/.well-known/openid-configuration":
                    body = {
                        "issuer": provider.issuer_override or provider.issuer,
                        "jwks_uri": f"{provider.issuer}/keys",
                        "id_token_signing_alg_values_supported": provider.algorithms,
                        "end_session_endpoint": f"{provider.issuer}/logout",
                    }
                elif self.path == "/keys" and provider.keys_status != 200:
                    self.send_response(provider.keys_status)
                    self.end_headers()
                    return
                elif self.path == "/keys":
                    provider.jwks_fetches += 1
                    body = {
                        "keys": [public_jwk(k, kid) for kid, k in provider.keys.items()]
                    }
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.issuer = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def claims(self, **changes) -> dict:
        now = int(time.time())
        base = {
            "iss": self.issuer,
            "sub": "CiQwOGE4Njg0Yi1kYjg4LTRiNzMtOTBhOS0zY2QxNjYxZjU0NjYSBWxvY2Fs",
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 600,
            "groups": ["cdg-analystes"],
            "preferred_username": "analyste",
            "email": "analyste@example.org",
        }
        return {k: v for k, v in (base | changes).items() if v is not None}

    def token(self, kid: str = "cle-1", key=None, **changes) -> str:
        signing = key if key is not None else self.keys[kid]
        return jwt.encode(
            self.claims(**changes), signing, algorithm="RS256", headers={"kid": kid}
        )


@pytest.fixture
def provider():
    served = Provider()
    yield served
    served.server.shutdown()
    served.server.server_close()


@pytest.fixture
def verifier(provider):
    return oidc.OidcVerifier(provider.issuer, AUDIENCE)


def b64(data: dict | bytes) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def rejected(verifier, token: str) -> str:
    with pytest.raises(IdentityRejected) as raised:
        verifier.verify(token)
    return raised.value.reason


# --- règles pures ------------------------------------------------------------------------


def test_algorithmes_publies_filtres_jamais_none_ni_symetriques():
    published = ["RS256", "HS256", "none", "ES256", "HS512", "PS256"]
    assert identity.allowed_algorithms(published) == ["RS256", "ES256", "PS256"]
    with pytest.raises(identity.IdentityConfigurationError, match="asymétrique"):
        identity.allowed_algorithms(["HS256", "none"])


def test_identite_tiree_des_revendications_verifiees():
    found = identity.from_claims(
        {
            "iss": "https://idp.example.org",
            "sub": "abc",
            "groups": ["cdg-relecteurs"],
            "name": "Nom Affiché",
            "preferred_username": "relecteur",
            "amr": ["pwd", "otp"],
        }
    )
    assert (found.issuer, found.subject, found.groups) == (
        "https://idp.example.org",
        "abc",
        ("cdg-relecteurs",),
    )
    assert found.display_name == "relecteur"  # nom affiché, jamais scellé
    assert found.amr == ("pwd", "otp")


def test_emetteur_en_https_seulement_hors_boucle_locale():
    with pytest.raises(identity.IdentityConfigurationError, match="https"):
        identity.check_issuer("http://idp.example.org")
    identity.check_issuer("https://idp.example.org")
    identity.check_issuer("http://127.0.0.1:5556")  # fournisseur de test local


# --- jeton valide ------------------------------------------------------------------------


def test_jeton_valide_accepte(provider, verifier):
    found = verifier.verify(provider.token())
    assert found.issuer == provider.issuer and found.groups == ("cdg-analystes",)
    assert found.subject.startswith("CiQw")


def test_tolerance_d_horloge_courte(provider, verifier):
    now = int(time.time())
    assert oidc.LEEWAY_SECONDS == 30
    verifier.verify(provider.token(exp=now - 10))  # dans la tolérance
    assert rejected(verifier, provider.token(exp=now - 60)) == "jeton_expire"
    assert (
        rejected(verifier, provider.token(iat=now + 120)) == "jeton_pas_encore_valide"
    )


# --- attaques ----------------------------------------------------------------------------


def test_algorithme_none_refuse(provider, verifier):
    token = f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': 'cle-1'})}.{b64(provider.claims())}."
    assert rejected(verifier, token) == "algorithme_refuse"


def test_confusion_rs256_hs256_cle_publique_comme_secret(provider, verifier):
    public_pem = (
        provider.keys["cle-1"]
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    header = b64({"alg": "HS256", "typ": "JWT", "kid": "cle-1"})
    body = b64(provider.claims())
    import hashlib
    import hmac

    signature = hmac.new(public_pem, f"{header}.{body}".encode(), hashlib.sha256)
    token = f"{header}.{body}.{b64(signature.digest())}"
    assert rejected(verifier, token) == "algorithme_refuse"


def test_jeton_d_un_autre_emetteur_refuse(provider, verifier):
    token = provider.token(iss="https://autre-fournisseur.example.org")
    assert rejected(verifier, token) == "emetteur_refuse"


def test_jeton_d_une_autre_audience_refuse(provider, verifier):
    assert rejected(verifier, provider.token(aud="autre-application")) == (
        "audience_refusee"
    )


def test_jeton_expire_refuse(provider, verifier):
    now = int(time.time())
    token = provider.token(iat=now - 3600, exp=now - 1800)
    assert rejected(verifier, token) == "jeton_expire"


def test_jeton_signe_par_une_cle_inconnue_refuse(provider, verifier):
    token = provider.token(kid="cle-attaquant", key=new_key())
    assert rejected(verifier, token) == "cle_inconnue"


def test_kid_forge_refuse(provider, verifier):
    # kid d'une vraie clé, signature d'une autre : la signature ne correspond pas
    assert rejected(verifier, provider.token(kid="cle-1", key=new_key())) == (
        "signature_invalide"
    )
    # kid qui ne désigne aucune clé publiée (chemin, injection) : aucune clé
    for forged in ("../../etc/passwd", "cle-1' OR '1'='1", ""):
        token = provider.token(kid=forged, key=provider.keys["cle-1"])
        assert rejected(verifier, token) in {"cle_inconnue", "jeton_illisible"}


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub"])
def test_revendication_obligatoire_absente_refusee(provider, verifier, claim):
    assert rejected(verifier, provider.token(**{claim: None})) == (
        "revendication_absente"
    )


def test_jeton_illisible_refuse(verifier):
    assert rejected(verifier, "pas-un-jeton") == "jeton_illisible"


# --- clés du fournisseur : cache et rotation ----------------------------------------------


def test_cles_en_cache_puis_rotation_geree(provider, monkeypatch):
    """Clé inconnue : les clés sont relues, une fois la période de relecture passée
    (REFRESH_COOLDOWN_SECONDS, raccourcie ici)."""
    monkeypatch.setattr(oidc, "REFRESH_COOLDOWN_SECONDS", 0.2)
    verifier = oidc.OidcVerifier(provider.issuer, AUDIENCE)
    verifier.verify(provider.token())
    verifier.verify(provider.token())
    assert provider.jwks_fetches == 1  # en cache
    provider.keys = {"cle-2": new_key()}  # rotation chez le fournisseur
    time.sleep(0.3)
    verifier.verify(provider.token(kid="cle-2"))
    assert provider.jwks_fetches == 2  # relues sur la clé inconnue


def test_rechargement_borne_sur_des_cles_inconnues(provider, verifier):
    verifier.verify(provider.token())
    for n in range(5):
        token = provider.token(kid=f"inconnue-{n}", key=new_key())
        assert rejected(verifier, token) == "cle_inconnue"
    assert provider.jwks_fetches <= 2  # une récupération par période, au plus


# --- découverte ---------------------------------------------------------------------------


def test_emetteur_de_la_decouverte_different_refuse(provider):
    provider.issuer_override = "https://usurpateur.example.org"
    with pytest.raises(identity.IdentityConfigurationError, match="émetteur"):
        oidc.OidcVerifier(provider.issuer, AUDIENCE).verify(provider.token())


def test_fournisseur_injoignable_distinct_d_un_jeton_refuse(provider):
    verifier = oidc.OidcVerifier("http://127.0.0.1:9", AUDIENCE)  # rien n'y écoute
    with pytest.raises(ProviderUnavailable) as raised:
        verifier.verify(provider.token())
    # la cause, par son type seulement (enquête du 05/10 : un 503 sans cause connue)
    assert raised.value.cause == "URLError/ConnectionRefusedError"


def test_cles_injoignables_disent_leur_cause(provider, verifier):
    provider.keys_status = 503
    with pytest.raises(ProviderUnavailable) as raised:
        verifier.verify(provider.token())
    assert raised.value.cause == "HTTPError 503"


TUNNEL = "Tunnel connection failed: 502 Bad Gateway, ne pas citer"


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (
            URLError(ConnectionRefusedError(111, "refusé")),
            "URLError/ConnectionRefusedError",
        ),
        (URLError(OSError(TUNNEL)), "URLError/OSError tunnel 502"),
        (URLError(TimeoutError("délai")), "URLError/TimeoutError"),
        (URLError("raison en texte"), "URLError/str"),
        (TimeoutError("délai"), "TimeoutError"),
        (http.client.RemoteDisconnected("fermé"), "RemoteDisconnected"),
        (OSError("autre"), "OSError"),
    ],
)
def test_cause_par_le_type_jamais_par_le_message(error, cause):
    assert oidc.cause_reseau(error) == cause


def test_cause_d_une_erreur_http_par_son_code():
    error = HTTPError("http://fournisseur.example.org/keys", 503, "indispo", None, None)
    assert oidc.cause_reseau(error) == "HTTPError 503"


def test_cause_lue_sous_l_erreur_de_pyjwt():
    # PyJWT 2.15.1 enchaîne l'erreur réseau d'origine (raise ... from e)
    try:
        try:
            raise URLError(ConnectionRefusedError(111, "refusé"))
        except URLError as inner:
            raise jwt.exceptions.PyJWKClientConnectionError("échec") from inner
    except jwt.exceptions.PyJWKClientConnectionError as error:
        assert oidc.cause_reseau(error) == "URLError/ConnectionRefusedError"


def test_fin_de_session_du_fournisseur_lue_dans_la_decouverte(provider, verifier):
    assert verifier.end_session_endpoint() == f"{provider.issuer}/logout"

"""Interface derrière oauth2-proxy (PR D1, zéro confiance) : chaque requête porte le jeton
d'identité signé (`Authorization: Bearer`), vérifié avant tout ; les en-têtes d'identité
seuls ne suffisent plus. Refus explicites (401, 503) tracés au journal des accès, qui ne
reçoit ni jeton, ni e-mail, ni nom. Déconnexion : session d'oauth2-proxy effacée, puis
session du fournisseur fermée quand il le permet et que le chart le demande."""

import html
import json
import logging
import re
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from doubles import ANALYSTE, ISSUER, FakeVerifier
from fastapi.testclient import TestClient
from web_helpers import CONTRACT_TEXT, client, memory_service

from cdg import cli
from cdg.adapters.web import acces
from cdg.adapters.web.app import Authentication, create_app
from cdg.ports.identity import ProviderUnavailable

PUBLIC = "https://cdg.example.org"
BEARER = {"authorization": "Bearer jeton-analyste"}


def authenticated(verifier=None, provider_logout=False):
    """Client en HTTPS : les cookies sont Secure derrière le proxy (TLS jusqu'à lui),
    et un client en HTTP ne les renverrait pas."""
    app = create_app(
        memory_service(),
        authentication=Authentication(
            verifier=verifier or FakeVerifier(),
            public_origin=PUBLIC,
            client_id="cdg-interface",
            provider_logout=provider_logout,
        ),
    )
    return TestClient(app, base_url="https://127.0.0.1:8000", follow_redirects=False)


def events(caplog) -> list[dict]:
    return [r.acces for r in caplog.records if r.name == "cdg.acces"]


@pytest.fixture(autouse=True)
def _journal_des_acces(caplog):
    caplog.set_level(logging.INFO, logger="cdg.acces")


# --- jeton exigé et vérifié --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/journal", "/static/style.css", "/favicon.ico"])
def test_sans_jeton_refuse_partout(path, caplog):
    response = authenticated().get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert events(caplog) == [
        {"evenement": "acces_refuse", "motif": "jeton_absent", "methode": "GET"}
        | {"chemin": path, "statut": 401}
    ]


def test_en_tetes_d_identite_seuls_ne_suffisent_plus(caplog):
    forged = {
        "x-forwarded-user": "admin",
        "x-forwarded-email": "admin@example.org",
        "x-forwarded-groups": "cdg-relecteurs",
        "x-auth-request-user": "admin",
    }
    assert authenticated().get("/", headers=forged).status_code == 401
    assert events(caplog)[0]["motif"] == "jeton_absent"


def test_jeton_refuse_trace_sans_jeton_ni_identite(caplog, capsys):
    forged = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhZG1pbiJ9."
    response = authenticated().get("/", headers={"authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    [event] = events(caplog)
    assert event["motif"] == "signature_invalide"
    assert forged not in caplog.text and forged not in response.text


def test_fournisseur_injoignable_503_distinct_d_un_refus(caplog):
    response = authenticated(FakeVerifier(unavailable=True)).get("/", headers=BEARER)
    assert response.status_code == 503
    assert events(caplog)[0]["evenement"] == "fournisseur_injoignable"


def test_jeton_valide_page_servie_avec_le_nom_affiche_et_la_deconnexion():
    response = authenticated().get("/", headers=BEARER)
    assert response.status_code == 200
    assert "analyste.affiche" in response.text
    assert 'action="/deconnexion"' in response.text
    assert "sans authentification" not in response.text
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie


def test_origine_des_formulaires_comparee_a_l_adresse_publique():
    web = authenticated()
    page = web.get("/analyse", headers=BEARER)
    token = page.text.split('name="csrf" value="')[1].split('"')[0]
    form = {"csrf": token, "source": "texte", "texte": CONTRACT_TEXT, "partie": "X"}
    local = web.post(
        "/analyse", data=form, headers=BEARER | {"origin": "http://127.0.0.1:8000"}
    )
    assert local.status_code == 403
    public = web.post("/analyse", data=form, headers=BEARER | {"origin": PUBLIC})
    assert public.status_code == 303


# --- déconnexion -------------------------------------------------------------------------


def target(response) -> str:
    """Suite de la déconnexion : lien de la page de transition (et son rafraîchissement
    automatique), vers la fin de session d'oauth2-proxy. Une redirection 303 après le
    formulaire serait soumise à form-action 'self', redirections comprises."""
    assert response.status_code == 200
    [link] = re.findall(r'<a id="suite" href="([^"]+)"', response.text)
    [refresh] = re.findall(r'content="0; url=([^"]+)"', response.text)
    assert link == refresh
    return html.unescape(link)


def logout(web, **headers):
    page = web.get("/", headers=BEARER)
    token = page.text.split('name="csrf" value="')[1].split('"')[0]
    return web.post(
        "/deconnexion", data={"csrf": token}, headers=BEARER | {"origin": PUBLIC}
    )


def test_deconnexion_session_du_proxy_effacee(caplog):
    response = logout(authenticated())
    assert target(response) == "/oauth2/sign_out?rd=%2F"
    [event] = [e for e in events(caplog) if e["evenement"] == "deconnexion"]
    assert event == {
        "evenement": "deconnexion",
        "iss": ISSUER,
        "sub": ANALYSTE.subject,
        "session_fournisseur": "desactivee",
    }
    assert ANALYSTE.display_name not in caplog.text


def test_deconnexion_ferme_aussi_la_session_du_fournisseur(caplog):
    verifier = FakeVerifier(end_session="https://idp.example.org/logout")
    response = logout(authenticated(verifier, provider_logout=True))
    location = urlsplit(target(response))
    assert location.path == "/oauth2/sign_out"
    back = urlsplit(parse_qs(location.query)["rd"][0])
    assert f"{back.scheme}://{back.netloc}{back.path}" == (
        "https://idp.example.org/logout"
    )
    assert parse_qs(back.query) == {
        "client_id": ["cdg-interface"],
        "post_logout_redirect_uri": [f"{PUBLIC}/"],
    }
    assert "jeton-analyste" not in response.text  # jamais le jeton
    assert events(caplog)[-1]["session_fournisseur"] == "fermee"


def test_fournisseur_sans_fin_de_session_le_journal_le_dit(caplog):
    response = logout(
        authenticated(FakeVerifier(end_session=None), provider_logout=True)
    )
    assert target(response) == f"/oauth2/sign_out?rd={quote('/', safe='')}"
    assert events(caplog)[-1]["session_fournisseur"] == "non_publiee"


def test_deconnexion_en_post_seulement_et_absente_sans_identite():
    assert authenticated().get("/deconnexion", headers=BEARER).status_code == 405
    assert client().post("/deconnexion", data={}).status_code in {403, 404}


# --- journal des accès ---------------------------------------------------------------------


def test_journal_des_acces_liste_blanche_de_champs():
    with pytest.raises(ValueError, match="email"):
        acces.event("deconnexion", iss=ISSUER, sub="x", email="a@example.org")
    with pytest.raises(ValueError, match="inconnu"):
        acces.event("evenement_inconnu")


def test_journal_des_acces_en_json(capsys):
    from cdg.adapters import journaux

    formatter = journaux.JsonFormatter()
    record = logging.LogRecord(
        "cdg.acces", logging.INFO, "", 0, "deconnexion", (), None
    )
    record.acces = {"evenement": "deconnexion", "iss": ISSUER, "sub": "s"}
    entry = json.loads(formatter.format(record))
    assert entry["evenement"] == "deconnexion" and entry["sub"] == "s"
    assert entry["journal"] == "cdg.acces"


# --- commande web ----------------------------------------------------------------------------


IDENTITY = [
    "web",
    "--identite",
    "en-tetes",
    "--oidc-emetteur",
    ISSUER,
    "--oidc-audience",
    "cdg-interface",
    "--adresse-publique",
    PUBLIC,
    "--cles-csrf",
    "/run/secrets/csrf",
]


def run_cli(argv, capsys) -> tuple[int, dict]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    lines = (captured.err if code else captured.out).strip().splitlines()
    return code, json.loads(lines[-1])


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.42.0.7", "localhost"])
def test_mode_identite_refuse_de_demarrer_hors_de_127_0_0_1(host, capsys):
    code, error = run_cli([*IDENTITY, "--host", host], capsys)
    assert code == 1
    assert "127.0.0.1" in error["detail"] and "--identite en-tetes" in error["detail"]


def test_mode_identite_incompatible_avec_l_ecoute_non_locale(capsys):
    code, error = run_cli([*IDENTITY, "--ecoute-non-locale"], capsys)
    assert code == 1 and "--ecoute-non-locale" in error["detail"]


@pytest.mark.parametrize(
    "option",
    ["--oidc-emetteur", "--oidc-audience", "--adresse-publique", "--cles-csrf"],
)
def test_mode_identite_exige_emetteur_audience_et_adresse_publique(option, capsys):
    argv = list(IDENTITY)
    index = argv.index(option)
    del argv[index : index + 2]
    code, error = run_cli(argv, capsys)
    assert code == 1 and option in error["detail"]


def with_keys(folder, **files) -> list[str]:
    folder.mkdir()
    for name, value in files.items():
        (folder / name).write_text(value, encoding="utf-8")
    argv = list(IDENTITY)
    argv[argv.index("--cles-csrf") + 1] = str(folder)
    return argv


def test_mode_identite_cles_csrf_absentes_refus_de_demarrer(tmp_path, capsys):
    code, error = run_cli(with_keys(tmp_path / "cles"), capsys)
    assert code == 1 and "courante" in error["detail"]


def test_mode_identite_cle_csrf_trop_courte_jamais_montree(tmp_path, capsys):
    code, error = run_cli(
        with_keys(tmp_path / "cles", courante="zzzz-secret-zzzz"), capsys
    )
    assert code == 1 and "32" in error["detail"]
    assert "zzzz-secret-zzzz" not in json.dumps(error)


def test_adresse_publique_en_https(capsys):
    argv = [a if a != PUBLIC else "http://cdg.example.org" for a in IDENTITY]
    code, error = run_cli(argv, capsys)
    assert code == 1 and "https" in error["detail"]


def test_autre_schema_d_authentification_refuse(caplog):
    response = authenticated().get("/", headers={"authorization": "Basic YWRtaW46eA=="})
    assert response.status_code == 401
    assert events(caplog)[0]["motif"] == "jeton_absent"


def test_deconnexion_fournisseur_injoignable_le_journal_le_dit(caplog):
    class Unreachable(FakeVerifier):
        def end_session_endpoint(self):
            raise ProviderUnavailable("injoignable")

    response = logout(authenticated(Unreachable(), provider_logout=True))
    assert target(response) == "/oauth2/sign_out?rd=%2F"
    assert events(caplog)[-1]["session_fournisseur"] == "fournisseur_injoignable"


def test_journal_des_acces_en_texte():
    from cdg.adapters import journaux

    record = logging.LogRecord(
        "cdg.acces", logging.INFO, "", 0, "deconnexion", (), None
    )
    record.acces = {"evenement": "deconnexion", "sub": "s"}
    line = journaux.TextFormatter().format(record)
    assert line.endswith("deconnexion evenement=deconnexion sub=s")

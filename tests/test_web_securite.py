"""Interface web : souveraineté et sécurité (décision du 27/09, points 5 à 10).

Aucune ressource externe ; écoute locale par défaut ; hôtes admis (contre le rebond DNS) ;
en-têtes de sécurité ; jeton CSRF sur tout formulaire qui modifie ; tout texte d'un
contrat ou d'un LLM échappé ; texte original jamais conservé ; taille et type des envois
vérifiés avant tout traitement.
"""

import base64
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import pytest
from doubles import ABSENT, CONTRACT_TEXT, FixedExtractor, clauses
from web_helpers import (
    CONFIG,
    PENDING_TEXT,
    analyse,
    client,
    csrf,
    memory_service,
)

from cdg.adapters.web import presentation, security
from cdg.adapters.web.app import WEB_ROOT, create_app
from cdg.domain.explanation import Draft, ExplainedFinding
from cdg.domain.models import Usage
from cdg.settings import SettingsError

TEMPLATES, STATIC = WEB_ROOT / "templates", WEB_ROOT / "static"
# empreinte publiée par la documentation d'HTMX pour htmx.org@2.0.11/dist/htmx.min.js,
# égale à celle du fichier de l'archive npm (intégrité sha512 du registre vérifiée)
HTMX_SHA384 = "2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg"
EXTERNAL = re.compile(
    r"(?i)(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}|\burl\(\s*['\"]?https?:"
)


# --- 5. aucune ressource externe ------------------------------------------------------------


def web_files() -> list[Path]:
    files = sorted(p for p in WEB_ROOT.rglob("*") if p.is_file())
    assert files, WEB_ROOT
    return [p for p in files if p.suffix in {".html", ".css", ".js"}]


@pytest.mark.parametrize("path", web_files(), ids=lambda p: p.name)
def test_aucune_url_externe_dans_les_gabarits_et_fichiers_statiques(path):
    assert not EXTERNAL.findall(path.read_text(encoding="utf-8"))


def test_detection_d_une_url_externe():
    for sample in (
        '<script src="https://cdn.example.org/x.js">',
        "<link href='//fonts.example.com/css'>",
        "background: url(http://example.net/a.png)",
    ):
        assert EXTERNAL.findall(sample), sample
    assert not EXTERNAL.findall('<a href="/contrats/c1">')


def test_htmx_copie_dans_le_depot_avec_sa_licence_et_son_empreinte():
    data = (STATIC / "htmx.min.js").read_bytes()
    assert base64.b64encode(hashlib.sha384(data).digest()).decode() == HTMX_SHA384
    assert "Zero-Clause BSD" in (STATIC / "htmx-LICENSE.txt").read_text()


def test_aucun_filtre_safe_ni_echappement_coupe_dans_les_gabarits():
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\|\s*safe\b", text), path.name
        assert "autoescape false" not in text, path.name


# --- 6. écoute locale par défaut ----------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_ecoute_locale_sans_avertissement(host):
    assert security.bind_warning(host, allow_non_local=False) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_ecoute_non_locale_refusee_sans_option_explicite(host):
    with pytest.raises(security.WebConfigError, match="authentification"):
        security.bind_warning(host, allow_non_local=False)
    warning = security.bind_warning(host, allow_non_local=True)
    assert "sans authentification" in warning


def test_hotes_admis():
    assert set(security.allowed_hosts("127.0.0.1")) == {"127.0.0.1", "localhost", "::1"}
    assert "192.168.1.10" in security.allowed_hosts("192.168.1.10")
    assert security.allowed_hosts("0.0.0.0") is None  # toute adresse : option explicite


def test_hote_non_admis_refuse():
    web = client()
    assert web.get("/", headers={"host": "rebond.example"}).status_code == 400
    assert web.get("/", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert web.get("/", headers={"host": "[::1]:8000"}).status_code == 200


# --- 7. en-têtes de sécurité, CSRF ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/analyse", "/static/style.css", "/absent"])
def test_en_tetes_de_securite_sur_chaque_reponse(path):
    headers = client().get(path).headers
    assert headers["content-security-policy"] == security.CSP
    assert "'unsafe-inline'" not in security.CSP
    assert "script-src 'self'" in security.CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"


def test_cookie_csrf_strict_et_inaccessible_au_javascript():
    web = client()
    cookie = web.get("/analyse").headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace(
        "Strict", "strict"
    )


FORMS = [
    ("/analyse", {"source": "texte", "texte": CONTRACT_TEXT}),
    ("/contrats/c-web/decision", {"decision": "GO", "relecteur": "x", "motif": "y"}),
    ("/administration/expiration", {"heures": "24", "confirme": "oui"}),
]


@pytest.mark.parametrize(("path", "data"), FORMS)
def test_formulaire_sans_jeton_refuse(path, data):
    web = client()
    web.get("/analyse")  # cookie posé, jeton absent du formulaire
    assert web.post(path, data=data).status_code == 403


@pytest.mark.parametrize(("path", "data"), FORMS)
def test_jeton_d_un_autre_cookie_refuse(path, data):
    other = client()
    token = csrf(other)
    web = client()
    web.get("/analyse")
    assert web.post(path, data={"csrf": token, **data}).status_code == 403


@pytest.mark.parametrize(("path", "data"), FORMS)
def test_origine_etrangere_refusee(path, data):
    web = client()
    token = csrf(web)
    response = web.post(
        path, data={"csrf": token, **data}, headers={"origin": "https://tiers.example"}
    )
    assert response.status_code == 403


def test_jeton_valide_et_meme_origine_acceptes():
    web = client()
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={"csrf": token, "source": "texte", "texte": CONTRACT_TEXT},
        headers={"origin": "http://127.0.0.1:8000"},
    )
    assert response.status_code == 303


# --- 8. tout texte d'un contrat ou d'un LLM est échappé ------------------------------------

SCRIPT = "<script>alert('contrat')</script>"
IMAGE = '<img src=x onerror="alert(1)">'
HOSTILE = f"{CONTRACT_TEXT}\nAnnexe : {SCRIPT} {IMAGE}\n"


class HostileExplainer:
    """Explication d'un LLM qui contient du HTML et du JavaScript."""

    def __call__(self, request, feedback):
        findings = [
            ExplainedFinding(
                id=f.id,
                kind=f.kind,
                references=list(f.references),
                text=f"{SCRIPT} constat <b>{f.kind}</b>",
            )
            for f in request.findings
        ]
        usage = Usage(
            node="explain", model="double", tokens_in=0, tokens_out=0, latency_ms=0
        )
        return Draft(findings=findings), usage


def test_texte_du_contrat_echappe_dans_le_dossier():
    web = client()
    thread = analyse(web, HOSTILE)
    page = web.get(f"/contrats/{thread}").text
    assert SCRIPT not in page and IMAGE not in page
    assert "&lt;script&gt;alert(&#39;contrat&#39;)&lt;/script&gt;" in page


def test_citation_et_explication_du_llm_echappees():
    hostile = [
        c.model_copy(update={"quote": f"Annexe : {SCRIPT} {IMAGE}"})
        if c.kind == "donnees_personnelles"
        else c
        for c in clauses(penalites_execution=ABSENT)
    ]
    service = memory_service(FixedExtractor(hostile), explainer=HostileExplainer())
    web = client(service)
    thread = analyse(web, HOSTILE)
    # l'explication hostile a bien été retenue, et la citation hostile vérifiée
    assert service.dossier(thread)["status"]["explanation"]["source"] == "llm"
    page = web.get(f"/contrats/{thread}").text
    assert SCRIPT not in page and IMAGE not in page and "<b>" not in page
    assert "&lt;b&gt;penalites_execution&lt;/b&gt;" in page


def test_nom_du_relecteur_echappe():
    web = client()
    thread = analyse(web, PENDING_TEXT)
    token = csrf(web, f"/contrats/{thread}")
    web.post(
        f"/contrats/{thread}/decision",
        data={"csrf": token, "decision": "GO", "relecteur": SCRIPT, "motif": IMAGE},
    )
    page = web.get(f"/contrats/{thread}").text
    assert SCRIPT not in page and IMAGE not in page


def test_surlignage_sur_du_texte_deja_echappe():
    from cdg.adapters.web.presentation import highlight

    marked = highlight("a < b\net <i>x</i> fin", [("k", "<i>x</i>")])
    assert str(marked) == (
        'a &lt; b\net <a class="citation" id="citation-k" href="#clause-k">'
        "<mark>&lt;i&gt;x&lt;/i&gt;</mark></a> fin"
    )
    # citation coupée par un retour à la ligne dans le contrat : retrouvée
    assert "<mark>" in str(highlight("un deux\ntrois", [("k", "deux trois")]))
    # introuvable : rien de surligné, rien d'inventé
    assert str(highlight("texte", [("k", "absent")])) == "texte"


# --- 9. texte original jamais conservé ; taille et type vérifiés ----------------------------

PARTY = "Société Témoin Synthétique"


def test_texte_original_ni_dans_l_etat_ni_dans_les_journaux_ni_dans_la_page(caplog):
    caplog.set_level(logging.DEBUG)
    service = memory_service()
    web = client(service)
    thread = analyse(web, f"{CONTRACT_TEXT}\nFournisseur : {PARTY}.\n", parties=PARTY)
    assert PARTY not in service.engine.values(thread)["raw_text"]
    assert PARTY not in web.get(f"/contrats/{thread}").text
    assert PARTY not in caplog.text


def test_erreur_d_analyse_sans_renvoyer_le_texte():
    web = client()
    analyse(web, identifiant="c-double")
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={
            "csrf": token,
            "source": "texte",
            "texte": f"{CONTRACT_TEXT}\n{PARTY}\n",
            "identifiant": "c-double",
        },
    )
    assert response.status_code == 409
    assert "existe déjà" in response.text and PARTY not in response.text


def test_envoi_trop_gros_refuse_avant_lecture():
    web = client()
    token = csrf(web)
    too_big = "a" * (security.max_body_bytes(CONFIG) + 1)
    response = web.post(
        "/analyse", data={"csrf": token, "source": "texte", "texte": too_big}
    )
    assert response.status_code == 413


def test_limite_sous_le_seuil_d_ecriture_sur_disque_des_envois():
    assert security.max_body_bytes(CONFIG) < security.SPOOL_BYTES
    huge = CONFIG.model_copy(
        update={"input": CONFIG.input.model_copy(update={"max_chars": 10_000_000})}
    )
    with pytest.raises(security.WebConfigError, match="disque"):
        create_app(memory_service(config=huge))


@pytest.mark.parametrize(
    ("content", "kind", "message"),
    [
        (b"\xff\xfe binaire", "text/plain", "UTF-8"),
        (CONTRACT_TEXT.encode() + b"\x00", "text/plain", "caractère de contrôle"),
        (CONTRACT_TEXT.encode(), "application/pdf", "texte brut"),
    ],
)
def test_fichier_qui_n_est_pas_du_texte_refuse(content, kind, message):
    web = client()
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={"csrf": token, "source": "fichier"},
        files={"fichier": ("contrat.txt", content, kind)},
    )
    assert response.status_code == 400
    assert message in response.text


def test_fichier_texte_accepte():
    web = client()
    token = csrf(web)
    response = web.post(
        "/analyse",
        data={"csrf": token, "source": "fichier", "identifiant": "c-fichier"},
        files={"fichier": ("contrat.txt", CONTRACT_TEXT.encode(), "text/plain")},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/contrats/c-fichier"


def test_cle_d_api_absente_message_clair():
    def missing():
        raise SettingsError(
            "variable d'environnement absente ou vide : MISTRAL_API_KEY"
        )

    web = client(memory_service(run=missing))
    token = csrf(web)
    response = web.post(
        "/analyse", data={"csrf": token, "source": "texte", "texte": CONTRACT_TEXT}
    )
    assert response.status_code == 503
    assert "MISTRAL_API_KEY" in response.text and "démonstration" in response.text


# --- 10. adresse d'un contrat : interne, un seul segment ------------------------------------
# alertes CodeQL py/url-redirection (27/09) : les redirections formaient l'adresse par une
# f-string, sans encoder l'identifiant ; les liens, par le filtre urlencode de Jinja, qui
# garde « / ». Une seule fonction forme désormais toutes les adresses d'un contrat.

HOSTILE_IDS = [
    "//evil.example",
    "\\\\evil.example",
    "/\\evil.example",
    "https://evil.example/x",
    "javascript:alert(1)",
    "a/b",
    "a?b=c",
    "a#b",
    "revue 1 é",
    "x\r\nLocation: https://evil.example",
    "..",
]


@pytest.mark.parametrize("thread_id", HOSTILE_IDS)
@pytest.mark.parametrize("action", ["", "decision", "rejeu"])
def test_adresse_d_un_contrat_reste_interne(thread_id, action):
    path = presentation.contract_path(thread_id, action)
    segment = path.removeprefix("/contrats/")
    if action:
        segment = segment.removesuffix(f"/{action}")
    assert path.startswith("/contrats/")
    assert not set(segment) & set("/\\?#: \r\n\t")  # rien ne sort du segment
    assert unquote(segment) == thread_id  # un seul segment, sans perte
    here = "http://127.0.0.1:8000/contrats/c-1"
    assert urlsplit(urljoin(here, path))[:2] == ("http", "127.0.0.1:8000")


def test_adresse_d_un_contrat_action_inconnue_refusee():
    with pytest.raises(ValueError, match="action inconnue"):
        presentation.contract_path("c-1", "suppression")


def test_redirection_apres_revue_vers_le_dossier_quel_que_soit_l_identifiant():
    thread = "revue 1#é"
    service = memory_service()
    service.analyse(PENDING_TEXT, contract_id=thread)
    web = client(service)
    data = {"decision": "GO_RESERVES", "relecteur": "Camille", "motif": "réserves"}
    response = web.post(
        presentation.contract_path(thread, "decision"),
        data={"csrf": csrf(web), **data},
    )
    assert response.status_code == 303
    assert response.headers["location"] == presentation.contract_path(thread)
    page = web.get(response.headers["location"])
    assert page.status_code == 200 and thread in page.text


def test_redirection_apres_analyse_vers_le_dossier():
    web = client()
    response = web.post(
        "/analyse",
        data={
            "csrf": csrf(web),
            "source": "texte",
            "texte": CONTRACT_TEXT,
            "identifiant": "c-2026.09_v1",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"] == presentation.contract_path("c-2026.09_v1")


def test_liens_des_pages_par_la_meme_adresse():
    service = memory_service()
    for thread in ("a/b", "revue 1#é"):
        service.analyse(PENDING_TEXT, contract_id=thread)
    web = client(service)
    listing = web.get("/").text
    for thread in ("a/b", "revue 1#é"):
        assert f'href="{presentation.contract_path(thread)}"' in listing
    dossier = web.get(presentation.contract_path("revue 1#é")).text
    for action in ("decision", "rejeu"):
        assert presentation.contract_path("revue 1#é", action) in dossier
    assert "|urlencode" not in "".join(p.read_text() for p in TEMPLATES.glob("*.html"))


# --- cas limites ------------------------------------------------------------------------


def test_envoi_sans_longueur_refuse():
    web = client()
    csrf(web)
    response = web.post("/analyse", content=iter([b"source=texte"]))
    assert response.status_code == 411


class Broken:
    """Service dont la liste échoue : erreur inattendue."""

    def __init__(self, service):
        self._service = service

    def __getattr__(self, name):
        return getattr(self._service, name)

    def contracts(self, pending_only=False):
        raise RuntimeError("détail qui ne doit pas sortir")


def test_erreur_inattendue_page_sobre_avec_en_tetes(caplog):
    from fastapi.testclient import TestClient
    from web_helpers import BASE_URL

    app = create_app(Broken(memory_service()))
    web = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    response = web.get("/")
    assert response.status_code == 500
    assert "RuntimeError" in response.text
    assert "détail qui ne doit pas sortir" not in response.text + caplog.text
    assert response.headers["content-security-policy"] == security.CSP


def test_surlignage_citation_vide_ou_chevauchante():
    from cdg.adapters.web.presentation import highlight

    assert str(highlight("un deux", [("a", "  ")])) == "un deux"
    marked = str(highlight("un deux trois", [("a", "un deux"), ("b", "deux trois")]))
    assert marked.count("<mark>") == 1 and 'id="citation-a"' in marked


def test_noms_d_hote():
    assert security.bind_warning("serveur.local", allow_non_local=True)
    assert security.allowed_hosts("serveur.local") == [
        "serveur.local",
        *security.LOOPBACK_NAMES,
    ]
    assert security.host_name("127.0.0.1:8000") == "127.0.0.1"
    assert security.host_name("[::1]:8000") == "::1"
    assert security.host_name("[mal forme") is None


def test_serveur_uvicorn_sans_en_tetes_de_mandataire(monkeypatch):
    from cdg.adapters.web import server

    seen = {}
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: seen.update(kw))
    server.serve(object(), "127.0.0.1", 8000)
    assert seen["proxy_headers"] is False and seen["server_header"] is False
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8000)

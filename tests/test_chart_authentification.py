"""Chart : authentification et entrée réseau (PR D1, ADR 005).

Sans authentification, aucune entrée : ni Ingress, ni Service de l'interface. Avec elle,
oauth2-proxy en conteneur annexe, seul chemin vers l'interface (qui n'écoute que sur
127.0.0.1 et vérifie le jeton d'identité de chaque requête), Ingress en TLS 1.2 au moins,
HSTS, HTTP redirigé vers HTTPS, taille maximale alignée sur celle de l'interface,
limitation de débit par adresse du client. Journal de connexion d'oauth2-proxy réduit au
sub ; secrets en fichiers."""

from urllib.parse import urlsplit

import pytest
from test_chart import chart_script, containers, env, named, of_kind, refused, render

from cdg.adapters.web import security
from cdg.domain.config import load_config

pytestmark = pytest.mark.chart

HOST = "cdg.example.org"
ISSUER = "https://idp.example.org"


@pytest.fixture(scope="module")
def auth() -> list[dict]:
    return render(*chart_script().VARIANTS["authentifie"])


def pod(docs: list[dict]) -> dict:
    return of_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]


def container(docs: list[dict], name: str) -> dict:
    [found] = [c for c in containers(pod(docs)) if c["name"] == name]
    return found


def flags(found: dict) -> dict[str, str]:
    return dict(arg.removeprefix("--").split("=", 1) for arg in found["args"])


# --- sans authentification, aucune entrée -------------------------------------------------


def test_sans_authentification_ni_ingress_ni_service_de_l_interface():
    docs = render()
    assert not of_kind(docs, "Ingress")
    assert [s["metadata"]["name"] for s in of_kind(docs, "Service")] == [
        "cdg-contract-decision-graph-sante"
    ]
    assert [c["name"] for c in pod(docs)["containers"]] == ["web"]


def test_entree_sans_authentification_refusee():
    message = refused("--set", "ingress.active=true", "--set", f"ingress.hote={HOST}")
    assert "authentification" in message


@pytest.mark.parametrize(
    "missing",
    ["authentification.emetteur", "authentification.clientId", "ingress.hote"],
)
def test_authentification_exige_ses_valeurs(missing):
    options = chart_script().VARIANTS["authentifie"]
    kept = []
    for i in range(0, len(options), 2):
        if not options[i + 1].startswith(f"{missing}="):
            kept += options[i : i + 2]
    assert missing.split(".")[-1] in refused(*kept)


def test_image_d_oauth2_proxy_par_empreinte_exigee():
    options = chart_script().VARIANTS["authentifie"]
    pairs = [options[i : i + 2] for i in range(0, len(options), 2)]
    kept = [
        word
        for pair in pairs
        if not pair[1].startswith("authentification.image.digest=")
        for word in pair
    ]
    assert "digest" in refused(*kept)


# --- oauth2-proxy, conteneur annexe -------------------------------------------------------


def test_oauth2_proxy_annexe_image_par_empreinte(auth):
    proxy = container(auth, "oauth2-proxy")
    assert proxy["image"] == (
        "ghcr.io/sifir-gun/contract-decision-graph/oauth2-proxy@"
        + chart_script().EXAMPLE_DIGEST
    )
    assert [p["containerPort"] for p in proxy["ports"]] == [4180]
    assert proxy["readinessProbe"]["httpGet"]["path"] == "/ping"


def test_oauth2_proxy_seul_chemin_jeton_transmis_en_tetes_du_client_retires(auth):
    args = flags(container(auth, "oauth2-proxy"))
    assert args["upstream"] == "http://127.0.0.1:8000/"
    assert args["http-address"] == "0.0.0.0:4180"
    assert args["pass-authorization-header"] == "true"  # jeton d'identité signé
    assert args["skip-auth-strip-headers"] == "true"  # ceux du client, retirés
    assert args["pass-host-header"] == "false"  # l'interface reste sur la boucle locale
    assert args["provider"] == "oidc" and args["oidc-issuer-url"] == ISSUER
    assert args["client-id"] == "cdg-interface"
    assert args["code-challenge-method"] == "S256"
    assert args["redirect-url"] == f"https://{HOST}/oauth2/callback"


def test_session_cookie_securise_et_durees_validees(auth):
    args = flags(container(auth, "oauth2-proxy"))
    assert (args["cookie-secure"], args["cookie-httponly"]) == ("true", "true")
    assert args["cookie-samesite"] == "lax"
    assert (args["cookie-expire"], args["cookie-refresh"]) == ("8h", "5m")
    assert "offline_access" in args["scope"].split()  # jeton de rafraîchissement
    assert "email" not in args["scope"].split()  # minimisation : ni e-mail


def test_journal_de_connexion_reduit_au_sub(auth):
    args = flags(container(auth, "oauth2-proxy"))
    assert args["oidc-email-claim"] == "sub"
    assert args["request-logging"] == "false"
    fmt = args["auth-logging-format"]
    assert "{{.Username}}" in fmt and "{{.Message}}" not in fmt
    assert "{{.Client}}" not in fmt


def test_secrets_d_oauth2_proxy_en_fichiers_jamais_en_variables(auth):
    proxy = container(auth, "oauth2-proxy")
    args = flags(proxy)
    assert args["client-secret-file"] == "/run/secrets/oauth2-proxy/client-secret"
    assert args["cookie-secret-file"] == "/run/secrets/oauth2-proxy/cookie-secret"
    assert all("valueFrom" not in e for e in env(proxy).values())
    [volume] = [v for v in pod(auth)["volumes"] if v["name"] == "secrets-oauth2-proxy"]
    assert volume["projected"]["defaultMode"] == 0o440
    web = container(auth, "web")
    assert "secrets-oauth2-proxy" not in [m["name"] for m in web["volumeMounts"]]


def test_oauth2_proxy_joint_le_fournisseur_par_le_proxy_de_sortie(auth):
    variables = env(container(auth, "oauth2-proxy"))
    assert variables["HTTPS_PROXY"]["value"] == "http://cdg-proxy:4750"
    assert set(variables["NO_PROXY"]["value"].split(",")) >= {"127.0.0.1", "localhost"}


# --- interface : jeton vérifié -------------------------------------------------------------


def test_interface_verifie_le_jeton_et_reste_sur_la_boucle_locale(auth):
    web = container(auth, "web")
    args = web["args"]
    start = args.index("web")
    options = args[start:]
    assert options[options.index("--identite") + 1] == "en-tetes"
    assert options[options.index("--oidc-emetteur") + 1] == ISSUER
    assert options[options.index("--oidc-audience") + 1] == "cdg-interface"
    assert options[options.index("--adresse-publique") + 1] == f"https://{HOST}"
    assert "--host" not in options  # 127.0.0.1, par défaut
    assert [p["name"] for p in web["ports"]] == ["sante"]
    assert (
        env(web)["HTTPS_PROXY"]["value"] == "http://cdg-proxy:4750"
    )  # clés du fournisseur


def test_service_de_l_interface_vise_oauth2_proxy(auth):
    service = named(auth, "Service", "cdg-contract-decision-graph")
    assert [(p["port"], p["targetPort"]) for p in service["spec"]["ports"]] == [
        (80, "http")
    ]


def test_deconnexion_chez_le_fournisseur_reglable():
    docs = render(
        *chart_script().VARIANTS["authentifie"],
        "--set",
        "authentification.deconnexionFournisseur=true",
    )
    assert "--deconnexion-fournisseur" in container(docs, "web")["args"]
    assert flags(container(docs, "oauth2-proxy"))["whitelist-domain"] == (
        urlsplit(ISSUER).hostname
    )
    assert "whitelist-domain" not in flags(
        container(render(*chart_script().VARIANTS["authentifie"]), "oauth2-proxy")
    )


def test_autorites_du_fournisseur_montees_dans_les_deux_conteneurs():
    docs = render(
        *chart_script().VARIANTS["authentifie"],
        "--set",
        "authentification.autorites=cdg-autorites",
    )
    [volume] = [v for v in pod(docs)["volumes"] if v["name"] == "autorites"]
    assert volume["configMap"]["name"] == "cdg-autorites"
    web, proxy = container(docs, "web"), container(docs, "oauth2-proxy")
    assert flags(proxy)["provider-ca-file"] == "/run/autorites/ca.crt"
    assert web["args"][web["args"].index("--oidc-ca") + 1] == "/run/autorites/ca.crt"
    for found in (web, proxy):
        [mount] = [m for m in found["volumeMounts"] if m["name"] == "autorites"]
        assert mount["readOnly"] is True


# --- entrée : TLS, HSTS, redirection, taille, débit ----------------------------------------


def test_ingress_en_tls_avec_ses_middlewares(auth):
    ingress = named(auth, "Ingress", "cdg-contract-decision-graph")
    notes = ingress["metadata"]["annotations"]
    assert notes["traefik.ingress.kubernetes.io/router.entrypoints"] == "websecure"
    assert notes["traefik.ingress.kubernetes.io/router.tls"] == "true"
    assert notes["traefik.ingress.kubernetes.io/router.tls.options"] == (
        "cdg-cdg-contract-decision-graph-tls@kubernetescrd"
    )
    assert notes["traefik.ingress.kubernetes.io/router.middlewares"].split(",") == [
        f"cdg-cdg-contract-decision-graph-{name}@kubernetescrd"
        for name in ("securite", "taille", "debit")
    ]
    [tls] = ingress["spec"]["tls"]
    assert tls["hosts"] == [HOST]
    [rule] = ingress["spec"]["rules"]
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert (rule["host"], backend["name"], backend["port"]["number"]) == (
        HOST,
        "cdg-contract-decision-graph",
        80,
    )


def test_tls_1_2_au_moins(auth):
    option = named(auth, "TLSOption", "-tls")
    assert option["apiVersion"] == "traefik.io/v1alpha1"
    assert option["spec"]["minVersion"] == "VersionTLS12"
    assert option["spec"]["sniStrict"] is True


def test_hsts(auth):
    headers = named(auth, "Middleware", "-securite")["spec"]["headers"]
    assert headers["stsSeconds"] == 31536000


def test_http_redirige_vers_https(auth):
    ingress = named(auth, "Ingress", "-http")
    notes = ingress["metadata"]["annotations"]
    assert notes["traefik.ingress.kubernetes.io/router.entrypoints"] == "web"
    assert notes["traefik.ingress.kubernetes.io/router.middlewares"] == (
        "cdg-cdg-contract-decision-graph-https@kubernetescrd"
    )
    assert "tls" not in ingress["spec"]
    redirect = named(auth, "Middleware", "-https")["spec"]["redirectScheme"]
    assert redirect == {"scheme": "https", "permanent": True}


def test_taille_maximale_alignee_sur_l_interface(auth):
    buffering = named(auth, "Middleware", "-taille")["spec"]["buffering"]
    assert buffering["maxRequestBodyBytes"] == security.max_body_bytes(load_config())


def test_debit_limite_par_adresse_du_client(auth):
    limit = named(auth, "Middleware", "-debit")["spec"]["rateLimit"]
    assert (limit["average"], limit["burst"], limit["period"]) == (20, 40, "1s")
    # l'adresse de la connexion, telle que Traefik la voit (préservée, ADR 005)
    assert limit["sourceCriterion"] == {"ipStrategy": {"depth": 0}}


def test_entree_de_l_interface_depuis_traefik_seulement(auth):
    policy = named(auth, "NetworkPolicy", "-web")
    rules = policy["spec"]["ingress"]
    [traefik] = [
        r for r in rules if r["ports"] == [{"port": "http", "protocol": "TCP"}]
    ]
    [source] = traefik["from"]
    assert source["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "traefik"
    }
    assert source["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "traefik"}

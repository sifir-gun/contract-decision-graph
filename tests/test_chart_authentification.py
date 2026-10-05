"""Chart : authentification et entrée réseau (PR D1, ADR 005).

Sans authentification, aucune entrée : ni Ingress, ni Service de l'interface. Avec elle,
oauth2-proxy en conteneur annexe, seul chemin vers l'interface (qui n'écoute que sur
127.0.0.1 et vérifie le jeton d'identité de chaque requête), Ingress en TLS 1.2 au moins,
HSTS, HTTP redirigé vers HTTPS, taille maximale alignée sur celle de l'interface,
limitation de débit par adresse du client. Journal de connexion d'oauth2-proxy réduit au
sub ; secrets en fichiers."""

import subprocess
from urllib.parse import urlsplit

import pytest
from test_chart import (
    CHART,
    chart_script,
    containers,
    env,
    named,
    of_kind,
    refused,
    render,
)

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


# --- authentification obligatoire : jamais d'interface locale dans le cluster --------------


def test_authentification_obligatoire_jamais_d_interface_locale_dans_le_cluster():
    """PR D2 : le canal locale (interface non authentifiée) n'existe pas dans le cluster ;
    l'interface y refuse aussi de démarrer sans --identite en-tetes."""
    auth = chart_script().AUTH
    message = refused(*auth, "--set", "authentification.active=false")
    assert "authentification" in message and "locale" in message


@pytest.mark.parametrize("variant", ["reel", "demo", "copie", "authentifie"])
def test_interface_toujours_derriere_oauth2_proxy(variant):
    args = container(render(*chart_script().VARIANTS[variant]), "web")["args"]
    assert args[args.index("--identite") + 1] == "en-tetes"


def test_sans_entree_ni_ingress():
    assert not of_kind(render(), "Ingress")


def test_entree_sans_authentification_refusee():
    auth = chart_script().AUTH
    options = ["--set", "ingress.active=true", "--set", f"ingress.hote={HOST}"]
    message = refused(*auth, "--set", "authentification.active=false", *options)
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


# index publié par la CI à la fusion de la D1 (sha-e338f31), signature cosign et
# provenance vérifiées le 30/09/2026 (docs/exploitation.md, « Image publiée »)
OAUTH2_PROXY_INDEX = (
    "sha256:1ea518c8f7b97699d64fec957921ae83de5bc1f75008c8b9bf66ebee8db87795"
)


def test_image_d_oauth2_proxy_par_defaut_l_index_publie():
    """Installation sans option d'image : l'index publié et vérifié."""
    assert not any("image.digest" in word for word in chart_script().AUTH)
    proxy = container(render(), "oauth2-proxy")
    assert proxy["image"] == (
        "ghcr.io/sifir-gun/contract-decision-graph/oauth2-proxy@" + OAUTH2_PROXY_INDEX
    )


def test_image_d_oauth2_proxy_jamais_par_etiquette():
    auth = chart_script().AUTH
    message = refused(*auth, "--set", "authentification.image.digest=v7.15.4")
    assert "digest" in message


# --- oauth2-proxy, conteneur annexe -------------------------------------------------------


def test_oauth2_proxy_annexe_image_par_empreinte(auth):
    proxy = container(auth, "oauth2-proxy")
    assert proxy["image"].endswith("@" + OAUTH2_PROXY_INDEX)
    assert [p["containerPort"] for p in proxy["ports"]] == [4180]
    assert proxy["readinessProbe"]["httpGet"]["path"] == "/ping"


def test_oauth2_proxy_conteneur_annexe_natif(auth):
    """Conteneur d'initialisation redémarré (restartPolicy: Always, stable depuis
    Kubernetes 1.33) : l'interface ne démarre qu'une fois oauth2-proxy prêt (sonde de
    démarrage), et le kubelet ne l'arrête qu'après elle ; aucune pause choisie à la
    main pour ordonner l'arrêt."""
    spec = pod(auth)
    assert [c["name"] for c in spec["containers"]] == ["web"]
    [proxy] = [c for c in spec["initContainers"] if c["name"] == "oauth2-proxy"]
    assert proxy["restartPolicy"] == "Always"
    assert proxy["startupProbe"]["httpGet"] == {"path": "/ping", "port": "http"}
    assert "lifecycle" not in proxy


def test_oauth2_proxy_demarre_juste_avant_l_interface():
    """Après les conteneurs d'initialisation ordinaires (copie du modèle) : il démarre
    au plus près de l'interface."""
    docs = render(
        *chart_script().VARIANTS["authentifie"], "--set", "modele.montage=copie"
    )
    names = [c["name"] for c in pod(docs)["initContainers"]]
    assert names[-1] == "oauth2-proxy" and len(names) > 1


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


def test_ecran_d_accord_non_force_par_defaut(auth):
    """oauth2-proxy 7.15.4 envoie approval_prompt=force si rien n'est réglé
    (legacy_options.go) : le chart fixe « auto », qui ne force pas l'écran d'accord à
    chaque connexion ; pas de paramètre OIDC prompt, dont aucune valeur n'est neutre."""
    args = flags(container(auth, "oauth2-proxy"))
    assert args["approval-prompt"] == "auto"
    assert "prompt" not in args


def test_ecran_d_accord_reglable():
    docs = render(
        *chart_script().VARIANTS["authentifie"],
        "--set",
        "authentification.accord=force",
    )
    assert flags(container(docs, "oauth2-proxy"))["approval-prompt"] == "force"


def test_ecran_d_accord_valeur_inconnue_refusee():
    options = [*chart_script().VARIANTS["authentifie"]]
    message = refused(*options, "--set", "authentification.accord=toujours")
    assert "accord" in message


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


def test_cles_csrf_partagees_montees_dans_l_interface_seulement(auth):
    """Deux réplicas derrière Traefik : un formulaire servi par l'un est envoyé à
    l'autre. Clés CSRF partagées (Secret), tout le Secret monté (courante, et precedente
    pendant une rotation), mis à jour sans redémarrage ; jamais dans oauth2-proxy."""
    web, proxy = container(auth, "web"), container(auth, "oauth2-proxy")
    args = web["args"]
    assert args[args.index("--cles-csrf") + 1] == "/run/secrets/csrf"
    [volume] = [v for v in pod(auth)["volumes"] if v["name"] == "cles-csrf"]
    assert volume["projected"]["defaultMode"] == 0o440
    [source] = volume["projected"]["sources"]
    assert source["secret"] == {"name": "cdg-csrf"}  # toutes ses clés, sans liste
    [mount] = [m for m in web["volumeMounts"] if m["name"] == "cles-csrf"]
    assert (mount["mountPath"], mount["readOnly"]) == ("/run/secrets/csrf", True)
    assert "subPath" not in mount  # sinon, aucune mise à jour du fichier
    assert "cles-csrf" not in [m["name"] for m in proxy["volumeMounts"]]


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
    # port par son numéro : kube-router (règles réseau de k3s) ne lit les ports
    # nommés que dans les conteneurs ordinaires, pas dans un conteneur annexe natif
    [traefik] = [r for r in rules if r["ports"] == [{"port": 4180, "protocol": "TCP"}]]
    [source] = traefik["from"]
    assert source["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "traefik"
    }
    assert source["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "traefik"}


def test_port_de_sante_jamais_publie(auth):
    """Les réponses de santé nomment le pod (décision du 05/10) : leur port reste interne,
    service ClusterIP à part, aucune entrée vers lui, ouvert aux seuls pods de test."""
    sante = named(auth, "Service", "-sante")
    assert sante["spec"]["type"] == "ClusterIP"
    for ingress in of_kind(auth, "Ingress"):
        for rule in ingress["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                backend = path["backend"]["service"]
                assert backend["name"] != sante["metadata"]["name"]
    for service in of_kind(auth, "Service"):
        if service is not sante:
            for port in service["spec"]["ports"]:
                assert port["targetPort"] not in ("sante", 8081), service
    policy = named(auth, "NetworkPolicy", "-web")
    for rule in policy["spec"]["ingress"]:
        if any(port["port"] in ("sante", 8081) for port in rule["ports"]):
            [source] = rule["from"]
            assert set(source) == {"podSelector"}  # dans l'espace de noms seulement
            labels = source["podSelector"]["matchLabels"]
            assert labels["app.kubernetes.io/component"] == "test"


# --- rôles et second facteur (PR D2) -------------------------------------------------------


def test_roles_tires_des_groupes_transmis_a_l_interface(auth):
    args = container(auth, "web")["args"]
    assert args[args.index("--groupes-analyste") + 1] == "cdg-analystes"
    assert args[args.index("--groupes-relecteur") + 1] == "cdg-relecteurs"


def test_roles_reglables_et_jamais_vides():
    docs = render(
        "--set-json",
        'authentification.roles.relecteur=["achats-relecteurs","direction"]',
    )
    args = container(docs, "web")["args"]
    assert args[args.index("--groupes-relecteur") + 1] == "achats-relecteurs,direction"
    auth = chart_script().AUTH
    message = refused(*auth, "--set-json", "authentification.roles.analyste=[]")
    assert "analyste" in message


def test_second_facteur_non_exige_par_defaut(auth):
    args = container(auth, "web")["args"]
    assert "--second-facteur-amr" not in args and "--second-facteur-acr" not in args


def test_second_facteur_transmis_quand_exige():
    docs = render(
        "--set-json",
        'authentification.secondFacteur.amr=["mfa","otp"]',
        "--set-json",
        'authentification.secondFacteur.acr=["urn:exemple:aal2"]',
    )
    args = container(docs, "web")["args"]
    assert args[args.index("--second-facteur-amr") + 1] == "mfa,otp"
    assert args[args.index("--second-facteur-acr") + 1] == "urn:exemple:aal2"


def install_notes(*options: str) -> str:
    """Notes d'installation, rendues sans cluster (helm install --dry-run=client)."""
    result = subprocess.run(
        ["helm", "install", "cdg", str(CHART), "--namespace", "cdg"]
        + ["--dry-run=client", *chart_script().AUTH, *options],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.split("NOTES:", 1)[1]


def test_notes_d_installation_avertissent_sans_second_facteur():
    assert "second facteur non exigé" in install_notes()
    exige = install_notes("--set-json", 'authentification.secondFacteur.amr=["mfa"]')
    assert "second facteur non exigé" not in exige

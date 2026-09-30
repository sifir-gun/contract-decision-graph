"""Scénarios sur le cluster de test k3d (ADR 005, PR C3 et D1), préparé par
scripts/cluster.py : plusieurs nœuds, CloudNativePG avec sauvegardes vers SeaweedFS, proxy
de sortie, serveur factice de Mistral, Traefik, Dex, application derrière oauth2-proxy.
Marqueur `cluster`, option `--cluster=DOSSIER`, en un mot (voir
tests/test_cluster_outillage.py).

Les analyses passent par l'interface (redirection de port vers un pod), comme pour un
utilisateur : la CLI, lancée dans un pod, chargerait une seconde fois le modèle (ADR 005,
mesure). La CLI ne sert qu'aux commandes sans modèle (list, journal, verify, resume).
Chaque requête porte un jeton d'identité de Dex, vérifié par l'interface (PR D1). Les
scénarios de l'entrée passent par Traefik, depuis deux pods clients, comme un navigateur.
Les scénarios s'enchaînent sur le même cluster, dans l'ordre du fichier.
"""

import base64
import contextlib
import hashlib
import importlib.util
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import jwt
import pytest
import yaml

pytestmark = pytest.mark.cluster

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'name="csrf" value="([0-9a-f]{64})"')
GO = "demo-01-go-maintenance"  # sans tentative d'instruction : scellé sans revue
PIEGE = "demo-11-piege-injection"  # tentative d'instruction : revue humaine imposée
WEB = "app.kubernetes.io/instance=cdg,app.kubernetes.io/component=web"
# corpus inchangé (dans l'image) : une mise à jour des scénarios ne le réindexe pas (tâche
# d'ingestion : plus de 10 minutes à chaque fois, ADR 005) ; --reuse-values ne suffit pas
# après un retour arrière, qui reprend les valeurs de la révision visée
SANS_INGESTION = ("--set", "taches.ingestion.active=false")
PUBLIC = "https://cdg.test"  # adresse publique de l'interface (PR D1)


def _cluster_module():
    spec = importlib.util.spec_from_file_location(
        "cluster", ROOT / "scripts" / "cluster.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLUSTER = _cluster_module()
KUBECTL = [
    "kubectl",
    "--kubeconfig",
    str(CLUSTER.KUBECONFIG),
    "--context",
    CLUSTER.CONTEXT,
]


@pytest.fixture(scope="module")
def images(request) -> dict[str, dict[str, str]]:
    folder = Path(request.config.getoption("--cluster"))
    return json.loads((folder / "images.json").read_text(encoding="utf-8"))


def kubectl(*args: str, stdin: str | None = None, timeout: float = 120) -> str:
    result = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(CLUSTER.KUBECONFIG),
            "--context",
            CLUSTER.CONTEXT,
        ]
        + list(args),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, f"kubectl {' '.join(args)} :\n{result.stderr}"
    return result.stdout


def helm(*args: str, timeout: float = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "helm",
            "--kubeconfig",
            str(CLUSTER.KUBECONFIG),
            "--kube-context",
            CLUSTER.CONTEXT,
        ]
        + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def web_pods() -> list[dict]:
    """Pods de l'interface prêts, hors arrêt."""
    pods = json.loads(kubectl("get", "pods", "-n", "cdg", "-l", WEB, "-o", "json"))
    return [
        p
        for p in pods["items"]
        if not p["metadata"].get("deletionTimestamp")
        and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p["status"].get("conditions", [])
        )
    ]


def wait_for(condition, what: str, timeout: float = 600, pause: float = 3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(pause)
    pytest.fail(f"délai dépassé : {what}")


def cli(pod: str, *args: str) -> tuple[int, dict]:
    """Commande de la CLI sans modèle, dans un pod de l'interface : code et résultat."""
    result = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(CLUSTER.KUBECONFIG),
            "--context",
            CLUSTER.CONTEXT,
        ]
        + ["exec", "-n", "cdg", pod, "-c", "web", "--"]
        + ["python", "-m", "cdg.cli", "--journaux", "json", *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode == 0:
        return 0, json.loads(lines[-1])
    return result.returncode, json.loads(result.stderr.strip().splitlines()[-1])


def factice(body: dict | None = None) -> dict:
    """État du serveur factice (délai, requêtes reçues, appels), ou son délai réglé."""
    method = "POST" if body is not None else "GET"
    code = (
        "import json, sys, urllib.request\n"
        "data = sys.argv[2].encode() if sys.argv[1] == 'POST' else None\n"
        "request = urllib.request.Request('http://127.0.0.1:8080/controle', data=data,"
        " method=sys.argv[1], headers={'content-type': 'application/json'})\n"
        "print(urllib.request.urlopen(request, timeout=10).read().decode())\n"
    )
    out = kubectl(
        "exec",
        "-n",
        "cdg-tests",
        "deployment/mistral-factice",
        "--",
        "python",
        "-c",
        code,
        method,
        json.dumps(body or {}),
    )
    return json.loads(out)


@contextlib.contextmanager
def forward(namespace: str, target: str, port: int) -> Iterator[str]:
    """Adresse locale d'une redirection de port vers un pod ou un service."""
    process = subprocess.Popen(
        KUBECTL + ["port-forward", "-n", namespace, target, f"0:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        match = re.search(r"127\.0\.0\.1:(\d+)", line)
        assert match, f"redirection de port impossible : {line}"
        yield f"http://127.0.0.1:{match[1]}"
    finally:
        process.terminate()
        process.wait(10)


# jetons d'identité de Dex, par utilisateur : échéance locale et jeton ; tous ceux délivrés
# pendant les scénarios, cherchés ensuite dans les journaux
TOKENS: dict[str, tuple[float, str]] = {}
ISSUED: set[str] = set()


def client_secret() -> str:
    encoded = kubectl(
        "get", "secret", "cdg-oidc", "-n", "cdg", "-o", "jsonpath={.data.client-secret}"
    )
    return base64.b64decode(encoded).decode()


def id_token(user: str = "analyste") -> str:
    """Jeton d'identité signé par Dex (octroi par mot de passe, réservé aux tests), repris
    jusqu'à mi-vie : Dex le délivre pour 10 minutes."""
    now = time.monotonic()
    cached = TOKENS.get(user)
    if cached and cached[0] > now:
        return cached[1]
    with forward("cdg-tests", "svc/dex", 5556) as base:
        response = httpx.post(
            f"{base}/token",
            data={
                "grant_type": "password",
                "username": f"{user}@example.org",
                "password": CLUSTER.TEST_USERS[user],
                "scope": "openid profile groups",
            },
            auth=("cdg-interface", client_secret()),
            timeout=30,
        )
    assert response.status_code == 200, response.text
    token = response.json()["id_token"]
    TOKENS[user] = (now + 300, token)
    ISSUED.add(token)
    return token


class Identity(httpx.Auth):
    """Jeton de Dex sur chaque requête, comme oauth2-proxy le transmet à l'interface."""

    def __init__(self, user: str = "analyste", token: str | None = None) -> None:
        self.user, self.token = user, token

    def auth_flow(self, request: httpx.Request):
        token = self.token or id_token(self.user)
        request.headers["authorization"] = f"Bearer {token}"
        yield request


def _keep_csrf(client: httpx.Client, response: httpx.Response) -> None:
    """Le cookie CSRF est Secure (interface derrière TLS) : la redirection de port est en
    HTTP, il est donc renvoyé à la main."""
    match = re.search(r"cdg_csrf=([^;,]+)", response.headers.get("set-cookie", ""))
    if match:
        client.headers["cookie"] = f"cdg_csrf={match[1]}"


@contextlib.contextmanager
def interface(pod: str, auth: httpx.Auth | None = None) -> Iterator[httpx.Client]:
    """Client HTTP de l'interface d'un pod, par redirection de port (elle n'écoute que sur
    127.0.0.1 dans le pod, ADR 004), avec le jeton d'identité et l'origine publique que
    lui transmettrait oauth2-proxy."""
    with (
        forward("cdg", f"pod/{pod}", 8000) as base,
        httpx.Client(
            base_url=base,
            timeout=600,
            headers={"origin": PUBLIC},
            auth=auth or Identity(),
        ) as client,
    ):
        client.event_hooks["response"] = [lambda r: _keep_csrf(client, r)]
        yield client


def analyse(client: httpx.Client, contract: str, identifier: str) -> httpx.Response:
    page = client.get("/analyse")
    assert page.status_code == 200, page.text
    token = TOKEN.search(page.text)
    assert token, "jeton CSRF absent"
    return client.post(
        "/analyse",
        data={
            "csrf": token[1],
            "source": "jeu",
            "contrat": contract,
            "identifiant": identifier,
        },
    )


def seal_if_pending(pod: str, thread: str) -> None:
    """Un contrat en attente de revue est tranché, pour être scellé."""
    code, listing = cli(pod, "list", "--en-attente")
    assert code == 0, listing
    if any(c["thread_id"] == thread for c in listing["contrats"]):
        code, out = cli(
            pod,
            "resume",
            thread,
            "--decision",
            "NO_GO",
            "--reviewer",
            "Camille",
            "--reason",
            "scénario du cluster",
        )
        assert code == 0, out


def sealed(pod: str, thread: str) -> int:
    code, journal = cli(pod, "journal")
    assert code == 0, journal
    return sum(1 for e in journal["enregistrements"] if e["thread_id"] == thread)


# --- 1. création concurrente d'un même contrat par deux réplicas --------------------------


def test_deux_replicas_sur_des_noeuds_differents():
    pods = wait_for(lambda: len(web_pods()) == 2 and web_pods(), "deux pods prêts")
    assert len({p["spec"]["nodeName"] for p in pods}) == 2


def test_creation_concurrente_d_un_meme_contrat_par_deux_replicas():
    """Le verrou consultatif de PostgreSQL tient entre réplicas : un seul thread, un seul
    scellement ; l'autre création est refusée explicitement."""
    first, second = (p["metadata"]["name"] for p in web_pods())
    thread = f"concurrent-{uuid.uuid4().hex[:8]}"
    factice({"delai": 5})
    results: dict[str, httpx.Response] = {}

    def submit(pod: str) -> None:
        with interface(pod) as client:
            results[pod] = analyse(client, GO, thread)

    workers = [threading.Thread(target=submit, args=(p,)) for p in (first, second)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(600)
    finally:
        factice({"delai": 0})
    codes = sorted(r.status_code for r in results.values())
    assert codes == [303, 409], {p: r.status_code for p, r in results.items()}
    _, listing = cli(first, "list")
    assert [c["thread_id"] for c in listing["contrats"]].count(thread) == 1
    seal_if_pending(first, thread)
    assert sealed(first, thread) == 1


# --- 2. arrêt d'un pod pendant une analyse ------------------------------------------------


def test_arret_propre_laisse_finir_l_analyse_en_cours():
    """Ordre d'arrêt pendant une analyse : la requête en cours finit (délai d'arrêt), le
    pod s'arrête ensuite."""
    [pod, *_] = (p["metadata"]["name"] for p in web_pods())
    thread = f"drain-{uuid.uuid4().hex[:8]}"
    before = factice()["recues"]
    factice({"delai": 15})
    result: dict[str, httpx.Response] = {}

    def submit() -> None:
        with interface(pod) as client:
            result["reponse"] = analyse(client, GO, thread)

    worker = threading.Thread(target=submit)
    worker.start()
    try:
        wait_for(lambda: factice()["recues"] > before, "analyse commencée", 120, 1)
        kubectl("delete", "pod", "-n", "cdg", pod, "--wait=false")
        worker.join(300)
    finally:
        factice({"delai": 0})
    assert result["reponse"].status_code == 303  # finie malgré l'ordre d'arrêt
    other = wait_for(
        lambda: [p for p in web_pods() if p["metadata"]["name"] != pod], "pod"
    )
    name = other[0]["metadata"]["name"]
    seal_if_pending(name, thread)
    assert sealed(name, thread) == 1


def test_pod_tue_pendant_une_analyse_reprise_par_l_autre_et_scellee_une_fois():
    """Pod tué sans délai (panne) : l'analyse, interrompue, est reprise depuis son dernier
    checkpoint par un autre réplica (reprise périodique), et scellée une seule fois."""
    wait_for(lambda: len(web_pods()) == 2, "deux pods prêts")
    victim, survivor = (p["metadata"]["name"] for p in web_pods())
    thread = f"panne-{uuid.uuid4().hex[:8]}"
    before = factice()["recues"]
    factice({"delai": 600})
    worker = threading.Thread(
        target=lambda: _submit_ignoring_errors(victim, thread), daemon=True
    )
    worker.start()
    try:
        wait_for(lambda: factice()["recues"] > before, "analyse commencée", 120, 1)
        kubectl("delete", "pod", "-n", "cdg", victim, "--grace-period=0", "--force")
    finally:
        factice({"delai": 0})

    def finished() -> bool:
        _, listing = cli(survivor, "list")
        states = {c["thread_id"]: c["etat"] for c in listing["contrats"]}
        return states.get(thread) in {"termine", "en_attente"}

    wait_for(finished, "analyse reprise par un autre réplica", 600, 5)
    seal_if_pending(survivor, thread)
    assert sealed(survivor, thread) == 1


def _submit_ignoring_errors(pod: str, thread: str) -> None:
    with contextlib.suppress(Exception), interface(pod) as client:
        analyse(client, GO, thread)


# --- 3. mise à jour sans interruption, 4. retour arrière ----------------------------------

PROBER = """
import json, time, urllib.request
url = "http://cdg-contract-decision-graph-sante:8081/sante/pret"
end, ok, ko = time.monotonic() + {duration}, 0, 0
while time.monotonic() < end:
    try:
        ok += urllib.request.urlopen(url, timeout=2).status == 200
    except Exception:
        ko += 1
    time.sleep(0.2)
print(json.dumps({{"ok": ok, "ko": ko}}))
"""


def _prober(images: dict, name: str, duration: int) -> None:
    factice_image = images["factice"]
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": "cdg",
            # la règle réseau des sondes n'admet que les pods du test Helm
            "labels": {
                "app.kubernetes.io/name": "contract-decision-graph",
                "app.kubernetes.io/instance": "cdg",
                "app.kubernetes.io/component": "test",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "sonde",
                    "image": f"{factice_image['repository']}@{factice_image['digest']}",
                    "command": ["python", "-c", PROBER.format(duration=duration)],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "128Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(pod))


def _deployment_args() -> list[str]:
    deployment = json.loads(
        kubectl(
            "get",
            "deployment",
            "-n",
            "cdg",
            "cdg-contract-decision-graph",
            "-o",
            "json",
        )
    )
    return deployment["spec"]["template"]["spec"]["containers"][0]["args"]


def test_mise_a_jour_sans_interruption(images):
    """Mise à jour progressive (un pod de plus, jamais un de moins, pause avant l'arrêt) :
    aucune requête perdue sur le service, pendant toute la mise à jour."""
    name, duration = f"sonde-{uuid.uuid4().hex[:6]}", 420
    _prober(images, name, duration)
    time.sleep(10)
    start = time.monotonic()
    upgraded = helm(
        "upgrade",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        "cdg",
        "--reuse-values",
        *SANS_INGESTION,
        "--set",
        "web.repriseIntervalle=16",
        "--wait",
        "--timeout",
        "900s",
    )
    assert upgraded.returncode == 0, upgraded.stderr
    # la sonde a couvert toute la mise à jour
    assert time.monotonic() - start < duration - 10, (
        "mise à jour plus longue que la sonde"
    )
    assert "16" in _deployment_args()
    wait_for(
        lambda: (
            json.loads(kubectl("get", "pod", "-n", "cdg", name, "-o", "json"))[
                "status"
            ]["phase"]
            in {"Succeeded", "Failed"}
        ),
        "fin de la sonde",
        600,
        5,
    )
    counts = json.loads(kubectl("logs", "-n", "cdg", name).strip().splitlines()[-1])
    kubectl("delete", "pod", "-n", "cdg", name, "--wait=false")
    assert counts["ok"] > 500 and counts["ko"] == 0, counts


def test_retour_arriere():
    history = json.loads(helm("history", "cdg", "-n", "cdg", "-o", "json").stdout)
    previous = [r for r in history if r["status"] == "superseded"][-1]["revision"]
    rolled = helm(
        "rollback", "cdg", str(previous), "-n", "cdg", "--wait", "--timeout", "900s"
    )
    assert rolled.returncode == 0, rolled.stderr
    args = _deployment_args()
    assert args[args.index("--reprise-intervalle") + 1] == "15"
    wait_for(lambda: len(web_pods()) == 2, "deux pods prêts après le retour arrière")
    code, listing = cli(web_pods()[0]["metadata"]["name"], "list")
    assert code == 0 and listing["contrats"]  # rien de perdu


# --- 5. blocage réseau --------------------------------------------------------------------

EGRESS = """
import socket, sys, urllib.request, urllib.error
kind = sys.argv[1]
try:
    if kind == "direct":
        socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    else:
        proxy = urllib.request.ProxyHandler({"https": "http://cdg-proxy:4750"})
        urllib.request.build_opener(proxy).open("https://example.com/", timeout=10)
    print("ouvert")
except (OSError, urllib.error.URLError) as exc:
    print("refuse", type(exc).__name__, exc)
"""


def test_sortie_directe_refusee_par_les_regles_reseau():
    pod = web_pods()[0]["metadata"]["name"]
    out = kubectl(
        "exec", "-n", "cdg", pod, "-c", "web", "--", "python", "-c", EGRESS, "direct"
    )
    assert out.startswith("refuse"), out


def test_domaine_hors_liste_refuse_par_le_proxy():
    pod = web_pods()[0]["metadata"]["name"]
    out = kubectl(
        "exec", "-n", "cdg", pod, "-c", "web", "--", "python", "-c", EGRESS, "proxy"
    )
    assert out.startswith("refuse") and "407" in out, out


# fournisseur d'identité de test, interne au cluster (Dex derrière Traefik), d'où sa plage
# privée et sa sortie interne ; en production, un fournisseur public, sans plage privée
IDENTITY_EGRESS = (
    "--set-json",
    'domainesAutorises=["api.mistral.ai","dex.cdg.test"]',
    "--set-json",
    f'plagesAutorisees=["{CLUSTER.SERVICE_CIDR}"]',
    "--set-json",
    "sortiesInternes="
    + json.dumps(
        [
            {
                "espaceDeNoms": "traefik",
                "selecteur": {"app.kubernetes.io/name": "traefik"},
                "port": 8443,
            }
        ]
    ),
)


def test_adresse_d_api_autre_que_mistral_bloquee_en_configuration_de_production(images):
    """Proxy aux valeurs de production (api.mistral.ai et le fournisseur d'identité,
    seulement) : l'analyse, pointée sur le serveur factice, échoue explicitement (rapport
    d'échec, ESCALADE, revue humaine) ; il ne reçoit aucune requête."""
    proxy = images["proxy"]
    chart = str(ROOT / "chart" / "cdg-proxy")
    production = helm(
        "upgrade",
        "cdg-proxy",
        chart,
        "--namespace",
        "cdg",
        "--set",
        f"image.repository={proxy['repository']}",
        "--set",
        f"image.digest={proxy['digest']}",
        *IDENTITY_EGRESS,
        "--wait",
        "--timeout",
        "300s",
    )
    assert production.returncode == 0, production.stderr
    try:
        before = factice()["recues"]
        pod, thread = (
            web_pods()[0]["metadata"]["name"],
            f"bloque-{uuid.uuid4().hex[:8]}",
        )
        with interface(pod) as client:
            assert analyse(client, GO, thread).status_code == 303  # vers le dossier
        assert factice()["recues"] == before
        code, dossier = cli(pod, "show", thread)
        assert code == 0, dossier
        status = dossier["status"]
        [failure] = status["failure_report"]["failures"]
        assert failure["node"] == "extract_clauses"
        assert "407" in failure["message"]
        assert "denied to host 'mistral-factice." in failure["message"]
        assert (status["proposed_decision"], dossier["etat"]) == (
            "ESCALADE",
            "en_attente",
        )
        seal_if_pending(pod, thread)
    finally:
        restored = helm(
            "upgrade",
            "cdg-proxy",
            chart,
            "--namespace",
            "cdg",
            "--values",
            str(ROOT / "cluster" / "valeurs-proxy.yaml"),
            "--set",
            f"image.repository={proxy['repository']}",
            "--set",
            f"image.digest={proxy['digest']}",
            "--wait",
            "--timeout",
            "300s",
        )
        assert restored.returncode == 0, restored.stderr


# --- 6. rotation du mot de passe d'app_role, absent de tous les journaux -----------------

APPLICATION_SECRET = "cdg-base-application"
FILE_DIGEST = (
    "import hashlib; print(hashlib.sha256(open('/run/secrets/cdg/APP_DB_PASSWORD', 'rb')"
    ".read().strip()).hexdigest())"
)
# depuis un pod de l'interface : la chaîne à jour (fichier monté) ouvre une session, et le
# mot de passe lu sur l'entrée standard (jamais en argument) est refusé ou accepté
AUTHENTICATION = """
import sys, psycopg
from psycopg.conninfo import make_conninfo
from cdg.adapters.postgres import conninfo
current = conninfo.app_conninfo()
psycopg.connect(current, connect_timeout=5).close()
other = make_conninfo(current, password=sys.stdin.read().strip())
try:
    psycopg.connect(other, connect_timeout=5).close()
    print("accepte")
except psycopg.OperationalError:
    print("refuse")
"""


# sorties des tâches, recopiées par Helm 4.3 : son journal standard passe par slog, qui les
# rend en texte, guillemets échappés (internal/logging/logging.go ; log/slog de Go)
TASK_OUTPUTS = (
    re.compile(r'setup_db\\?": \\?"ok'),  # migrations
    re.compile(r'a_trancher\\?": \[\]'),  # contrôle de configuration
)


def web_exec(pod: str, code: str, stdin: str | None = None):
    """Python dans le conteneur de l'interface ; code et sorties, sans exiger le succès."""
    return subprocess.run(
        KUBECTL
        + ["exec", "-i", "-n", "cdg", pod, "-c", "web", "--", "python", "-c", code],
        input=stdin or "",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def application_password() -> str:
    encoded = kubectl(
        "get",
        "secret",
        APPLICATION_SECRET,
        "-n",
        "cdg",
        "-o",
        "jsonpath={.data.password}",
    )
    return base64.b64decode(encoded).decode()


def container_logs(namespaces: set[str] | None = None) -> tuple[str, set[str]]:
    """Journaux de tous les conteneurs de tous les pods du cluster (ou de ces espaces de
    noms), conteneurs d'initialisation et exécutions précédentes compris ; et les pods lus.
    Un conteneur démarré dont le journal est illisible est une erreur, jamais un trou
    silencieux."""
    pods = json.loads(kubectl("get", "pods", "--all-namespaces", "-o", "json"))["items"]
    chunks, read = [], set()
    for pod in pods:
        meta, status = pod["metadata"], pod.get("status", {})
        if namespaces is not None and meta["namespace"] not in namespaces:
            continue
        states = {
            s["name"]: s
            for s in status.get("initContainerStatuses", [])
            + status.get("containerStatuses", [])
        }
        containers = pod["spec"].get("initContainers", []) + pod["spec"]["containers"]
        for container in containers:
            state = states.get(container["name"], {})
            runs = []
            if {"running", "terminated"} & set(state.get("state", {})):
                runs.append([])
            if state.get("restartCount"):
                runs.append(["--previous"])
            for extra in runs:
                result = subprocess.run(
                    KUBECTL
                    + ["logs", "-n", meta["namespace"], meta["name"]]
                    + ["-c", container["name"], *extra],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                where = f"{meta['namespace']}/{meta['name']}/{container['name']}"
                assert result.returncode == 0, f"journal illisible : {where}"
                chunks.append(result.stdout)
                read.add(f"{meta['namespace']}/{meta['name']}")
    return "\n".join(chunks), read


def assert_covered(read: set[str]) -> None:
    """L'opérateur, les instances PostgreSQL et l'application ont bien été lus."""
    names = {name.split("/", 1)[1] for name in read}
    assert any(n.startswith("cnpg-controller-manager-") for n in names), names
    assert any(n.startswith("cdg-postgres-") for n in names), names
    assert {p["metadata"]["name"] for p in web_pods()} <= names


def assert_absent(password: str, text: str) -> None:
    for form in (password, base64.b64encode(password.encode()).decode()):
        assert form not in text, "mot de passe d'app_role trouvé dans un journal"


def upgrade_without_changes() -> str:
    """Mise à jour aux mêmes valeurs : ses tâches tournent (migrations en superutilisateur,
    contrôle de configuration en app_role) et Helm recopie leurs journaux sur sa sortie
    (hook-output-log-policy) avant de les supprimer ; le Deployment ne change pas."""
    result = helm(
        "upgrade",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        "cdg",
        "--reuse-values",
        *SANS_INGESTION,
        "--set",
        "taches.controleConfiguration.passerOutre=false",
        "--wait",
        "--timeout",
        "600s",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert all(pattern.search(output) for pattern in TASK_OUTPUTS), output
    return output


def restarts() -> dict[str, int]:
    return {
        p["metadata"]["name"]: sum(
            s.get("restartCount", 0) for s in p["status"].get("containerStatuses", [])
        )
        for p in web_pods()
    }


def primary() -> str:
    return kubectl(
        "get",
        "cluster",
        "cdg-postgres",
        "-n",
        "cdg",
        "-o",
        "jsonpath={.status.currentPrimary}",
    ).strip()


def test_rotation_du_mot_de_passe_sans_redemarrage_ni_trace_dans_les_journaux():
    """Le mot de passe d'app_role tiré par l'installation, puis celui de la rotation,
    témoins, n'apparaissent dans aucun journal : opérateur, instances PostgreSQL,
    application, tâches (recopiées par Helm), avant et après la rotation. Après la
    rotation (Secret changé, appliqué par CloudNativePG ; fichier monté mis à jour par le
    kubelet), l'ancien mot de passe est refusé ; les connexions d'app_role sont coupées
    côté serveur, et chaque réplica en ouvre de nouvelles avec le nouveau mot de passe
    pour analyser et sceller un contrat, sans redémarrage."""
    first = application_password()
    before = upgrade_without_changes()
    logs, read = container_logs()
    assert_covered(read)
    assert_absent(first, logs + before)

    pods = restarts()
    assert len(pods) == 2, pods
    second = secrets.token_urlsafe(24)
    kubectl(
        "patch",
        "secret",
        APPLICATION_SECRET,
        "-n",
        "cdg",
        "--type",
        "merge",
        "--patch-file",
        "/dev/stdin",
        stdin=json.dumps({"stringData": {"password": second}}),
    )
    digest = hashlib.sha256(second.encode()).hexdigest()
    for pod in pods:
        wait_for(
            lambda pod=pod: web_exec(pod, FILE_DIGEST).stdout.strip() == digest,
            f"fichier du secret mis à jour dans {pod}",
            300,
            5,
        )

        def refused(pod=pod) -> bool:
            result = web_exec(pod, AUTHENTICATION, stdin=first)
            return result.returncode == 0 and result.stdout.strip() == "refuse"

        wait_for(refused, "nouveau mot de passe appliqué, ancien refusé", 300, 5)
    terminated = kubectl(
        "exec",
        "-n",
        "cdg",
        primary(),
        "-c",
        "postgres",
        "--",
        "psql",
        "-U",
        "postgres",
        "-d",
        "cdg",
        "-Atc",
        "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity"
        " WHERE usename = 'app_role'",
    )
    assert int(terminated.strip()) >= 2  # au moins une connexion par réplica
    for pod in pods:
        thread = f"rotation-{uuid.uuid4().hex[:8]}"
        with interface(pod) as client:
            assert analyse(client, GO, thread).status_code == 303
        seal_if_pending(pod, thread)
        assert sealed(pod, thread) == 1
    after = upgrade_without_changes()  # le contrôle se connecte avec le nouveau
    assert restarts() == pods  # mêmes pods, aucun redémarrage
    logs, read = container_logs()
    assert_covered(read)
    for password in (first, second):
        assert_absent(password, logs + before + after)


# --- 7. contrôle de configuration avant une mise à jour -----------------------------------


def test_mise_a_jour_refusee_tant_qu_un_contrat_attend_sous_l_ancienne_configuration(
    tmp_path,
):
    pod = web_pods()[0]["metadata"]["name"]
    thread = f"attente-{uuid.uuid4().hex[:8]}"
    with interface(pod) as client:
        assert analyse(client, PIEGE, thread).status_code == 303
    _, pending = cli(pod, "list", "--en-attente")
    assert thread in [c["thread_id"] for c in pending["contrats"]]
    decision = yaml.safe_load((ROOT / "config" / "decision.yaml").read_text())
    decision["min_margin"] = round(decision["min_margin"] + 0.01, 2)  # autre empreinte
    changed = tmp_path / "decision.yaml"
    changed.write_text(yaml.safe_dump(decision, allow_unicode=True, sort_keys=False))
    before = _deployment_args()
    refused = helm(
        "upgrade",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        "cdg",
        "--reuse-values",
        *SANS_INGESTION,
        "--set-file",
        f"configuration.decision={changed}",
        "--wait",
        "--timeout",
        "600s",
    )
    assert refused.returncode != 0
    assert "controle-configuration" in refused.stderr
    assert _deployment_args() == before  # rien n'a été déployé
    forced = helm(
        "upgrade",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        "cdg",
        "--reuse-values",
        *SANS_INGESTION,
        "--set-file",
        f"configuration.decision={changed}",
        "--set",
        "taches.controleConfiguration.passerOutre=true",
        "--wait",
        "--timeout",
        "900s",
    )
    assert forced.returncode == 0, forced.stderr


# --- 8. authentification et entrée (PR D1) ------------------------------------------------

CLIENTS = ("client-a", "client-b")
# navigateur minimal, dans un pod client : Traefik, oauth2-proxy, Dex, comme un utilisateur ;
# mot de passe lu sur l'entrée standard, jamais en argument ; ni cookie ni jeton imprimés
BROWSER = r"""
import html, json, re, socket, ssl, sys, time, urllib.parse
import httpx

CA, PUBLIC, DEX = "/run/autorites/ca.crt", "https://cdg.test", "dex.cdg.test"
FORM = re.compile(r'<form[^>]*action="([^"]+)"')
CSRF = re.compile(r'name="csrf" value="([0-9a-f]{64})"')
NEXT = re.compile(r'<a id="suite" href="([^"]+)"')
USER = re.compile(r'class="utilisateur">([^<]*)<')
REQUEST = re.compile(r'name="req" value="([^"]+)"')
SHOWN = {"accord": False}  # écran d'accord de Dex affiché pendant la connexion
SESSION = re.compile(r"^_cdg_session(_\d+)?$")


def browser():
    return httpx.Client(verify=CA, follow_redirects=True, timeout=30)


def state(response):
    shown = USER.search(response.text)
    return {
        "hote": response.url.host,
        "statut": response.status_code,
        "utilisateur": html.unescape(shown[1]) if shown else None,
    }


def login(client, user, password):
    page = client.get(PUBLIC + "/")
    form = FORM.search(page.text)
    if page.url.host != DEX or not form:
        raise SystemExit(f"formulaire de Dex absent : {page.status_code} {page.url.host}")
    target = urllib.parse.urljoin(str(page.url), html.unescape(form[1]))
    data = {"login": f"{user}@example.org", "password": password}
    done = client.post(target, data=data)
    # filet de sécurité : le chart règle approval_prompt=auto, mais un écran d'accord
    # affiché quand même (Dex honore « force ») est accordé, comme par un utilisateur,
    # et signalé au scénario de connexion
    if done.url.host == DEX and done.url.path == "/approval":
        SHOWN["accord"] = True
        request = REQUEST.search(done.text)
        if not request:
            raise SystemExit("écran d'accord de Dex sans demande")
        grant = {"req": html.unescape(request[1]), "approval": "approve"}
        done = client.post(str(done.url), data=grant)
    if done.url.host == DEX:
        raise SystemExit(f"connexion restée chez Dex : {done.status_code} {done.url.path}")
    return done


def session_attributes(response):
    found = []
    for step in [*response.history, response]:
        for header in step.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if SESSION.match(name):
                found.append(rest.partition(";")[2].strip())
    return found


def session_cookies(client):
    return {c.name: c.value for c in client.cookies.jar if SESSION.match(c.name)}


action = sys.argv[1]
if action == "connexion":
    client = browser()
    done = login(client, sys.argv[2], sys.stdin.read().strip())
    cookie = session_attributes(done)
    print(json.dumps({**state(done), "cookie": cookie, **SHOWN}))
elif action == "forge":
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    subject, kid = sys.argv[2], sys.argv[3]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {
        "iss": "https://dex.cdg.test", "aud": "cdg-interface", "sub": subject,
        "iat": now, "exp": now + 600, "name": "relecteur", "groups": ["cdg-relecteurs"],
    }
    forged = jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})
    headers = {
        "authorization": f"Bearer {forged}",
        "x-forwarded-user": "relecteur",
        "x-forwarded-email": "relecteur@example.org",
        "x-forwarded-preferred-username": "relecteur",
        "x-forwarded-groups": "cdg-relecteurs",
        "x-forwarded-access-token": forged,
        "x-auth-request-user": "relecteur",
    }
    anonymous = browser().get(PUBLIC + "/", headers=headers)
    client = browser()
    login(client, "analyste", sys.stdin.read().strip())
    connected = client.get(PUBLIC + "/", headers=headers)
    print(json.dumps({"anonyme": state(anonymous), "connecte": state(connected)}))
elif action == "direct":
    opened = {}
    for port in (4180, 8000):
        try:
            socket.create_connection((sys.argv[2], port), timeout=5).close()
            opened[port] = "ouvert"
        except OSError as exc:
            opened[port] = f"refuse {type(exc).__name__}"
    print(json.dumps(opened))
elif action == "entree":
    plain = httpx.get("http://cdg.test/analyse", timeout=10)
    secure = httpx.get(PUBLIC + "/ping", verify=CA, timeout=10)
    size = int(sys.argv[2])
    big = httpx.post(
        PUBLIC + "/analyse", content=b"x" * size, verify=CA, timeout=60,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    print(json.dumps({
        "http": [plain.status_code, plain.headers.get("location")],
        "hsts": secure.headers.get("strict-transport-security"),
        "taille": big.status_code,
    }))
elif action == "tls":
    def handshake(version):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(CA)
        context.set_ciphers("DEFAULT:@SECLEVEL=0")  # TLS 1.1 proposé par le client
        context.minimum_version = context.maximum_version = version
        try:
            with socket.create_connection(("cdg.test", 443), timeout=10) as raw:
                with context.wrap_socket(raw, server_hostname="cdg.test") as tls:
                    return tls.version()
        except ssl.SSLError as exc:
            return f"refuse {exc.reason}"
    versions = (ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3)
    print(json.dumps({v.name: handshake(v) for v in versions}))
elif action == "rafale":
    with httpx.Client(verify=CA, timeout=10) as client:
        codes = [client.get(PUBLIC + "/ping").status_code for _ in range(int(sys.argv[2]))]
    print(json.dumps({"codes": codes}))
elif action == "deconnexion":
    client = browser()
    login(client, "analyste", sys.stdin.read().strip())
    page = client.get(PUBLIC + "/")
    token = CSRF.search(page.text)[1]
    transition = client.post(
        PUBLIC + "/deconnexion", data={"csrf": token}, headers={"origin": PUBLIC}
    )
    target = urllib.parse.urljoin(PUBLIC, html.unescape(NEXT.search(transition.text)[1]))
    after = client.get(target)
    again = client.get(PUBLIC + "/")
    print(json.dumps({
        "avant": state(page), "transition": transition.status_code,
        "apres": state(after), "ensuite": state(again),
        "session": sorted(session_cookies(client)),
    }))
elif action == "expiration":
    client = browser()
    done = login(client, "analyste", sys.stdin.read().strip())
    kept = session_cookies(client)
    time.sleep(float(sys.argv[2]))
    replay = browser()  # l'ancien cookie, rejoué tel quel : sans navigateur qui l'écarte
    for name, value in kept.items():
        replay.cookies.set(name, value, domain="cdg.test")
    stale = replay.get(PUBLIC + "/")
    print(json.dumps({"avant": state(done), "apres": state(stale), "cookies": len(kept)}))
"""


def _client_pod(name: str, image: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": "cdg-tests",
            "labels": {"app": "client-test"},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "client",
                    "image": image,
                    "command": ["python", "-c", "import time; time.sleep(10800)"],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": "autorites", "mountPath": "/run/autorites"}
                    ],
                }
            ],
            "volumes": [{"name": "autorites", "configMap": {"name": "cdg-autorites"}}],
        },
    }


@pytest.fixture(scope="module")
def clients(images) -> Iterator[dict[str, str]]:
    """Deux pods clients dans cdg-tests, l'autorité de test montée ; leur adresse."""
    image = f"{images['factice']['repository']}@{images['factice']['digest']}"
    for name in CLIENTS:
        kubectl("apply", "-f", "-", stdin=json.dumps(_client_pod(name, image)))
    for name in CLIENTS:
        kubectl(
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            "-n",
            "cdg-tests",
            "--timeout=300s",
            timeout=320,
        )
    yield {
        name: json.loads(kubectl("get", "pod", name, "-n", "cdg-tests", "-o", "json"))[
            "status"
        ]["podIP"]
        for name in CLIENTS
    }
    kubectl("delete", "pod", *CLIENTS, "-n", "cdg-tests", "--wait=false")


def browse(pod: str, *args: str, user: str = "analyste", timeout: float = 120) -> dict:
    """Une action du navigateur dans un pod client ; le mot de passe par l'entrée
    standard."""
    result = subprocess.run(
        KUBECTL
        + ["exec", "-i", "-n", "cdg-tests", pod, "--", "python", "-c", BROWSER, *args],
        input=CLUSTER.TEST_USERS[user],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, f"navigateur, {args[0]} :\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def web_logs(pod: str, container: str) -> list[dict]:
    """Lignes JSON du journal d'un conteneur d'un pod de l'interface."""
    lines = kubectl("logs", "-n", "cdg", pod, "-c", container).splitlines()
    return [json.loads(line) for line in lines if line.startswith("{")]


def claims(token: str) -> dict:
    """Revendications d'un jeton, lues sans vérification (le test ne fait que les lire)."""
    return jwt.decode(token, options={"verify_signature": False})


def test_connexion_par_le_navigateur_cookie_de_session_securise(clients):
    """Par Traefik : redirection vers Dex, formulaire, retour, sans écran d'accord
    (approval_prompt=auto, réglé par le chart) ; l'interface affiche l'utilisateur dont
    elle a vérifié le jeton. Cookie de session Secure, HttpOnly, SameSite=Lax, pour la
    durée de session réglée."""
    done = browse("client-a", "connexion", "analyste")
    assert done["accord"] is False, "écran d'accord affiché malgré approval_prompt=auto"
    assert (done["hote"], done["statut"], done["utilisateur"]) == (
        "cdg.test",
        200,
        "analyste",
    )
    assert done["cookie"], "aucun cookie de session"
    for attributes in done["cookie"]:
        parts = {part.strip().lower() for part in attributes.split(";")}
        assert {"secure", "httponly", "samesite=lax"} <= parts, attributes
        assert "max-age=120" in parts, attributes  # session de test : 2 minutes


def test_en_tetes_et_jeton_forges_refuses(clients):
    """En-têtes d'identité et jeton forgés (clé de l'attaquant, kid et sub réels), depuis
    un autre pod : sans session, renvoyé vers Dex ; avec la session d'analyste, toujours
    analyste (oauth2-proxy remplace l'en-tête Authorization par le jeton de la session,
    l'interface ne croit que lui)."""
    subject = claims(id_token("relecteur"))["sub"]
    with forward("cdg-tests", "svc/dex", 5556) as base:
        kid = httpx.get(f"{base}/keys", timeout=30).json()["keys"][0]["kid"]
    forged = browse("client-b", "forge", subject, kid)
    assert forged["anonyme"]["hote"] == "dex.cdg.test", forged
    assert forged["anonyme"]["utilisateur"] is None
    assert (forged["connecte"]["hote"], forged["connecte"]["utilisateur"]) == (
        "cdg.test",
        "analyste",
    )


def test_interface_joignable_par_traefik_seulement(clients):
    """Depuis un autre pod, ni oauth2-proxy (règle réseau : Traefik seulement) ni
    l'interface (boucle locale du pod) ne sont joignables en direct."""
    for pod in web_pods():
        opened = browse("client-a", "direct", pod["status"]["podIP"])
        assert all(state.startswith("refuse") for state in opened.values()), opened


def test_https_hsts_et_taille_maximale_par_l_entree(clients):
    """HTTP redirigé vers HTTPS ; HSTS ; envoi plus gros que la taille maximale de
    l'interface refusé par Traefik (413), avant oauth2-proxy."""
    from cdg.adapters.web import security
    from cdg.domain.config import load_config

    size = security.max_body_bytes(load_config()) + 1
    seen = browse("client-a", "entree", str(size))
    code, location = seen["http"]
    assert code in {301, 308} and location == f"{PUBLIC}/analyse", seen
    assert seen["hsts"] == "max-age=31536000", seen
    assert seen["taille"] == 413, seen


def test_tls_1_2_au_moins(clients):
    """TLS 1.1, proposé par le client, refusé par Traefik (alerte protocol_version) ; TLS
    1.2 et 1.3 acceptés."""
    versions = browse("client-a", "tls")
    assert versions["TLSv1_1"] == "refuse TLSV1_ALERT_PROTOCOL_VERSION", versions
    assert (versions["TLSv1_2"], versions["TLSv1_3"]) == ("TLSv1.2", "TLSv1.3")


def test_limites_de_debit_separees_par_client(clients):
    """Rafale d'un client : limité (429) ; l'autre, aussitôt après, ne l'est pas. Traefik
    voit l'adresse de chaque pod (journal d'accès) : la limite porte sur elle."""
    burst = browse("client-a", "rafale", "40")["codes"]
    other = browse("client-b", "rafale", "5")["codes"]
    assert 429 in burst and burst[0] == 200, burst
    assert other == [200] * 5, other
    lines = kubectl(
        "logs", "-n", "traefik", "-l", "app.kubernetes.io/name=traefik", "--tail=-1"
    ).splitlines()
    entries = [json.loads(line) for line in lines if line.startswith("{")]
    pings = [
        e
        for e in entries
        if e.get("RequestPath") == "/ping" and e.get("RequestHost") == "cdg.test"
    ]
    limited = {e["ClientHost"] for e in pings if e.get("DownstreamStatus") == 429}
    served = {e["ClientHost"] for e in pings if e.get("DownstreamStatus") == 200}
    assert limited == {clients["client-a"]}, limited
    assert clients["client-b"] in served, served


def test_rotation_des_cles_du_fournisseur():
    """Dex redémarre avec de nouvelles clés : un jeton signé par la nouvelle clé est
    accepté (clés publiques relues par le proxy de sortie, au plus un délai de
    rafraîchissement après la précédente lecture), l'ancien est refusé et le refus tracé
    (cle_inconnue)."""
    pod = web_pods()[0]["metadata"]["name"]

    def accepted(token: str) -> bool:
        with interface(pod, Identity(token=token)) as client:
            return client.get("/").status_code == 200

    TOKENS.clear()
    old = id_token()
    assert accepted(old)
    kubectl("rollout", "restart", "deployment/dex", "-n", "cdg-tests")
    kubectl(
        "rollout",
        "status",
        "deployment/dex",
        "-n",
        "cdg-tests",
        "--timeout=300s",
        timeout=320,
    )
    TOKENS.clear()
    new = id_token()
    assert (
        jwt.get_unverified_header(new)["kid"] != jwt.get_unverified_header(old)["kid"]
    )
    # PyJWKClient : une clé inconnue relit les clés, au plus une fois par délai (30 s)
    wait_for(lambda: accepted(new), "nouvelle clé acceptée", 120, 5)
    with interface(pod, Identity(token=old)) as client:
        refused = client.get("/")
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"].startswith("Bearer")
    events = [e for e in web_logs(pod, "web") if e.get("journal") == "cdg.acces"]
    assert any(
        e["message"] == "acces_refuse" and e["motif"] == "cle_inconnue" for e in events
    ), events[-5:]


def test_deconnexion_exige_une_nouvelle_connexion(clients):
    """Déconnexion : page de transition, fin de session d'oauth2-proxy (cookie retiré),
    puis Dex de nouveau ; tracée avec sub et émetteur, sans nom ni courriel."""
    seen = browse("client-a", "deconnexion")
    assert seen["avant"]["utilisateur"] == "analyste", seen
    assert seen["transition"] == 200
    assert seen["apres"]["hote"] == "dex.cdg.test", seen
    assert seen["ensuite"]["hote"] == "dex.cdg.test", seen
    assert seen["session"] == [], seen
    events = [
        e
        for pod in web_pods()
        for e in web_logs(pod["metadata"]["name"], "web")
        if e.get("message") == "deconnexion"
    ]
    assert events, "déconnexion non tracée"
    for event in events:
        assert event["iss"] == "https://dex.cdg.test" and event["sub"]
        assert event["session_fournisseur"] == "desactivee"
        assert not {"email", "name", "preferred_username"} & set(event)


def test_session_expiree_refusee(clients):
    """Session de test de 2 minutes : passé ce délai, l'ancien cookie, rejoué tel quel,
    ne donne plus accès (retour vers Dex)."""
    seen = browse("client-b", "expiration", "150", timeout=300)
    assert (seen["avant"]["hote"], seen["avant"]["utilisateur"]) == (
        "cdg.test",
        "analyste",
    )
    assert seen["cookies"] >= 1
    assert seen["apres"]["hote"] == "dex.cdg.test", seen


EMAILS = ("analyste@example.org", "relecteur@example.org")


def test_journaux_sans_courriel_ni_jeton_connexions_tracees_par_sub():
    """Journaux de l'entrée, d'oauth2-proxy et de l'application (espaces cdg et traefik ;
    Dex, fournisseur de test, écarté) : aucun courriel, aucun jeton délivré pendant les
    scénarios, aucun cookie de session. Chaque connexion est tracée par oauth2-proxy avec
    le seul sub."""
    logs, read = container_logs({"cdg", "traefik"})
    assert {p["metadata"]["name"] for p in web_pods()} <= {
        name.split("/", 1)[1] for name in read
    }
    for email in EMAILS:
        assert email not in logs, "courriel trouvé dans un journal"
    assert ISSUED and not any(token in logs for token in ISSUED)
    assert "_cdg_session=" not in logs
    auth = [
        json.loads(line)
        for pod in web_pods()
        for line in kubectl(
            "logs", "-n", "cdg", pod["metadata"]["name"], "-c", "oauth2-proxy"
        ).splitlines()
        if line.startswith('{"journal":"oauth2-proxy"')
    ]
    successes = [e for e in auth if e["statut"] == "AuthSuccess"]
    assert successes, auth[-5:]
    for entry in auth:
        assert set(entry) == {"journal", "evenement", "statut", "sub"}, entry
    assert all(e["sub"] and "@" not in e["sub"] for e in successes)


# --- 9. sauvegarde et restauration, puis désinstallation ----------------------------------


def postgres_ready(name: str) -> bool:
    """Cluster CloudNativePG sain : toutes ses instances prêtes, WAL archivés en continu."""
    cluster = json.loads(kubectl("get", "cluster", "-n", "cdg", name, "-o", "json"))
    status = cluster.get("status", {})
    conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
    return (
        status.get("phase") == "Cluster in healthy state"
        and status.get("readyInstances") == cluster["spec"]["instances"]
        and conditions.get("ContinuousArchiving") == "True"
    )


def release_resources(selector: str) -> set[str]:
    """Ressources de l'espace de noms qui portent ces étiquettes, de toutes les sortes
    listables (métriques et événements exceptés)."""
    kinds = [
        kind
        for kind in kubectl(
            "api-resources", "--verbs=list", "--namespaced", "-o", "name"
        ).split()
        if not kind.endswith(".metrics.k8s.io")
        and kind not in {"events", "events.events.k8s.io"}
    ]
    listed = kubectl(
        "get",
        ",".join(kinds),
        "-n",
        "cdg",
        "-l",
        selector,
        "-o",
        "name",
        "--ignore-not-found",
    )
    return set(listed.split())


def test_sauvegarde_puis_restauration_verifiee_contre_la_tete_conservee():
    """La tête du journal est relevée hors de la base ; une sauvegarde est faite ; un
    nouveau cluster est restauré depuis elle ; l'application, basculée dessus, vérifie la
    chaîne contre la tête conservée (verify --expect-head). Puis la release est
    désinstallée : aucune de ses ressources ne reste, crochets compris, hormis une tâche
    en échec gardée pour le diagnostic, que le nettoyage documenté supprime."""
    wait_for(lambda: postgres_ready("cdg-postgres"), "base saine, WAL archivés", 300, 5)
    pod = web_pods()[0]["metadata"]["name"]
    code, report = cli(pod, "verify")
    assert code == 0 and report["enregistrements"] > 0, report
    head, count = report["tete"], report["enregistrements"]
    name = f"sauvegarde-{uuid.uuid4().hex[:6]}"
    backup = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Backup",
        "metadata": {"name": name, "namespace": "cdg"},
        "spec": {
            "cluster": {"name": "cdg-postgres"},
            "method": "plugin",
            "pluginConfiguration": {"name": "barman-cloud.cloudnative-pg.io"},
        },
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(backup))

    def completed() -> bool:
        status = json.loads(kubectl("get", "backup", "-n", "cdg", name, "-o", "json"))
        phase = status.get("status", {}).get("phase")
        assert phase != "failed", status["status"]
        return phase == "completed"

    wait_for(completed, "sauvegarde terminée", 600, 5)
    restored = helm(
        "upgrade",
        "--install",
        "cdg-postgres-restauree",
        str(ROOT / "chart" / "cdg-postgres"),
        "--namespace",
        "cdg",
        "--values",
        str(ROOT / "cluster" / "valeurs-postgres.yaml"),
        "--set",
        "nom=cdg-postgres-restauree",
        "--set",
        "instances=1",
        "--set",
        "restauration.source=cdg-postgres",
    )
    assert restored.returncode == 0, restored.stderr
    # la condition Ready peut précéder l'instance qui sert : l'état sain est attendu
    wait_for(
        lambda: postgres_ready("cdg-postgres-restauree"), "base restaurée saine", 900, 5
    )
    switched = helm(
        "upgrade",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        "cdg",
        "--reuse-values",
        *SANS_INGESTION,
        "--set",
        "base.hote=cdg-postgres-restauree-rw",
        "--set",
        "base.selecteur.cnpg\\.io/cluster=cdg-postgres-restauree",
        "--set",
        "base.administrateur.secret=cdg-postgres-restauree-superuser",
        "--set",
        "taches.controleConfiguration.passerOutre=true",
        "--wait",
        "--timeout",
        "900s",
    )
    assert switched.returncode == 0, switched.stderr
    wait_for(lambda: len(web_pods()) == 2, "deux pods prêts sur la base restaurée")
    code, report = cli(
        web_pods()[0]["metadata"]["name"], "verify", "--expect-head", head
    )
    assert code == 0, report
    assert (report["tete"], report["enregistrements"]) == (head, count)

    release = "app.kubernetes.io/instance=cdg"
    tasks = f"{release},app.kubernetes.io/component=taches"
    assert release_resources(release)
    uninstalled = helm(
        "uninstall", "cdg", "--namespace", "cdg", "--wait", "--timeout", "300s"
    )
    assert uninstalled.returncode == 0, uninstalled.stderr
    # le temps que les pods, le ReplicaSet et les EndpointSlices suivent leur propriétaire
    wait_for(
        lambda: release_resources(release) <= release_resources(tasks),
        "plus que des tâches après la désinstallation",
        300,
        5,
    )
    jobs = json.loads(kubectl("get", "jobs", "-n", "cdg", "-l", tasks, "-o", "json"))
    for job in jobs["items"]:  # seule une tâche en échec reste, pour le diagnostic
        assert job["status"].get("failed"), job["metadata"]["name"]
    # certificat de l'entrée : supprimé avec l'Ingress, son propriétaire ; son Secret,
    # écrit par cert-manager, lui survit (réutilisable à la réinstallation)
    wait_for(
        lambda: (
            not kubectl(
                "get", "certificate", "cdg-tls", "-n", "cdg", "--ignore-not-found"
            ).strip()
        ),
        "certificat de l'entrée supprimé avec l'Ingress",
        300,
        5,
    )
    assert kubectl("get", "secret", "cdg-tls", "-n", "cdg", "-o", "name").strip()
    # nettoyage documenté (docs/exploitation.md, « Désinstallation »)
    kubectl("delete", "jobs", "--namespace", "cdg", "--selector", release)
    kubectl("delete", "secret", "cdg-tls", "--namespace", "cdg")
    wait_for(
        lambda: not release_resources(release), "aucune ressource de la release", 300, 5
    )

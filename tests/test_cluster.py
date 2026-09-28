"""Scénarios sur le cluster de test k3d (ADR 005, PR C3), préparé par scripts/cluster.py :
plusieurs nœuds, CloudNativePG avec sauvegardes vers SeaweedFS, proxy de sortie, serveur
factice de Mistral, application. Marqueur `cluster`, option `--cluster=DOSSIER`, en un
mot (voir tests/test_cluster_outillage.py).

Les analyses passent par l'interface (redirection de port vers un pod), comme pour un
utilisateur : la CLI, lancée dans un pod, chargerait une seconde fois le modèle (ADR 005,
mesure). La CLI ne sert qu'aux commandes sans modèle (list, journal, verify, resume).
Les scénarios s'enchaînent sur le même cluster, dans l'ordre du fichier.
"""

import contextlib
import importlib.util
import json
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
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


def _cluster_module():
    spec = importlib.util.spec_from_file_location(
        "cluster", ROOT / "scripts" / "cluster.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLUSTER = _cluster_module()


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
def interface(pod: str) -> Iterator[httpx.Client]:
    """Client HTTP de l'interface d'un pod, par redirection de port (elle n'écoute que sur
    127.0.0.1 dans le pod, ADR 004)."""
    process = subprocess.Popen(
        [
            "kubectl",
            "--kubeconfig",
            str(CLUSTER.KUBECONFIG),
            "--context",
            CLUSTER.CONTEXT,
        ]
        + ["port-forward", "-n", "cdg", f"pod/{pod}", "0:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        match = re.search(r"127\.0\.0\.1:(\d+)", line)
        assert match, f"redirection de port impossible : {line}"
        base = f"http://127.0.0.1:{match[1]}"
        with httpx.Client(
            base_url=base, timeout=600, headers={"origin": base}
        ) as client:
            yield client
    finally:
        process.terminate()
        process.wait(10)


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


def test_adresse_d_api_autre_que_mistral_bloquee_en_configuration_de_production(images):
    """Proxy aux valeurs de production (seulement api.mistral.ai, aucune adresse privée) :
    l'analyse, pointée sur le serveur factice, échoue explicitement (rapport d'échec,
    ESCALADE, revue humaine) ; il ne reçoit aucune requête."""
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


# --- 6. contrôle de configuration avant une mise à jour -----------------------------------


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


# --- 7. sauvegarde et restauration --------------------------------------------------------


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


def test_sauvegarde_puis_restauration_verifiee_contre_la_tete_conservee():
    """La tête du journal est relevée hors de la base ; une sauvegarde est faite ; un
    nouveau cluster est restauré depuis elle ; l'application, basculée dessus, vérifie la
    chaîne contre la tête conservée (verify --expect-head)."""
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

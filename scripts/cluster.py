"""Cluster de test k3d (ADR 005, PR C3) : mêmes commandes en CI (job `cluster`) et en local
(`scripts/check.sh`).

  uv run --no-sync python scripts/cluster.py detruire
  uv run --no-sync python scripts/cluster.py creer [--profil ci|local]
  uv run --no-sync python scripts/cluster.py tirer-modele
  uv run --no-sync python scripts/cluster.py images --dossier DOSSIER
  uv run --no-sync python scripts/cluster.py installer [--profil ci|local] --dossier DOSSIER

Profil : `ci` sur GitHub Actions (CI=true), `local` ailleurs (profil réduit) ; mêmes
commandes en CI et dans scripts/check.sh. `detruire` ne fait rien s'il n'y a rien.

- `creer` : un registre local (k3d) et un cluster de plusieurs nœuds, k3s figé par
  empreinte ; le contexte kubectl courant du poste n'est jamais changé (contexte `k3d-cdg`,
  toujours nommé, configuration propre dans .cache/cluster/kubeconfig).
- `tirer-modele` : l'image publiée du modèle d'embedding, figée par l'empreinte du chart,
  après vérification de sa signature et de sa provenance (CI : aucune construction du
  modèle ; exception documentée dans l'ADR 005).
- `images` : les images de l'application, du proxy, du serveur factice et du modèle,
  poussées dans le registre local ; le cluster les tire par empreinte, comme en production.
- `installer` : cert-manager, CloudNativePG et le greffon Barman Cloud (manifestes figés
  par empreinte, chaque image réécrite pour être tirée par empreinte), SeaweedFS, les
  Secrets (générés, passés par l'entrée standard, jamais en argument), puis la base, le
  serveur factice, le proxy et l'application.
- `detruire` : le cluster et le registre.
Réseau : registres des images, publications des composants tiers.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS_DIR = ROOT / "cluster"
NAME = "cdg"
CONTEXT = f"k3d-{NAME}"
# configuration kubectl propre au cluster de test : celle du poste n'est jamais modifiée
KUBECONFIG = ROOT / ".cache" / "cluster" / "kubeconfig"
REGISTRY_NAME = "cdg-registre.localhost"
REGISTRY_PORT = 5111
REGISTRY_HOST = f"localhost:{REGISTRY_PORT}"  # poussée depuis le poste
REGISTRY_CLUSTER = f"k3d-{REGISTRY_NAME}:{REGISTRY_PORT}"  # tirage depuis le cluster
NAMESPACE = "cdg"
TESTS_NAMESPACE = "cdg-tests"
STORAGE_NAMESPACE = "cdg-stockage"
BUCKET = "cdg-sauvegardes"
FACTICE_URL = "http://mistral-factice.cdg-tests.svc.cluster.local:8080"
SERVICE_CIDR = "10.43.0.0/16"  # plage des services de k3s (par défaut)
WAIT = "600s"
# l'application attend sa tâche d'ingestion (crochet post-install : 64 extraits, plus
# de 10 minutes sur 2 CPU, mesure du 28/09) : délai de la tâche (job-ingestion.yaml)
APPLICATION_WAIT = "3600s"
K3D_VERSION = "v5.9.0"  # celle de la CI et de Homebrew (ADR 005)

# canal stable de k3s au 27/09/2026 (ADR 005) : volume image stable
K3S = (
    "rancher/k3s:v1.36.4-k3s1"
    "@sha256:edad48e12bf81c3a09ac1c05c0c0ffaaa22145980b989d6fae84543a76b83657"
)
REGISTRY = (
    "docker.io/library/registry:3"
    "@sha256:852b3e4d378c426dda6b318fe9d9bfe8e92a0eccb9926671ec3d3ea17a196696"
)
SEAWEEDFS = (
    "docker.io/chrislusf/seaweedfs:4.47"
    "@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882"
)


class ClusterError(Exception):
    """Préparation du cluster impossible : la commande s'arrête, avec la cause."""


@dataclass(frozen=True)
class Manifest:
    url: str
    sha256: str

    def checked(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        if digest != self.sha256:
            raise ClusterError(
                f"empreinte inattendue pour {self.url} : {digest}, {self.sha256} attendue"
            )
        return data.decode("utf-8")

    def fetch(self) -> str:
        with urllib.request.urlopen(self.url, timeout=60) as response:
            return self.checked(response.read())


# publications officielles, relevées le 28/09/2026 (ADR 005)
MANIFESTS = {
    "cert-manager": Manifest(
        "https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/"
        "cert-manager.yaml",
        "e03b668ec8675214af6b0a671699d088f2601fa3878e0dbe1b41d3feafd1879f",
    ),
    "cloudnative-pg": Manifest(
        "https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/"
        "cnpg-1.30.1.yaml",
        "37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb",
    ),
    "barman-cloud": Manifest(
        "https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/"
        "v0.15.0/manifest.yaml",
        "1c483eae12a7424ad28ac66bdfee771b510ee8e234cb8756c3ade7254cef2fad",
    ),
}
# images des composants, réécrites pour être tirées par empreinte (index multi-architecture)
IMAGES = {
    "quay.io/jetstack/cert-manager-cainjector:v1.21.2": (
        "quay.io/jetstack/cert-manager-cainjector"
        "@sha256:c85268c64f2e0e76684bf5fe8906caff34b82523561c6affe0fae3546bd87562"
    ),
    "quay.io/jetstack/cert-manager-controller:v1.21.2": (
        "quay.io/jetstack/cert-manager-controller"
        "@sha256:70f532fd9cfde0b09d55687200942399d89838bc2d5d5b45152eb799a15912b8"
    ),
    "quay.io/jetstack/cert-manager-webhook:v1.21.2": (
        "quay.io/jetstack/cert-manager-webhook"
        "@sha256:a60e2dac46dbb8a7f3df95c54ce941012f54c2fe022f0ee55aaa1ab40ed957ae"
    ),
    "quay.io/jetstack/cert-manager-acmesolver:v1.21.2": (
        "quay.io/jetstack/cert-manager-acmesolver"
        "@sha256:699b40d622211ab7accad8a21b04c5fbaa1841ef7a12621e8de492dbe27b2503"
    ),
    "ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1": (
        "ghcr.io/cloudnative-pg/cloudnative-pg"
        "@sha256:923c267ec29636db3bee20f993d0ec4973fa22998e1adad37da79e4d32b5bc07"
    ),
    "ghcr.io/cloudnative-pg/plugin-barman-cloud:v0.15.0": (
        "ghcr.io/cloudnative-pg/plugin-barman-cloud"
        "@sha256:563c680fe7fda3466ca2b1f55a1397ed2ddc9e760360107dd7724f1959c1a536"
    ),
    "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.0": (
        "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar"
        "@sha256:06c78deca670525daa35fb1e5323159092785d11cf87b86217bdd5c679a41a84"
    ),
}


@dataclass(frozen=True)
class Profile:
    agents: int
    postgres_instances: int
    web_memory_request: str


# CI : 1 serveur et 2 agents (runner de 16 Go) ; local réduit : 1 serveur et 1 agent, une
# instance de base, requêtes de mémoire de l'interface abaissées (Docker Desktop, 8 Go)
PROFILES = {
    "ci": Profile(agents=2, postgres_instances=2, web_memory_request="2Gi"),
    "local": Profile(agents=1, postgres_instances=1, web_memory_request="1Gi"),
}


# taille des images en Go, compressées (registre) et décompressées : mesure du 29/09,
# linux/amd64 (docker buildx imagetools inspect, docker image inspect) ; pour les images
# tierces (cert-manager, CloudNativePG, greffon, PostgreSQL, SeaweedFS, k3s, registre),
# 0,78 Go compressés, décompressés estimés au triple, comme l'application (ADR 005)
IMAGE_SIZES_GB = {
    "modele": (1.33, 2.25),
    "application": (0.14, 0.42),
    "oauth2-proxy": (0.02, 0.04),  # PR D1 : binaire publié, distroless static
    # 0,78 Go, puis Traefik (0,055) et Dex (0,048) en PR D1 ; décompressées : le triple
    "tierces": (0.88, 2.64),
}
DATA_GB = 1.0  # bases (trois instances au plus), WAL, sauvegardes : mesurés sous 1 Go
EVICTION_GB = 1.07  # seuil d'éviction des nœuds (1 Gi, KUBELET_ARGS)


def disk_need_gb(nodes: int) -> float:
    """Besoin du cluster, au pire, sur le disque de Docker : chaque image sur chaque nœud,
    en couches compressées et en contenu décompressé ; nos images dans le registre local
    (compressées) et sur l'hôte (décompressées, plus l'archive de l'application) ; les
    données ; le seuil d'éviction."""
    per_node = sum(c + u for c, u in IMAGE_SIZES_GB.values())
    ours = [IMAGE_SIZES_GB[name] for name in ("modele", "application", "oauth2-proxy")]
    registry = sum(c for c, _ in ours)
    host = sum(u for _, u in ours) + IMAGE_SIZES_GB["application"][1]
    return nodes * per_node + registry + host + DATA_GB + EVICTION_GB


def default_profile(environ: Mapping[str, str] = os.environ) -> str:
    """Profil tiré de l'environnement : mêmes commandes en CI et dans scripts/check.sh."""
    return "ci" if environ.get("CI") == "true" else "local"


# --- réécriture des images des composants tiers ---------------------------------------------


def _pin_string(value: str) -> str:
    for tag, pinned in IMAGES.items():
        if value == tag:
            return pinned
        if value.endswith(f"={tag}"):  # argument : --acme-http01-solver-image=…
            return value.removesuffix(tag) + pinned
    return value


def _is_image(value: str) -> bool:
    return "@sha256:" in value or ":" in value.rsplit("/", 1)[-1]


def _walk(node: Any, key: str | None = None) -> Any:
    if isinstance(node, dict):
        return {k: _walk(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(v, key) for v in node]
    if isinstance(node, str):
        pinned = _pin_string(node)
        if key == "image" and "@sha256:" not in pinned and _is_image(pinned):
            raise ClusterError(f"image non figée par empreinte : {pinned}")
        return pinned
    return node


def _pin_secret(doc: dict) -> dict:
    """Une image passée dans un Secret (greffon Barman Cloud : SIDECAR_IMAGE)."""
    for name, value in (doc.get("data") or {}).items():
        try:
            decoded = base64.b64decode("".join(str(value).split())).decode().strip()
        except (ValueError, UnicodeDecodeError):
            continue
        if decoded in IMAGES:
            doc["data"][name] = base64.b64encode(IMAGES[decoded].encode()).decode()
    return doc


def repin(text: str) -> str:
    """Chaque image des manifestes, tirée par empreinte ; une image inconnue est refusée."""
    docs = []
    for doc in yaml.safe_load_all(text):
        if not doc:
            continue
        if doc.get("kind") == "Secret":
            doc = _pin_secret(doc)
        docs.append(_walk(doc))
    return yaml.safe_dump_all(docs, sort_keys=False, width=10**9)


# --- commandes -------------------------------------------------------------------------------

Run = Callable[..., subprocess.CompletedProcess[str]]


def run(command: list[str], what: str, *, stdin: str | None = None) -> str:
    result = subprocess.run(
        command, input=stdin, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ClusterError(f"{what} :\n{result.stdout}{result.stderr}".strip())
    return result.stdout


def kubectl(*args: str, stdin: str | None = None, what: str = "kubectl") -> str:
    command = ["kubectl", "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT]
    return run([*command, *args], what, stdin=stdin)


def helm(*args: str, what: str = "helm") -> str:
    command = ["helm", "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
    return run([*command, *args], what)


def check_k3d(runner: Callable[..., str] | None = None) -> None:
    output = (runner or run)(["k3d", "version"], "k3d introuvable")
    version = output.split()[2] if output.startswith("k3d version") else output
    if version != K3D_VERSION:
        raise ClusterError(f"k3d {version} : {K3D_VERSION} attendu (celui de la CI)")


# disque des nœuds : celui de Docker, partagé (poste, runner). k3s 1.36.4 évince à 5 %
# libres, puis récupère 10 % de plus (pkg/daemons/agent/agent.go) : sur un disque de 110 Go
# rempli par d'autres, tout le cluster était évincé (28/09). Seuils absolus ; k3s ne pose
# que ces deux signaux, les autres restent à zéro comme chez lui (ADR 005). Un seul « @ »,
# filtres séparés par « ; » (cmd/util/filter.go de k3d 5.9.0, que son aide contredit).
KUBELET_ARGS = tuple(
    f"--kubelet-arg={flag}@server:*;agent:*"
    for flag in (
        "eviction-hard=imagefs.available<1Gi,nodefs.available<1Gi",
        "eviction-minimum-reclaim=imagefs.available=500Mi,nodefs.available=500Mi",
    )
)


def create(profile: Profile) -> None:
    check_k3d()
    registries = run(["k3d", "registry", "list", "-o", "json"], "registres k3d")
    if not any(r["name"] == f"k3d-{REGISTRY_NAME}" for r in json.loads(registries)):
        run(
            ["k3d", "registry", "create", REGISTRY_NAME]
            + ["--port", f"127.0.0.1:{REGISTRY_PORT}", "--image", REGISTRY],
            "registre local",
        )
    run(
        ["k3d", "cluster", "create", NAME, "--image", K3S]
        + ["--agents", str(profile.agents), "--no-lb", "--wait", "--timeout", "300s"]
        + ["--registry-use", REGISTRY_CLUSTER]
        + ["--k3s-arg", "--disable=traefik@server:*"]
        + [arg for kubelet in KUBELET_ARGS for arg in ("--k3s-arg", kubelet)]
        + ["--kubeconfig-update-default=false", "--kubeconfig-switch-context=false"],
        "cluster k3d",
    )
    KUBECONFIG.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["k3d", "kubeconfig", "write", NAME, "--output", str(KUBECONFIG)],
        "configuration kubectl du cluster",
    )
    print(f"cluster {CONTEXT} : 1 serveur, {profile.agents} agent(s)")


LOCAL_IMAGES = {
    "application": "cdg:verification",
    "proxy": "cdg-proxy:verification",
    "factice": "cdg-mistral-factice:verification",
    "modele": "cdg-modele:verification",
    "oauth2-proxy": "cdg-oauth2-proxy:verification",  # PR D1
}


def push_images(folder: Path) -> dict[str, dict[str, str]]:
    """Chaque image locale poussée dans le registre ; son dépôt et son empreinte, pour le
    cluster, dans DOSSIER/images.json."""
    pushed = {}
    for role, local in LOCAL_IMAGES.items():
        name = local.split(":")[0]
        target = f"{REGISTRY_HOST}/{name}:verification"
        run(["docker", "tag", local, target], f"étiquette de {local}")
        run(["docker", "push", "--quiet", target], f"poussée de {local}")
        digest = json.loads(
            run(
                ["docker", "buildx", "imagetools", "inspect", target]
                + ["--format", "{{json .Manifest}}"],
                f"empreinte de {local}",
            )
        )["digest"]
        pushed[role] = {"repository": f"{REGISTRY_CLUSTER}/{name}", "digest": digest}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images.json").write_text(json.dumps(pushed, indent=2), encoding="utf-8")
    return pushed


def pull_model() -> None:
    """L'image publiée du modèle (empreinte du chart), signature et provenance vérifiées
    (workflow modele.yml de main), puis étiquetée pour `images`."""
    chaine = _chaine()
    values = yaml.safe_load(
        (ROOT / "chart" / "contract-decision-graph" / "values.yaml").read_text()
    )
    image = f"{values['modele']['image']['repository']}@{values['modele']['image']['digest']}"
    identity = (
        "https://github.com/sifir-gun/contract-decision-graph/.github/workflows/"
        "modele.yml@refs/heads/main"
    )
    run(
        chaine.Cosign(identity, "https://token.actions.githubusercontent.com").command(
            image
        ),
        "signature de l'image du modèle",
    )
    run(
        chaine.GitHubAttestation(
            "sifir-gun",
            "sifir-gun/contract-decision-graph/.github/workflows/modele.yml",
        ).command(image),
        "provenance de l'image du modèle",
    )
    run(["docker", "pull", "--quiet", image], "image du modèle")
    run(["docker", "tag", image, LOCAL_IMAGES["modele"]], "étiquette du modèle")
    print(f"image du modèle vérifiée : {image}")


def _chaine() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "chaine", ROOT / "scripts" / "chaine.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply_components() -> None:
    for name, manifest in MANIFESTS.items():
        kubectl(
            "apply",
            "--server-side",
            "--force-conflicts",
            "-f",
            "-",
            stdin=repin(manifest.fetch()),
            what=f"installation de {name}",
        )
        namespace = "cert-manager" if name == "cert-manager" else "cnpg-system"
        kubectl(
            "wait",
            "--for=condition=Available",
            "deployment",
            "--all",
            "-n",
            namespace,
            f"--timeout={WAIT}",
            what=f"{name} disponible",
        )
        print(f"composant prêt : {name}")


def _secret(
    namespace: str,
    name: str,
    data: dict[str, str],
    *,
    kind: str = "Opaque",
    labels: dict[str, str] | None = None,
) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "labels": labels or {}},
        "type": kind,
        "stringData": data,
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(manifest), what=f"Secret {name}")


def generated(
    namespace: str,
    name: str,
    generators: dict[str, Callable[[], str]],
    *,
    get: Callable[..., str] | None = None,
) -> dict[str, str]:
    """Valeurs d'un Secret tiré au hasard : celles du Secret s'il existe déjà (installation
    relancée), sinon tirées. Une clé attendue qui manque est une erreur, jamais retirée."""
    found = (get or kubectl)(
        "get",
        "secret",
        name,
        "-n",
        namespace,
        "-o",
        "json",
        "--ignore-not-found",
        what=f"Secret {name}",
    )
    if not found.strip():
        return {key: generate() for key, generate in generators.items()}
    data = json.loads(found).get("data") or {}
    missing = sorted(set(generators) - set(data))
    if missing:
        raise ClusterError(
            f"Secret {name} existant sans {', '.join(missing)} : à supprimer ou compléter"
        )
    return {key: base64.b64decode(data[key]).decode() for key in generators}


def _namespaces() -> None:
    for namespace, level in (
        (NAMESPACE, "restricted"),
        (TESTS_NAMESPACE, "restricted"),
        (STORAGE_NAMESPACE, "baseline"),
        ("traefik", "restricted"),  # PR D1 : contexte de sécurité du chart, conforme
    ):
        labels = {
            f"pod-security.kubernetes.io/{mode}": level
            for mode in ("enforce", "warn", "audit")
        }
        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": labels},
        }
        kubectl("apply", "-f", "-", stdin=json.dumps(manifest), what=namespace)


def _manifest(name: str, **images: str) -> str:
    text = (MANIFESTS_DIR / name).read_text(encoding="utf-8")
    for placeholder, image in images.items():
        text = text.replace(f"${{{placeholder}}}", image)
    return text


def _storage(access: str, secret: str) -> None:
    config = {
        "identities": [
            {
                "name": "cdg",
                "credentials": [{"accessKey": access, "secretKey": secret}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"],
            }
        ]
    }
    _secret(STORAGE_NAMESPACE, "seaweedfs-s3", {"s3.json": json.dumps(config)})
    kubectl(
        "apply",
        "-f",
        "-",
        stdin=_manifest("seaweedfs.yaml", SEAWEEDFS=SEAWEEDFS),
        what="SeaweedFS",
    )
    kubectl(
        "wait",
        "--for=condition=Available",
        "deployment/seaweedfs",
        "-n",
        STORAGE_NAMESPACE,
        f"--timeout={WAIT}",
        what="SeaweedFS disponible",
    )
    kubectl(
        "exec",
        "-n",
        STORAGE_NAMESPACE,
        "deployment/seaweedfs",
        "--",
        "sh",
        "-c",
        f'echo "s3.bucket.create -name {BUCKET}" | weed shell',
        what="seau des sauvegardes",
    )


# --- authentification et entrée réseau (PR D1, ADR 005) --------------------------------------

TRAEFIK_NAMESPACE = "traefik"
PUBLIC_HOST, DEX_HOST = "cdg.test", "dex.cdg.test"
# Dex v2.45.1, fournisseur d'identité de test ; signature vérifiée avant l'installation
DEX = (
    "ghcr.io/dexidp/dex:v2.45.1@sha256:"
    "8499afd690c437f52301efd2b05b2455da5bd2dfc20332cd697dc9937f808462"
)
DEX_IDENTITY = (
    "https://github.com/dexidp/dex/.github/workflows/artifacts.yaml@refs/tags/v2.45.1"
)
DEX_ISSUER = "https://token.actions.githubusercontent.com"
# mots de passe de test des utilisateurs de Dex (hachés en bcrypt dans cluster/dex.yaml) :
# tirés pour ce cluster jetable, jamais ailleurs
TEST_USERS = {
    "analyste": "96eOvec_MZ79T-bsh5wYSsrj",
    "relecteur": "uyD6U39cC-MKL7dNss2HBLVP",
    "polyvalent": "u-5ntghYOQGp6S9rf5LjV2pP",
}


@dataclass(frozen=True)
class Chart:
    url: str
    sha256: str


# chart officiel de Traefik : Traefik v3.7.13 (celui de k3s 1.36.4, 3.7.8, désactivé)
TRAEFIK_CHART = Chart(
    url="https://traefik.github.io/charts/traefik/traefik-41.6.0.tgz",
    sha256="cd7254ea853da73bdb88edc896f079b88d43ffa0bfe699fdbf21081361eac365",
)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def fetch_chart(chart: Chart, folder: Path) -> Path:
    """Archive d'un chart, vérifiée par son empreinte avant tout usage."""
    data = _download(chart.url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != chart.sha256:
        raise ClusterError(
            f"chart {chart.url} : empreinte {digest}, {chart.sha256} attendue"
        )
    path = folder / chart.url.rsplit("/", 1)[1]
    path.write_bytes(data)
    return path


def coredns_override() -> str:
    """Noms de test résolus vers Traefik dans le cluster (coredns-custom de k3s)."""
    target = f"traefik.{TRAEFIK_NAMESPACE}.svc.cluster.local"
    return "".join(
        f"rewrite name exact {name} {target}\n" for name in (PUBLIC_HOST, DEX_HOST)
    )


def certificate_authority() -> tuple[str, str]:
    """Autorité de test (EC P-256, 30 jours), en PEM : certificat et clé."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [
            x509.NameAttribute(
                NameOID.COMMON_NAME, "contract-decision-graph, autorité de test"
            )
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def _config_map(namespace: str, name: str, data: dict[str, str]) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(manifest), what=f"ConfigMap {name}")


def _authority() -> None:
    """Autorité de test (reprise si elle existe), émetteur cert-manager, et son certificat
    publié pour oauth2-proxy, l'application et les clients de test."""
    drawn: dict[str, str] = {}

    def draw(key: str) -> str:
        # certificat et clé tirés ensemble, une seule fois
        if not drawn:
            drawn.update(
                zip(("tls.crt", "tls.key"), certificate_authority(), strict=True)
            )
        return drawn[key]

    authority = generated(
        "cert-manager",
        "cdg-autorite",
        {key: (lambda key=key: draw(key)) for key in ("tls.crt", "tls.key")},
    )
    _secret("cert-manager", "cdg-autorite", authority, kind="kubernetes.io/tls")
    issuer = {
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": "cdg-ca"},
        "spec": {"ca": {"secretName": "cdg-autorite"}},
    }
    kubectl("apply", "-f", "-", stdin=json.dumps(issuer), what="émetteur cdg-ca")
    kubectl(
        "wait",
        "--for=condition=Ready",
        "clusterissuer/cdg-ca",
        f"--timeout={WAIT}",
        what="émetteur cdg-ca prêt",
    )
    for namespace in (NAMESPACE, TESTS_NAMESPACE):
        _config_map(namespace, "cdg-autorites", {"ca.crt": authority["tls.crt"]})


def _dns() -> None:
    _config_map("kube-system", "coredns-custom", {"cdg.override": coredns_override()})
    kubectl(
        "rollout", "restart", "deployment/coredns", "-n", "kube-system", what="CoreDNS"
    )
    kubectl(
        "rollout",
        "status",
        "deployment/coredns",
        "-n",
        "kube-system",
        f"--timeout={WAIT}",
        what="CoreDNS relancé",
    )


def _traefik(folder: Path) -> None:
    helm(
        "upgrade",
        "--install",
        "traefik",
        str(fetch_chart(TRAEFIK_CHART, folder)),
        "--namespace",
        TRAEFIK_NAMESPACE,
        "--values",
        str(MANIFESTS_DIR / "valeurs-traefik.yaml"),
        "--wait",
        "--timeout",
        WAIT,
        what="Traefik",
    )


def _dex(client_secret: str) -> None:
    run(_chaine().Cosign(DEX_IDENTITY, DEX_ISSUER).command(DEX), "signature de Dex")
    kubectl(
        "apply",
        "-f",
        "-",
        stdin=_manifest("dex.yaml", DEX=DEX, CLIENT_SECRET=client_secret),
        what="Dex",
    )
    for kind, name in (("deployment", "dex"), ("certificate", "dex-tls")):
        condition = "Available" if kind == "deployment" else "Ready"
        kubectl(
            "wait",
            f"--for=condition={condition}",
            f"{kind}/{name}",
            "-n",
            TESTS_NAMESPACE,
            f"--timeout={WAIT}",
            what=f"{name} prêt",
        )


def install(profile: Profile, folder: Path) -> None:
    images = json.loads((folder / "images.json").read_text(encoding="utf-8"))
    _apply_components()
    _namespaces()
    # entrée (PR D1) : autorité de test, noms de test, Traefik figé
    _authority()
    _dns()
    _traefik(folder)
    # secrets tirés au premier passage, repris ensuite (installation rejouable)
    s3 = generated(
        NAMESPACE,
        "cdg-s3",
        {
            "ACCESS_KEY_ID": lambda: secrets.token_hex(16),
            "ACCESS_SECRET_KEY": lambda: secrets.token_urlsafe(32),
        },
    )
    _storage(s3["ACCESS_KEY_ID"], s3["ACCESS_SECRET_KEY"])
    _secret(NAMESPACE, "cdg-s3", s3)
    # rôle géré par CloudNativePG (chart cdg-postgres) : Secret basic-auth, rechargé
    # aussitôt qu'il change (rotation) ; l'application en lit la clé password
    application = generated(
        NAMESPACE,
        "cdg-base-application",
        {"username": lambda: "app_role", "password": lambda: secrets.token_urlsafe(24)},
    )
    _secret(
        NAMESPACE,
        "cdg-base-application",
        application,
        kind="kubernetes.io/basic-auth",
        labels={"cnpg.io/reload": "true"},
    )
    _secret(NAMESPACE, "cdg-mistral", {"MISTRAL_API_KEY": "cle-du-serveur-factice"})
    # oauth2-proxy (PR D1) : secret du client OIDC, partagé avec Dex ; clé du cookie
    oidc = generated(
        NAMESPACE,
        "cdg-oidc",
        {
            "client-secret": lambda: secrets.token_urlsafe(32),
            "cookie-secret": lambda: secrets.token_urlsafe(24),  # 32 caractères
        },
    )
    _secret(NAMESPACE, "cdg-oidc", oidc)
    # clé CSRF partagée par les réplicas de l'interface (rotation : docs/exploitation.md)
    csrf = generated(
        NAMESPACE, "cdg-csrf", {"courante": lambda: secrets.token_urlsafe(32)}
    )
    _secret(NAMESPACE, "cdg-csrf", csrf)
    helm(
        "upgrade",
        "--install",
        "cdg-postgres",
        str(ROOT / "chart" / "cdg-postgres"),
        "--namespace",
        NAMESPACE,
        "--values",
        str(MANIFESTS_DIR / "valeurs-postgres.yaml"),
        "--set",
        f"instances={profile.postgres_instances}",
        what="base PostgreSQL",
    )
    kubectl(
        "wait",
        "--for=condition=Ready",
        "cluster.postgresql.cnpg.io/cdg-postgres",
        "-n",
        NAMESPACE,
        f"--timeout={WAIT}",
        what="base PostgreSQL prête",
    )
    factice = images["factice"]
    kubectl(
        "apply",
        "-f",
        "-",
        stdin=_manifest(
            "mistral-factice.yaml",
            FACTICE=f"{factice['repository']}@{factice['digest']}",
        ),
        what="serveur factice",
    )
    kubectl(
        "wait",
        "--for=condition=Available",
        "deployment/mistral-factice",
        "-n",
        TESTS_NAMESPACE,
        f"--timeout={WAIT}",
        what="serveur factice disponible",
    )
    _dex(oidc["client-secret"])
    proxy = images["proxy"]
    helm(
        "upgrade",
        "--install",
        "cdg-proxy",
        str(ROOT / "chart" / "cdg-proxy"),
        "--namespace",
        NAMESPACE,
        "--values",
        str(MANIFESTS_DIR / "valeurs-proxy.yaml"),
        "--set",
        f"image.repository={proxy['repository']}",
        "--set",
        f"image.digest={proxy['digest']}",
        "--wait",
        "--timeout",
        WAIT,
        what="proxy de sortie",
    )
    application, modele = images["application"], images["modele"]
    oauth2 = images["oauth2-proxy"]
    helm(
        "upgrade",
        "--install",
        "cdg",
        str(ROOT / "chart" / "contract-decision-graph"),
        "--namespace",
        NAMESPACE,
        "--values",
        str(MANIFESTS_DIR / "valeurs-application.yaml"),
        "--set",
        f"image.repository={application['repository']}",
        "--set",
        f"image.digest={application['digest']}",
        "--set",
        f"modele.image.repository={modele['repository']}",
        "--set",
        f"modele.image.digest={modele['digest']}",
        "--set",
        f"ressources.reel.requests.memory={profile.web_memory_request}",
        "--set",
        f"authentification.image.repository={oauth2['repository']}",
        "--set",
        f"authentification.image.digest={oauth2['digest']}",
        "--wait",
        "--timeout",
        APPLICATION_WAIT,
        what="application",
    )
    print("cluster installé : entrée, Dex, base, serveur factice, proxy, application")


def destroy() -> None:
    subprocess.run(["k3d", "cluster", "delete", NAME], check=False)
    subprocess.run(["k3d", "registry", "delete", f"k3d-{REGISTRY_NAME}"], check=False)
    print(f"cluster {NAME} et registre supprimés")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="commande", required=True)
    for name in ("creer", "installer"):
        sub = commands.add_parser(name)
        # par défaut, d'après l'environnement : CI=true sur GitHub Actions
        sub.add_argument(
            "--profil", choices=sorted(PROFILES), default=default_profile()
        )
        if name == "installer":
            sub.add_argument("--dossier", type=Path, required=True)
    commands.add_parser("tirer-modele")
    images = commands.add_parser("images")
    images.add_argument("--dossier", type=Path, required=True)
    commands.add_parser("detruire")
    args = parser.parse_args(argv)
    try:
        if args.commande == "creer":
            create(PROFILES[args.profil])
        elif args.commande == "tirer-modele":
            pull_model()
        elif args.commande == "images":
            push_images(args.dossier)
        elif args.commande == "installer":
            install(PROFILES[args.profil], args.dossier)
        else:
            destroy()
    except ClusterError as exc:
        print(f"cluster : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Outillage du cluster de test (ADR 005, PR C3), vérifié sans cluster : versions figées
par empreinte, manifestes tiers contrôlés et réécrits pour tirer chaque image par
empreinte, profils CI et local réduit."""

import base64
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(
    r"^[a-z0-9.-]+(:\d+)?(/[a-z0-9._-]+)+(:[\w.-]+)?@sha256:[0-9a-f]{64}$"
)


def cluster():
    spec = importlib.util.spec_from_file_location(
        "cluster", ROOT / "scripts" / "cluster.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_images_des_noeuds_et_du_registre_figees():
    module = cluster()
    assert module.K3S.startswith("rancher/k3s:v1.36.4-k3s1@sha256:")
    assert module.REGISTRY.startswith("docker.io/library/registry:3@sha256:")
    for image in (module.K3S, module.REGISTRY, module.SEAWEEDFS):
        assert DIGEST.fullmatch(image), image


def test_composants_tiers_figes_par_version_et_empreinte():
    module = cluster()
    assert set(module.MANIFESTS) == {"cert-manager", "cloudnative-pg", "barman-cloud"}
    versions = {name: m.url for name, m in module.MANIFESTS.items()}
    assert "/v1.21.2/cert-manager.yaml" in versions["cert-manager"]
    assert "/v1.30.1/cnpg-1.30.1.yaml" in versions["cloudnative-pg"]
    assert "/v0.15.0/manifest.yaml" in versions["barman-cloud"]
    for manifest in module.MANIFESTS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", manifest.sha256)
    for tag, pinned in module.IMAGES.items():
        assert pinned.startswith(tag.rsplit(":", 1)[0] + "@sha256:"), tag
        assert DIGEST.fullmatch(pinned)


def test_manifeste_altere_refuse():
    module = cluster()
    manifest = module.Manifest("https://exemple.test/m.yaml", "0" * 64)
    with pytest.raises(module.ClusterError, match="empreinte"):
        manifest.checked(b"kind: Namespace\n")


def test_images_reecrites_par_empreinte_meme_en_argument_ou_dans_un_secret():
    module = cluster()
    tag = "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.15.0"
    solver = "quay.io/jetstack/cert-manager-acmesolver:v1.21.2"
    encoded = base64.b64encode(tag.encode()).decode()
    text = (
        "kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
        "      - image: ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1\n"
        f"        args: [--acme-http01-solver-image={solver}]\n"
        "---\nkind: Secret\ndata:\n"
        f"  SIDECAR_IMAGE: {encoded}\n"
    )
    pinned = module.repin(text)
    assert module.IMAGES["ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1"] in pinned
    assert f"--acme-http01-solver-image={module.IMAGES[solver]}" in pinned
    secret = base64.b64encode(module.IMAGES[tag].encode()).decode()
    assert f"SIDECAR_IMAGE: {secret}" in pinned
    assert "cloudnative-pg:1.30.1\n" not in pinned


def test_image_inconnue_des_composants_refusee():
    module = cluster()
    with pytest.raises(module.ClusterError, match="exemple.test/inconnue:1"):
        module.repin("spec:\n  containers:\n  - image: exemple.test/inconnue:1\n")


def test_profils_ci_et_local_reduit():
    module = cluster()
    ci, local = module.PROFILES["ci"], module.PROFILES["local"]
    assert (ci.agents, ci.postgres_instances) == (2, 2)
    assert (local.agents, local.postgres_instances) == (1, 1)
    # plusieurs nœuds dans les deux profils : la répartition des réplicas est éprouvée
    assert ci.agents >= 1 and local.agents >= 1
    # profil local réduit : les requêtes de mémoire de l'interface sont abaissées
    assert local.web_memory_request < ci.web_memory_request


def test_profil_choisi_d_apres_l_environnement():
    """Mêmes commandes en CI et dans scripts/check.sh (tests/test_ci.py) : le profil vient
    de l'environnement, CI=true sur GitHub Actions."""
    module = cluster()
    assert module.default_profile({"CI": "true"}) == "ci"
    assert module.default_profile({}) == "local"
    assert module.default_profile({"CI": "false"}) == "local"


def test_attente_de_l_application_couvre_la_tache_d_ingestion():
    """L'installation de l'application attend sa tâche d'ingestion (crochet post-install :
    64 extraits, plus de 10 minutes sur 2 CPU, mesure du 28/09) : l'attente de Helm couvre
    le délai de la tâche, et le job de CI laisse ce délai plus 30 minutes au reste."""
    module = cluster()
    template = ROOT / "chart" / "contract-decision-graph" / "templates"
    [delai] = re.findall(
        r'"delai" (\d+)', (template / "job-ingestion.yaml").read_text(encoding="utf-8")
    )
    assert module.APPLICATION_WAIT == f"{delai}s"
    assert ci_job()["timeout-minutes"] >= int(delai) // 60 + 30


def test_installation_relancee_reprend_les_secrets_deja_crees():
    """`installer` est rejouable : les secrets tirés au premier passage (identités S3,
    mot de passe d'app_role) sont repris, jamais retirés en silence. SeaweedFS ne relit
    ses identités qu'au démarrage : une nouvelle clé casserait les sauvegardes."""
    module = cluster()
    generators = {"ACCESS_KEY_ID": lambda: "neuve", "ACCESS_SECRET_KEY": lambda: "neuf"}

    def existing(*args, what):
        assert args[:2] == ("get", "secret") and "--ignore-not-found" in args
        data = {"ACCESS_KEY_ID": "ancienne", "ACCESS_SECRET_KEY": "ancien"}
        encoded = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
        return json.dumps({"data": encoded})

    assert module.generated("cdg", "cdg-s3", generators, get=existing) == {
        "ACCESS_KEY_ID": "ancienne",
        "ACCESS_SECRET_KEY": "ancien",
    }
    assert module.generated("cdg", "cdg-s3", generators, get=lambda *a, what: "") == {
        "ACCESS_KEY_ID": "neuve",
        "ACCESS_SECRET_KEY": "neuf",
    }

    def partial(*args, what):
        return json.dumps({"data": {"ACCESS_KEY_ID": base64.b64encode(b"a").decode()}})

    with pytest.raises(module.ClusterError, match="ACCESS_SECRET_KEY"):
        module.generated("cdg", "cdg-s3", generators, get=partial)


def test_noeuds_evinces_sur_un_seuil_absolu_de_disque(monkeypatch, tmp_path):
    """Le disque des nœuds est celui de Docker, partagé (poste, runner) : k3s 1.36.4
    évince à 5 % libres puis récupère 10 % de plus (pkg/daemons/agent/agent.go), soit
    tout le cluster sur un disque de 110 Go à 86 % (vu le 28/09). Seuils absolus, sur
    chaque nœud ; toute la mémoire et les inodes restent aux réglages de k3s."""
    module = cluster()
    commands = []

    def fake(command, what, *, stdin=None):
        commands.append(command)
        if command[:2] == ["k3d", "version"]:
            return f"k3d version {module.K3D_VERSION}\n"
        return "[]" if command[:3] == ["k3d", "registry", "list"] else ""

    monkeypatch.setattr(module, "run", fake)
    monkeypatch.setattr(module, "KUBECONFIG", tmp_path / "kubeconfig")
    module.create(module.PROFILES["local"])
    [create] = [c for c in commands if c[:3] == ["k3d", "cluster", "create"]]
    k3s_args = [create[i + 1] for i, arg in enumerate(create) if arg == "--k3s-arg"]
    assert (
        "--kubelet-arg=eviction-hard=imagefs.available<1Gi,nodefs.available<1Gi"
        "@server:*;agent:*"
    ) in k3s_args
    assert (
        "--kubelet-arg=eviction-minimum-reclaim=imagefs.available=500Mi,"
        "nodefs.available=500Mi@server:*;agent:*"
    ) in k3s_args


def test_version_de_k3d_verifiee():
    module = cluster()

    def answer(output):
        def run(command, what, **_):
            assert command == ["k3d", "version"]
            return output

        return run

    module.check_k3d(answer("k3d version v5.9.0\nk3s version v1.35.5-k3s1 (default)\n"))
    with pytest.raises(module.ClusterError, match="v5.9.0"):
        module.check_k3d(answer("k3d version v5.8.3\n"))


# --- CI : job cluster, mêmes commandes que check.sh ------------------------------------------

K3D_SHA256 = "06d8f25bc3a971c4eb29e0ff08429b180402db0f4dec838c9eac427e296800a0"
KUBECTL_SHA256 = "8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a"
SCRIPT = "uv run --no-sync python scripts/cluster.py"
ORDER = [
    f"{SCRIPT} detruire",
    f"{SCRIPT} tirer-modele",
    f"{SCRIPT} creer",
    f'{SCRIPT} images --dossier "$RUNNER_TEMP/cluster"',
    f'{SCRIPT} installer --dossier "$RUNNER_TEMP/cluster"',
    'uv run --no-sync pytest -m cluster --cluster="$RUNNER_TEMP/cluster"',
]


def ci_job() -> dict:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    return workflow["jobs"]["cluster"]


def test_outils_du_cluster_installes_depuis_leurs_binaires_officiels_verifies():
    steps = {s.get("id"): s.get("run", "") for s in ci_job()["steps"]}
    k3d = steps["installation-k3d"]
    assert (
        "https://github.com/k3d-io/k3d/releases/download/v5.9.0/k3d-linux-amd64" in k3d
    )
    assert f"{K3D_SHA256}  " in k3d and "sha256sum --check --strict" in k3d
    kubectl = steps["installation-kubectl"]
    assert "https://dl.k8s.io/release/v1.36.4/bin/linux/amd64/kubectl" in kubectl
    assert f"{KUBECTL_SHA256}  " in kubectl and "sha256sum --check --strict" in kubectl
    assert "installation-helm" in steps  # helm 4.3.0, comme le job chart


def test_images_de_l_application_et_du_proxy_reprises_du_job_image():
    job = ci_job()
    assert "image" in job["needs"] and job["runs-on"] == "ubuntu-24.04"
    downloads = [s for s in job["steps"] if "download-artifact" in s.get("uses", "")]
    assert sorted(d["with"]["name"] for d in downloads) == [
        "image-amd64",
        "proxy-amd64",
    ]
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "docker load --input" in runs
    builds = [
        s["run"] for s in job["steps"] if s.get("run", "").startswith("docker build")
    ]
    # seule l'image de test du serveur factice est construite ici, sur l'image chargée
    factice = (
        "docker build --file docker/mistral-factice/Dockerfile "
        "--build-arg APPLICATION=cdg:verification "
        "--tag cdg-mistral-factice:verification ."
    )
    assert builds == [factice]


def test_deroule_du_cluster_et_destruction_quoi_qu_il_arrive():
    steps = ci_job()["steps"]
    runs = [" ".join(s.get("run", "").split()) for s in steps]
    positions = [runs.index(command) for command in ORDER]
    assert positions == sorted(positions)
    [last] = [
        s for s in steps if s.get("run", "") == f"{SCRIPT} detruire" and s.get("if")
    ]
    assert last["if"] == "always()"
    [model] = [s for s in steps if s.get("run", "") == f"{SCRIPT} tirer-modele"]
    assert model["env"]["GH_TOKEN"] == "${{ github.token }}"  # provenance vérifiée


def test_besoin_de_disque_calcule_sur_la_taille_reelle_des_images():
    """Tailles mesurées le 29/09 (registre, linux/amd64 ; décompressées : docker) : le
    modèle pèse 1,33 Go compressé et 2,25 Go décompressé. Au pire, chaque image est sur
    chaque nœud, deux fois (couches compressées et contenu décompressé), nos images en
    plus dans le registre local et sur l'hôte, avec les données et le seuil
    d'éviction."""
    module = cluster()
    assert module.IMAGE_SIZES_GB["modele"] == (1.33, 2.25)
    assert module.IMAGE_SIZES_GB["application"] == (0.14, 0.42)
    nodes = module.PROFILES["ci"].agents + 1
    need = module.disk_need_gb(nodes)
    assert 28 < need < 29
    assert module.disk_need_gb(nodes + 1) - need == pytest.approx(7.26)


def test_runner_libere_les_outils_inutilises_sous_le_seuil_de_disque():
    """Un runner public ne garantit que 14 Go (documentation de GitHub), sous le besoin
    du cluster au pire. Avant le cluster, le job retire des outils préinstallés qu'il
    n'utilise pas, seulement si l'espace libre est sous ce besoin, et journalise l'espace
    libre avant et, en fin de job, l'espace restant. Jamais dans scripts/check.sh : le
    poste n'est pas un runner."""
    module = cluster()
    steps = ci_job()["steps"]
    ids = [s.get("id") for s in steps]
    [space] = [s for s in steps if s.get("id") == "installation-espace-disque"]
    create = [s.get("run", "") for s in steps].index(f"{SCRIPT} creer")
    assert ids.index("installation-espace-disque") < create
    threshold = int(space["env"]["ESPACE_MIN_GO"])
    assert threshold >= module.disk_need_gb(module.PROFILES["ci"].agents + 1)
    assert threshold < module.disk_need_gb(module.PROFILES["ci"].agents + 1) + 2
    lines = [line.strip() for line in space["run"].splitlines() if line.strip()]
    assert lines[0] == "df -h /"
    assert (
        'libre=$(df --output=avail --block-size=1G / | tail -n 1 | tr -d " ")' in lines
    )
    guarded = lines.index("if (( libre < ESPACE_MIN_GO )); then")
    removal = next(i for i, line in enumerate(lines) if line.startswith("sudo rm -rf"))
    assert guarded < removal < lines.index("else") < lines.index("fi")
    for folder in (
        "/usr/local/lib/android",
        "/usr/share/dotnet",
        "/usr/local/.ghcup",
        "/opt/hostedtoolcache/CodeQL",
    ):
        assert folder in lines[removal]
    [left] = [s for s in steps if s.get("id") == "diagnostic-espace-disque"]
    assert left["if"] == "always()" and left["run"].strip() == "df -h /"
    destroy = [
        i for i, s in enumerate(steps) if s.get("run", "") == f"{SCRIPT} detruire"
    ][-1]
    assert ids.index("diagnostic-espace-disque") < destroy
    script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "rm -rf /usr" not in script and "/usr/local/lib/android" not in script


def test_commande_des_scenarios_comprise_par_pytest_hors_du_depot(tmp_path):
    """Un dossier hors du dépôt passé en second mot (`--cluster DOSSIER`) est pris par
    pytest pour une cible : il y cherche sa racine, ne charge pas tests/conftest.py et
    refuse l'option (vu par check.sh le 28/09). La commande du job, telle quelle, avec
    le dossier du runner : les onze scénarios sont collectés."""
    [command] = [c for c in ORDER if " pytest " in c]
    folder = tmp_path / "cluster"
    folder.mkdir()
    args = shlex.split(command.replace("$RUNNER_TEMP", str(tmp_path)))
    assert args[:4] == ["uv", "run", "--no-sync", "pytest"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *args[4:], "--collect-only", "-q"]
        + ["-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.match(r"11/\d+ tests collected", result.stdout.splitlines()[-1])


def test_check_sh_deroule_le_meme_cluster():
    script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    lines = [" ".join(line.split()) for line in script.splitlines()]
    positions = [lines.index(command) for command in ORDER]
    assert positions == sorted(positions)
    assert f"{SCRIPT} detruire" in lines[positions[-1] + 1 :]

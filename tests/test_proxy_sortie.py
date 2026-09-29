"""Image du proxy de sortie (ADR 005, PR C3) : Smokescreen, construit par nous, avec la même
chaîne que l'image de l'application.

- Sans image : bases vérifiables (scripts/chaine.py), archive officielle de Go vérifiée par
  son empreinte pour chaque architecture, source figée par son commit, construction
  reproductible, licences embarquées.
- Sur l'image construite (marqueur `proxy`, `--proxy ÉTIQUETTE`, avec `--image` pour le
  client et le serveur de test, qui ont Python) : non root, sans shell, licences présentes ;
  un domaine autorisé passe, un autre est refusé, et en configuration de production les
  adresses privées sont refusées même pour un domaine autorisé.
"""

import importlib.util
import io
import json
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROXY = ROOT / "docker" / "proxy-sortie"
# commit de master du 28/09/2026 : les correctifs de sécurité publiés après la v0.1.0
# (#326 adresses NAT64 locales, #328 connexions vers soi, #329 validation des CRL)
SMOKESCREEN_COMMIT = "6c44698887826662733b47bbdc461aabcbb365ee"
GO = "1.26.8"  # go.mod de Smokescreen : go 1.26.0
GO_SHA256 = {  # https://go.dev/dl/?mode=json, relevé le 28/09/2026
    "amd64": "d0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b",
    "arm64": "211ffced9dcb9633a55eac6364816ec0ddd951389a740e88fa8b3337971bdda0",
}
NONROOT = "65532:65532"


def chaine():
    spec = importlib.util.spec_from_file_location(
        "chaine", ROOT / "scripts" / "chaine.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instructions() -> list[str]:
    lines, current = [], ""
    for raw in (PROXY / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line.removesuffix(chr(92)).strip()}".strip()
        if not line.endswith("\\"):
            lines.append(current)
            current = ""
    return lines


# --- sans image ------------------------------------------------------------------------------


def test_bases_de_l_application_verifiables():
    module = chaine()
    bases = module.bases(PROXY / "Dockerfile")
    application = module.bases(ROOT / "Dockerfile")
    assert bases[0] == application[0]  # construction : l'image de uv, déjà vérifiée
    assert bases[1].startswith("gcr.io/distroless/static-debian13:nonroot@sha256:")
    assert len(bases) == 2
    for base in bases:
        module.command(base)  # une politique de vérification pour chacune


def test_go_officiel_verifie_par_son_empreinte_pour_chaque_architecture():
    adds = [i for i in instructions() if i.startswith("ADD --checksum=sha256:")]
    for arch, digest in GO_SHA256.items():
        url = f"https://go.dev/dl/go{GO}.linux-{arch}.tar.gz"
        assert f"ADD --checksum=sha256:{digest} {url} /go.tar.gz" in adds, arch


def test_source_de_smokescreen_figee_par_son_commit():
    source = f"https://github.com/stripe/smokescreen.git#{SMOKESCREEN_COMMIT}"
    assert f"ADD --keep-git-dir=false {source} /src" in instructions()


def test_construction_statique_reproductible_sans_telechargement():
    [build] = [i for i in instructions() if "go build" in i]
    for flag in (
        "CGO_ENABLED=0",
        "-mod=vendor",
        "-trimpath",
        "GOFLAGS=-buildvcs=false",
    ):
        assert flag in build, flag
    assert "GOPROXY=off" in build  # dépendances du dossier vendor seulement


def test_image_finale_non_root_sans_autre_contenu_que_le_proxy_et_ses_licences():
    lines = instructions()
    final = lines[max(i for i, line in enumerate(lines) if line.startswith("FROM ")) :]
    copies = [line for line in final if line.startswith("COPY")]
    assert copies == [
        "COPY --from=construction /smokescreen /smokescreen",
        "COPY --from=construction /licences /licences",
    ]
    assert f"USER {NONROOT}" in final
    assert 'ENTRYPOINT ["/smokescreen"]' in final
    assert 'CMD ["--config-file", "/configuration/smokescreen.yaml"]' in final


def test_contexte_de_construction_vide():
    ignored = (PROXY / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    rules = [line for line in ignored.splitlines() if line and not line.startswith("#")]
    assert rules == ["*"]  # tout vient des sources figées, rien du dépôt


# --- sur l'image construite (--proxy, --image) --------------------------------------------------

WAIT = 60


@pytest.fixture(scope="module")
def images(request) -> tuple[str, str]:
    tags = request.config.getoption("--proxy"), request.config.getoption("--image")
    for tag in tags:
        assert tag, "--proxy et --image exigés"
        found = docker("image", "inspect", tag)
        assert found.returncode == 0, f"image introuvable : {tag}"
    return tags


def docker(*args: str, timeout: float = WAIT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


HARDENED = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]


@pytest.mark.proxy
def test_image_non_root_sans_shell(images):
    proxy, _ = images
    [config] = json.loads(docker("image", "inspect", proxy).stdout)
    assert config["Config"]["User"] == NONROOT
    assert config["Config"]["Entrypoint"] == ["/smokescreen"]
    assert docker("run", "--rm", "--entrypoint", "sh", proxy).returncode == 127


@pytest.mark.proxy
def test_licences_embarquees(images):
    proxy, _ = images
    container = docker("create", proxy).stdout.strip()
    try:
        exported = subprocess.run(
            ["docker", "export", container],
            capture_output=True,
            check=True,
            timeout=WAIT,
        ).stdout
    finally:
        docker("rm", "--force", container)
    with tarfile.open(fileobj=io.BytesIO(exported)) as archive:
        names = set(archive.getnames())
    assert "licences/smokescreen/LICENSE.txt" in names
    assert "licences/go/LICENSE" in names
    assert any(n.startswith("licences/vendor/") for n in names)


def _network_scenario(
    proxy: str, application: str, config: str, folder: Path
) -> dict[str, int]:
    """Serveur HTTP (Python de l'image de l'application) sous deux noms, proxy, client :
    code HTTP obtenu par le client pour chaque nom, à travers le proxy."""
    name = f"cdg-proxy-{uuid.uuid4().hex[:8]}"
    created = docker("network", "create", name)
    assert created.returncode == 0, created.stderr
    containers: list[str] = []
    try:
        server = docker(
            "run",
            "--detach",
            "--network",
            name,
            "--network-alias",
            "autorise.test",
            "--network-alias",
            "refuse.test",
            *HARDENED,
            "--entrypoint",
            "python",
            application,
            "-m",
            "http.server",
            "8080",
            "--directory",
            "/app/config",
        )
        assert server.returncode == 0, server.stderr
        containers.append(server.stdout.strip())
        subnet = json.loads(docker("network", "inspect", name).stdout)[0]["IPAM"][
            "Config"
        ][0]["Subnet"]
        # lisible par l'utilisateur du proxy (65532) : pytest crée ses dossiers en 0700
        folder.chmod(0o755)
        (folder / "smokescreen.yaml").write_text(config.replace("SUBNET", subnet))
        (folder / "acl.yaml").write_text(
            "version: v1\nservices: []\ndefault:\n  project: cdg\n  action: enforce\n"
            "  allowed_domains:\n    - autorise.test\n"
        )
        started = docker(
            "run",
            "--detach",
            "--network",
            name,
            "--network-alias",
            "proxy",
            *HARDENED,
            "-v",
            f"{folder}:/configuration:ro",
            proxy,
        )
        assert started.returncode == 0, started.stderr
        containers.append(started.stdout.strip())
        time.sleep(2)
        client = (
            "import json, urllib.request, urllib.error\n"
            "opener = urllib.request.build_opener(urllib.request.ProxyHandler("
            "{'http': 'http://proxy:4750'}))\n"
            "codes = {}\n"
            "for host in ('autorise.test', 'refuse.test'):\n"
            "    try:\n"
            "        codes[host] = opener.open(f'http://{host}:8080/', timeout=10).status\n"
            "    except urllib.error.HTTPError as exc:\n"
            "        codes[host] = exc.code\n"
            "print(json.dumps(codes))\n"
        )
        ran = docker(
            "run",
            "--rm",
            "--network",
            name,
            *HARDENED,
            "--entrypoint",
            "python",
            application,
            "-c",
            client,
        )
        logs = docker("logs", containers[-1])
        assert ran.returncode == 0, ran.stderr + logs.stdout + logs.stderr
        return json.loads(ran.stdout)
    finally:
        for container in containers:
            docker("rm", "--force", container)
        docker("network", "rm", name)


@pytest.mark.proxy
def test_domaine_autorise_passe_autre_domaine_refuse(images, tmp_path):
    config = (
        "acl_file: /configuration/acl.yaml\nallow_missing_role: true\n"
        "allow_ranges:\n  - SUBNET\n"
    )
    codes = _network_scenario(*images, config, tmp_path)
    assert codes["autorise.test"] == 200
    assert codes["refuse.test"] == 407


@pytest.mark.proxy
def test_adresses_privees_refusees_en_production_meme_pour_un_domaine_autorise(
    images, tmp_path
):
    config = "acl_file: /configuration/acl.yaml\nallow_missing_role: true\n"
    codes = _network_scenario(*images, config, tmp_path)
    assert codes == {"autorise.test": 407, "refuse.test": 407}

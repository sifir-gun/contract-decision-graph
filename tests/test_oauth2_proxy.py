"""Image d'oauth2-proxy (PR D1, ADR 005, option B) : construite par notre chaîne à partir
du binaire publié dans la release GitHub v7.15.4, jamais depuis l'image officielle, dont
les binaires ne correspondent pas aux empreintes publiées (relevé du 30/09/2026).

- Sans image : archive de la release vérifiée par son empreinte, binaire extrait comparé à
  l'empreinte publiée pour chaque architecture, licence d'oauth2-proxy figée, licences des
  modules Go du binaire reconstituées, bases vérifiables, contexte vide.
- Sur l'image construite (marqueur `oauth2proxy`, `--oauth2-proxy ÉTIQUETTE`) : non root,
  sans shell, version attendue, binaire identique à l'empreinte publiée, licences de chaque
  module embarqué.
"""

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "docker" / "oauth2-proxy"
VERSION = "v7.15.4"
RELEASE = f"https://github.com/oauth2-proxy/oauth2-proxy/releases/download/{VERSION}"
# fichiers *.tar.gz-sha256sum.txt et *-sha256sum.txt de la release, relevés le 30/09/2026
ARCHIVE_SHA256 = {
    "amd64": "4fbe902189aab713d9c0519b90a645032d4636ecb523dc36f5cc312d8ebef1e2",
    "arm64": "b3fb0b61aecfb4b776dccb6daff406676ed0ead74245dc6e4bdf71f993eb1710",
}
BINARY_SHA256 = {
    "amd64": "d2cc1a814d189ad3c5d3ac712dcfbea43176afacae814c8f64e8dded885f5c41",
    "arm64": "dd70759fdd210ea10e16d44a8072356f74789da2d58fd1a7f3b9bb08922b9c40",
}
TAG_COMMIT = "81ff034fe2ff3246e670c694b02e3267d1ae46bc"  # étiquette v7.15.4
LICENSE_SHA256 = "89807acf2309bd285f033404ee78581602f3cd9b819a16ac2f0e5f60ff4a473e"
NONROOT = "65532:65532"
WAIT = 300


def instructions() -> list[str]:
    lines, current = [], ""
    for raw in (FOLDER / "Dockerfile").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line.removesuffix(chr(92)).strip()}".strip()
        if not line.endswith("\\"):
            lines.append(current)
            current = ""
    return lines


def chaine():
    spec = importlib.util.spec_from_file_location(
        "chaine", ROOT / "scripts" / "chaine.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- sans image ------------------------------------------------------------------------------


def test_archive_de_la_release_verifiee_par_son_empreinte():
    for arch, digest in ARCHIVE_SHA256.items():
        url = f"{RELEASE}/oauth2-proxy-{VERSION}.linux-{arch}.tar.gz"
        assert f"ADD --checksum=sha256:{digest} {url} /archive.tar.gz" in instructions()


def test_binaire_compare_a_l_empreinte_publiee_pour_chaque_architecture():
    """Échec de la construction si le binaire de l'archive n'est pas celui dont la
    release publie l'empreinte."""
    text = " ".join(instructions())
    for arch, digest in BINARY_SHA256.items():
        assert f"ENV BINAIRE_SHA256={digest}" in text, arch
    assert "sha256sum --check --strict" in text


def test_licence_d_oauth2_proxy_figee_au_commit_de_l_etiquette():
    url = (
        f"https://raw.githubusercontent.com/oauth2-proxy/oauth2-proxy/{TAG_COMMIT}"
        "/LICENSE"
    )
    assert (
        f"ADD --checksum=sha256:{LICENSE_SHA256} {url} /licences/oauth2-proxy/LICENSE"
        in (instructions())
    )


def test_licences_des_modules_du_binaire_reconstituees_sans_trou():
    """Chaque module Go lié au binaire (go version -m) : sa licence, téléchargée à la
    version exacte et vérifiée par la base de sommes de Go ; un module sans licence
    arrête la construction."""
    text = " ".join(instructions())
    assert "go version -m /oauth2-proxy" in text
    assert "go mod download -json" in text
    assert "GOSUMDB=sum.golang.org" in text
    assert "module sans licence" in text


UV_BASE = (
    "ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:"
    "c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197"
)
STATIC_BASE = (
    "gcr.io/distroless/static-debian13:nonroot@sha256:"
    "e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3"
)


def test_bases_verifiables():
    assert chaine().bases(FOLDER / "Dockerfile") == [UV_BASE, STATIC_BASE]


def test_image_finale_non_root_sans_autre_contenu_que_le_binaire_et_ses_licences():
    final = instructions()[instructions().index(f"FROM {STATIC_BASE}") :]
    copies = [line for line in final if line.startswith("COPY")]
    assert copies == [
        "COPY --from=binaire /oauth2-proxy /oauth2-proxy",
        "COPY --from=licences /licences /licences",
    ]
    assert f"USER {NONROOT}" in final
    assert 'ENTRYPOINT ["/oauth2-proxy"]' in final


def test_contexte_de_construction_vide():
    ignored = (FOLDER / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    assert [line for line in ignored.splitlines() if not line.startswith("#")] == ["*"]


# --- sur l'image construite ------------------------------------------------------------------


def docker(*args: str, timeout: float = WAIT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


@pytest.fixture(scope="module")
def image(request) -> str:
    tag = request.config.getoption("--oauth2-proxy")
    assert docker("image", "inspect", tag).returncode == 0, f"image absente : {tag}"
    return tag


def files(tag: str) -> dict[str, bytes]:
    """Fichiers réguliers de l'image, par son export, sous leur chemin absolu."""
    container = docker("create", tag).stdout.strip()
    try:
        exported = subprocess.run(
            ["docker", "export", container], capture_output=True, check=True
        ).stdout
    finally:
        docker("rm", "--force", container)
    found = {}
    with tarfile.open(fileobj=io.BytesIO(exported)) as archive:
        for member in archive.getmembers():
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                found["/" + member.name.lstrip("/")] = extracted.read()
    return found


@pytest.mark.oauth2proxy
def test_image_non_root_sans_shell(image):
    [config] = json.loads(docker("image", "inspect", image).stdout)
    assert config["Config"]["User"] == NONROOT
    assert config["Config"]["Entrypoint"] == ["/oauth2-proxy"]
    assert docker("run", "--rm", "--entrypoint", "sh", image).returncode == 127


@pytest.mark.oauth2proxy
def test_version_attendue(image):
    ran = docker("run", "--rm", "--read-only", image, "--version")
    assert ran.returncode == 0, ran.stderr
    assert VERSION in ran.stdout + ran.stderr


@pytest.mark.oauth2proxy
def test_binaire_identique_a_l_empreinte_publiee(image):
    [config] = json.loads(docker("image", "inspect", image).stdout)
    arch = config["Architecture"]
    binary = files(image)["/oauth2-proxy"]  # chemin de l'ENTRYPOINT
    assert hashlib.sha256(binary).hexdigest() == BINARY_SHA256[arch]


@pytest.mark.oauth2proxy
def test_licence_de_chaque_module_embarque(image):
    content = files(image)
    licence = content["/licences/oauth2-proxy/LICENSE"]
    assert hashlib.sha256(licence).hexdigest() == LICENSE_SHA256
    assert "/licences/go/LICENSE" in content
    modules = content["/licences/modules.txt"].decode().split()
    assert len(modules) > 20
    for module in modules:
        prefix = f"/licences/modules/{module}/"
        assert any(name.startswith(prefix) for name in content), module

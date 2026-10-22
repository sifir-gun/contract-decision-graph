"""Image d'oauth2-proxy (PR D1, ADR 005) : jusqu'au 12/10/2026, le binaire publié dans la
release GitHub v7.15.4 (option B). Amendement du 12/10 à l'ADR 005 : aucune version publiée
n'est compilée avec Go 1.26.9 (failles de net/http du 08/10) ; compilée par notre chaîne
depuis les sources de l'étiquette v7.15.5, avec Go 1.26.10 (1.26.9 jusqu'au 21/10 :
GO-2026-6809, crypto/x509) et golang.org/x/net 0.60.0, jusqu'à ce qu'une version
officielle soit compilée avec Go 1.26.9 ou plus.

- Sans image : Go officiel vérifié par son empreinte, commit de l'étiquette vérifié, x/net
  vérifié par la base de sommes de Go, suite de tests d'oauth2-proxy et govulncheck
  bloquants, binaire statique à la version de l'étiquette, licence figée, licences des
  modules Go du binaire reconstituées, bases vérifiables, contexte vide.
- Sur l'image construite (marqueur `oauth2proxy`, `--oauth2-proxy ÉTIQUETTE`) : non root,
  sans shell, version attendue et Go 1.26.10, x/net 0.60.0 lié, licences de chaque
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
VERSION = "v7.15.5"
SOURCE = f"https://github.com/oauth2-proxy/oauth2-proxy.git#{VERSION}"
# commit de l'étiquette annotée v7.15.5 (objet 6e4bd00e…), relevé le 12/10/2026
TAG_COMMIT = "bea3f04bf87b93f5354f392fe2216a88e6031350"
GO = "1.26.10"
GO_SHA256 = {  # https://go.dev/dl/?mode=json, relevé le 21/10/2026
    "amd64": "ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5",
    "arm64": "3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797",
}
# version gravée dans le binaire, comme dist.sh de la release
LDFLAG_VERSION = "github.com/oauth2-proxy/oauth2-proxy/v7/pkg/version.VERSION"
# LICENSE identique aux étiquettes v7.15.4 et v7.15.5
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


def test_go_officiel_verifie_par_son_empreinte_pour_chaque_architecture():
    adds = [i for i in instructions() if i.startswith("ADD --checksum=sha256:")]
    for arch, digest in GO_SHA256.items():
        url = f"https://go.dev/dl/go{GO}.linux-{arch}.tar.gz"
        assert f"ADD --checksum=sha256:{digest} {url} /go.tar.gz" in adds, arch


def test_sources_de_l_etiquette_publiee_commit_verifie():
    """Le commit compilé est celui de l'étiquette v7.15.5 publiée : l'étiquette déplacée,
    la construction échoue."""
    lines = instructions()
    assert f"ADD --keep-git-dir=true {SOURCE} /src" in lines
    [check] = [i for i in lines if ".git/HEAD" in i]
    assert f'test "$(cat .git/HEAD)" = "{TAG_COMMIT}"' in check
    assert "rm -rf .git" in check


def test_x_net_0_60_0_verifie_par_la_base_de_sommes_de_go():
    [update] = [i for i in instructions() if "go get" in i]
    for part in (
        "GOPROXY=https://proxy.golang.org",
        "GOSUMDB=sum.golang.org",
        "go get golang.org/x/net@v0.60.0",
        "go mod download",
        "go mod verify",
        "go list -m -f '{{.Version}}' golang.org/x/net",
        '= "v0.60.0"',
    ):
        assert part in update, part
    assert "GONOSUMDB" not in update and "GOFLAGS=-insecure" not in update


def test_suite_de_tests_et_govulncheck_bloquants_pendant_la_construction():
    """Suite de tests d'oauth2-proxy (sans -race, qui exige cgo), sans téléchargement ;
    govulncheck sur le binaire construit : une faille atteignable arrête la
    construction."""
    lines = instructions()
    [tests] = [i for i in lines if "go test" in i]
    assert "go test ./..." in tests and "GOPROXY=off" in tests
    [build] = [i for i in lines if "go build" in i]
    for part in (
        "CGO_ENABLED=0",
        "GOPROXY=off",
        "-trimpath",
        f'-ldflags="-X {LDFLAG_VERSION}={VERSION}"',
        "-o /oauth2-proxy",
    ):
        assert part in build, part
    [check] = [i for i in lines if "govulncheck -mode=binary" in i]
    assert "go install golang.org/x/vuln/cmd/govulncheck@v1.1.4" in check
    assert "GOSUMDB=sum.golang.org" in check
    assert "govulncheck -mode=binary -show verbose /oauth2-proxy" in check
    assert lines.index(tests) < lines.index(build) < lines.index(check)


def test_licence_d_oauth2_proxy_figee():
    [licence] = [i for i in instructions() if "/licences/oauth2-proxy" in i]
    assert "cp /src/LICENSE /licences/oauth2-proxy/LICENSE" in licence
    assert f"{LICENSE_SHA256}  /licences/oauth2-proxy/LICENSE" in licence
    assert "sha256sum --check --strict" in licence


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
    "ghcr.io/astral-sh/uv:0.12.24-trixie-slim@sha256:"
    "71b09e37995e49a5aed44c63be80d3e15dcb39e18e31c24a2925b66d4b6d5980"
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
def test_version_attendue_compilee_avec_go_1_26_10(image):
    ran = docker("run", "--rm", "--read-only", image, "--version")
    assert ran.returncode == 0, ran.stderr
    assert f"oauth2-proxy {VERSION} (built with go{GO})" in ran.stdout + ran.stderr


@pytest.mark.oauth2proxy
def test_x_net_0_60_0_lie_au_binaire(image):
    """Modules du binaire (go version -m), relevés pour les licences : x/net 0.60.0."""
    modules = files(image)["/licences/modules.txt"].decode().split()
    assert "golang.org/x/net@v0.60.0" in modules
    assert not [m for m in modules if m.startswith("golang.org/x/net@v0.5")]


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

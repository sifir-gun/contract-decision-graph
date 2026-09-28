"""Notices de licence des images publiées (ADR 005, décision du 28/09) : chaque composant
redistribué dans une image porte sa licence. Les images de ghcr.io sont publiques dès leur
première publication.

- Application : AGPL du projet ; licences de HTMX et du corpus ; fichiers `copyright` des
  paquets Debian (distroless) ; licence de CPython et de chaque bibliothèque que
  python-build-standalone y lie (OpenSSL, SQLite, libffi…) ; licence de chaque paquet
  Python, dans sa roue ou, à défaut, dans `licences/paquets/` (texte amont, à la version
  de uv.lock).
- Modèle d'embedding : notice MIT du modèle et de sa conversion ONNX.

Les textes ajoutés au dépôt (`licences/`) et leur provenance sont vérifiés avec la suite ;
le contenu des images construites avec les marqueurs `image` et `modele`.
"""

import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LICENCES = ROOT / "licences"
PROVENANCE = LICENCES / "PROVENANCE.md"
# contenu de python/licenses/ dans les archives complètes de la publication 20260924
# (identique en x86_64 et en aarch64, relevé le 28/09/2026)
PYTHON_LICENCES = {
    f"LICENSE.{name}.txt"
    for name in (
        "bdb",
        "bzip2",
        "cpython",
        "expat",
        "libX11",
        "libXau",
        "libedit",
        "libffi",
        "liblzma",
        "libuuid",
        "libxcb",
        "mpdecimal",
        "ncurses",
        "openssl-1.1",
        "openssl-3",
        "sqlite",
        "tcl",
        "tix",
        "zlib",
    )
}
# paquets dont la roue ne contient aucun texte de licence (inspection du 28/09/2026)
PACKAGES = {
    "flatbuffers": ("google/flatbuffers", "Apache License"),
    "langsmith": ("langchain-ai/langsmith-sdk", "MIT License"),
    "loguru": ("Delgan/loguru", "MIT License"),
    "tokenizers": ("huggingface/tokenizers", "Apache License"),
}


def provenance() -> str:
    return PROVENANCE.read_text(encoding="utf-8")


def locked_version(name: str) -> str:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(rf'^name = "{name}"\nversion = "([^"]+)"', lock, re.MULTILINE)
    assert match, f"{name} absent de uv.lock"
    return match[1]


# --- textes du dépôt et leur provenance ---------------------------------------------------


def test_licences_de_python_build_standalone():
    found = {p.name for p in (LICENCES / "python-build-standalone").iterdir()}
    assert found == PYTHON_LICENCES
    for name in found:
        text = (LICENCES / "python-build-standalone" / name).read_text("utf-8")
        assert text.strip(), name


def test_meme_publication_de_python_que_celle_de_l_image():
    """uv installe dans l'image la publication de python-build-standalone qu'il connaît
    pour la version figée : les licences doivent venir de cette publication-là."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    version = re.search(r"^ARG PYTHON_VERSION=(\S+)$", dockerfile, re.MULTILINE)[1]
    releases = set()
    for arch in ("x86_64", "aarch64"):
        listed = subprocess.run(
            ["uv", "python", "list", f"cpython-{version}-linux-{arch}-gnu"]
            + ["--only-downloads", "--all-platforms", "--output-format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        [entry] = json.loads(listed.stdout)
        releases.add(re.search(r"/download/(\d+)/", entry["url"])[1])
    assert len(releases) == 1
    [release] = releases
    text = provenance()
    assert f"publication `{release}`" in text
    for arch in ("x86_64", "aarch64"):
        archive = f"cpython-{version}+{release}-{arch}-unknown-linux-gnu-pgo+lto-full"
        assert archive in text, archive


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_licence_amont_des_paquets_sans_texte_dans_leur_roue(name):
    repository, header = PACKAGES[name]
    text = (LICENCES / "paquets" / f"{name}-LICENSE.txt").read_text(encoding="utf-8")
    assert header in text[:200]
    # version revue : une mise à jour du paquet fait relire sa licence
    version = locked_version(name)
    assert f"| {name} | {version} | [{repository}]" in provenance()


def test_licences_copiees_dans_l_image():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY licences /app/licences" in dockerfile
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!licences/" in ignore


# --- l'image de l'application (--image ÉTIQUETTE) -----------------------------------------

INSPECT = r"""
import json, os, re, pathlib
site = pathlib.Path("/app/.venv/lib/python3.12/site-packages")
notice = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE)", re.I)
def has_notice(folder, depth):
    if not folder.is_dir():
        return False
    for path in folder.rglob("*"):
        if len(path.relative_to(folder).parts) <= depth and notice.match(path.name):
            return True
    return False
missing = []
for info in sorted(site.glob("*.dist-info")):
    name = info.name.split("-")[0].lower().replace("_", "-")
    tops = set()
    record = info / "RECORD"
    if record.is_file():
        for line in record.read_text().splitlines():
            first = line.split(",")[0].split("/")[0]
            if first and not first.endswith((".dist-info", ".data")):
                tops.add(first)
    covered = has_notice(info, 3) or any(has_notice(site / top, 2) for top in tops)
    covered = covered or pathlib.Path(f"/app/licences/paquets/{name}-LICENSE.txt").is_file()
    if not covered:
        missing.append(info.name)
status = pathlib.Path("/var/lib/dpkg/status.d")
debs = sorted({p.name.split(".")[0] for p in status.iterdir()})
no_copyright = [d for d in debs if not pathlib.Path(f"/usr/share/doc/{d}/copyright").is_file()]
# cpython-3.12-… est un lien vers cpython-3.12.14-… (uv) : chemins résolus
pbs = pathlib.Path("/app/licences/python-build-standalone")
cpython = {p.resolve() for p in pathlib.Path("/python").glob("cpython-*/lib/python3.12/LICENSE.txt")}
print(json.dumps({
    "paquets sans licence": missing,
    "paquets Debian": debs,
    "paquets Debian sans copyright": no_copyright,
    "licence de CPython": [str(p) for p in cpython],
    "licences de python-build-standalone": sorted(
        p.name for p in pbs.iterdir()
    ) if pbs.is_dir() else [],
    "projet": pathlib.Path("/app/LICENSE").read_text()[:200],
    "htmx": pathlib.Path(
        "/app/src/cdg/adapters/web/static/htmx-LICENSE.txt"
    ).is_file(),
    "corpus": pathlib.Path("/app/data/corpus/SOURCES.md").is_file(),
}))
"""


@pytest.fixture(scope="module")
def application(request) -> dict:
    tag = request.config.getoption("--image")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", tag]
        + ["-c", INSPECT],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.mark.image
def test_chaque_paquet_python_porte_sa_licence(application):
    assert application["paquets sans licence"] == []


@pytest.mark.image
def test_chaque_paquet_debian_porte_son_copyright(application):
    assert application["paquets Debian"]  # la liste n'est pas vide : le test voit
    assert application["paquets Debian sans copyright"] == []


@pytest.mark.image
def test_python_et_ses_bibliotheques_liees_portent_leurs_licences(application):
    assert len(application["licence de CPython"]) == 1
    assert set(application["licences de python-build-standalone"]) == PYTHON_LICENCES


@pytest.mark.image
def test_licences_du_projet_de_htmx_et_du_corpus(application):
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in application["projet"]
    assert application["htmx"] and application["corpus"]


# --- l'image du modèle (--modele ÉTIQUETTE) : le même test ---------------------------------


@pytest.mark.modele
def test_l_image_du_modele_porte_sa_notice(request):
    tag = request.config.getoption("--modele")
    created = subprocess.run(
        ["docker", "create", tag, "absent"], capture_output=True, text=True, check=True
    ).stdout.strip()
    try:
        export = subprocess.Popen(["docker", "export", created], stdout=subprocess.PIPE)
        notice = None
        with tarfile.open(fileobj=export.stdout, mode="r|") as archive:
            for member in archive:
                if member.name == "LICENCE-MODELE.md":
                    stream = archive.extractfile(member)
                    assert stream is not None
                    notice = stream.read().decode("utf-8")
        assert export.wait(300) == 0
    finally:
        subprocess.run(["docker", "rm", created], capture_output=True, check=False)
    assert notice is not None, "notice de licence absente de l'image du modèle"
    assert "Copyright (c) Microsoft Corporation" in notice
    assert "Permission is hereby granted, free of charge" in notice
    for source in (
        "intfloat/multilingual-e5-large",
        "Qdrant/multilingual-e5-large-onnx",
    ):
        assert source in notice

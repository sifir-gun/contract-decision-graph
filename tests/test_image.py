"""Image de l'application (phase Kubernetes, point 6 ; ADR 005, « Image »).

Deux familles de tests :
- sur le Dockerfile et le `.dockerignore`, toujours lancés : bases figées par empreinte,
  installation stricte depuis uv.lock, utilisateur non root numérique, aucun outil de
  construction dans l'image finale, contexte de construction réduit à ce que l'image
  utilise (jamais `.env`) ;
- sur l'image construite (marqueur `image`, `--image ÉTIQUETTE`) : lancée en lecture seule,
  sans privilège, elle répond à ses sondes, écrit ses journaux en JSON, s'arrête proprement,
  et n'offre ni shell, ni gestionnaire de paquets, ni pip. Lancés par le job `image` de la CI
  et par `scripts/check.sh`, après `docker build --tag cdg:verification .`.
"""

import json
import re
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PINNED = re.compile(
    r"(?P<name>[^@\s:]+(?::\d+)?/[^@\s:]+):(?P<tag>[^@\s]+)@sha256:[0-9a-f]{64}"
)
NONROOT = "65532:65532"  # utilisateur nonroot des images distroless
ENTRYPOINT = ["python", "-m", "cdg.cli"]
# ce que l'image utilise : le code, sa configuration, ses données, ses migrations, sa licence
CONTEXT = {
    "LICENSE",
    "licences",  # textes des licences tierces (tests/test_licences.py)
    "pyproject.toml",
    "uv.lock",
    "config",
    "data",
    "migrations",
    "src",
}


# --- lecture du Dockerfile ------------------------------------------------------------------


def instructions() -> list[tuple[str, str]]:
    """(instruction, arguments), lignes continuées jointes, commentaires retirés."""
    joined, current = [], ""
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        joined.append(current + stripped)
        current = ""
    assert not current, "Dockerfile : dernière ligne continuée"
    result = []
    for line in joined:
        keyword, _, rest = line.partition(" ")
        result.append((keyword.upper(), " ".join(rest.split())))
    return result


def stages() -> list[list[tuple[str, str]]]:
    found: list[list[tuple[str, str]]] = []
    for keyword, rest in instructions():
        if keyword == "FROM":
            found.append([])
        if found:
            found[-1].append((keyword, rest))
    return found


def ci_uv_version() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    versions = {
        step["with"]["version"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    }
    assert len(versions) == 1, f"versions de uv divergentes dans la CI : {versions}"
    return versions.pop()


def env_of(stage: list[tuple[str, str]]) -> dict[str, str]:
    env = {}
    for keyword, rest in stage:
        if keyword == "ENV":
            for pair in shlex.split(rest):
                name, _, value = pair.partition("=")
                env[name] = value
    return env


# --- le Dockerfile ----------------------------------------------------------------------------


def test_deux_etapes_bases_figees_par_empreinte():
    froms = [rest for keyword, rest in instructions() if keyword == "FROM"]
    assert len(froms) == 2, "deux étapes attendues : construction, exécution"
    for line in froms:
        image = line.split()[0]
        assert PINNED.fullmatch(image), f"base non figée par empreinte : {image}"


def test_uv_de_la_construction_est_celui_de_la_ci():
    builder = stages()[0][0][1].split()[0]
    match = PINNED.fullmatch(builder)
    assert match and match["name"] == "ghcr.io/astral-sh/uv"
    assert match["tag"].split("-")[0] == ci_uv_version()


def test_image_finale_distroless_sans_shell():
    final = stages()[-1][0][1].split()[0]
    match = PINNED.fullmatch(final)
    assert match and match["name"] == "gcr.io/distroless/cc-debian13"
    assert match["tag"] == "nonroot"
    # distroless n'a pas de shell : aucune instruction RUN possible dans l'étape finale
    assert not [rest for keyword, rest in stages()[-1] if keyword == "RUN"]


def test_aucun_frontal_de_construction_non_fige():
    # une directive « # syntax= » tirerait une image de frontal à chaque construction
    first = DOCKERFILE.read_text(encoding="utf-8").splitlines()[0]
    assert not first.replace(" ", "").lower().startswith("#syntax=")


def test_python_fige_dans_la_version_du_projet():
    args = [rest for keyword, rest in instructions() if keyword == "ARG"]
    [version] = [a.split("=", 1)[1] for a in args if a.startswith("PYTHON_VERSION=")]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'requires-python = "==3.12.*"' in lock
    assert re.fullmatch(r"3\.12\.\d+", version), version


def test_installation_stricte_depuis_uv_lock_sans_rien_construire():
    syncs = [
        rest for keyword, rest in stages()[0] if keyword == "RUN" and "uv sync" in rest
    ]
    assert syncs, "aucun uv sync dans l'étape de construction"
    for command in syncs:
        options = set(command.split("uv sync", 1)[1].split())
        # --locked : uv.lock tel quel ; --no-dev : sans le groupe dev ; --no-build : aucun
        # paquet construit depuis ses sources ; --no-install-project : le projet n'est
        # pas construit non plus (hatchling ne serait pas figé par uv.lock)
        assert {"--locked", "--no-dev", "--no-build", "--no-install-project"} <= options


def test_image_finale_non_root_numerique_et_lancement_direct():
    final = stages()[-1]
    users = [rest for keyword, rest in final if keyword == "USER"]
    assert users == [NONROOT]
    [entrypoint] = [rest for keyword, rest in final if keyword == "ENTRYPOINT"]
    assert json.loads(entrypoint) == ENTRYPOINT  # forme exec : Python reçoit SIGTERM


def test_image_finale_ne_copie_que_python_et_l_application():
    copies = [rest for keyword, rest in stages()[-1] if keyword == "COPY"]
    sources = sorted(c.split()[1] for c in copies if c.startswith("--from="))
    assert sources == ["/app", "/python"], "ni uv, ni cache, ni outil de construction"
    assert all(c.startswith("--from=") for c in copies)


def test_environnement_de_l_image_finale():
    env = env_of(stages()[-1])
    assert env["CDG_JOURNAUX"] == "json"
    assert env["HF_HUB_OFFLINE"] == "1"  # aucun téléchargement du modèle au démarrage
    assert env["ORT_DISABLE_TELEMETRY"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"  # système de fichiers en lecture seule
    assert env["PYTHONPATH"] == "/app/src"
    assert env["PATH"].startswith("/app/.venv/bin:")


def test_etiquettes_oci_de_l_image():
    labels = {}
    for keyword, rest in stages()[-1]:
        if keyword == "LABEL":
            for pair in shlex.split(rest):
                name, _, value = pair.partition("=")
                labels[name] = value
    # la source relie le paquet ghcr.io au dépôt (droits, page du paquet, Dependabot)
    assert labels["org.opencontainers.image.source"] == (
        "https://github.com/sifir-gun/contract-decision-graph"
    )
    assert labels["org.opencontainers.image.licenses"] == "AGPL-3.0-only"
    assert labels["org.opencontainers.image.title"] == "contract-decision-graph"


def test_contexte_de_construction_limite_a_ce_que_l_image_utilise():
    lines = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines[0] == "*", "tout exclure d'abord, puis admettre"
    admitted = {line[1:].strip("/") for line in lines if line.startswith("!")}
    assert admitted == CONTEXT
    for name in admitted:
        assert (ROOT / name).exists(), name
    assert ".env" not in admitted and not any(".env" in line for line in lines[1:])


# --- l'image construite (--image ÉTIQUETTE) -----------------------------------------------

HARDENED = [
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
]
WAIT = 90


@pytest.fixture(scope="module")
def image(request) -> str:
    tag = request.config.getoption("--image")
    found = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, text=True, check=False
    )
    assert found.returncode == 0, (
        f"image introuvable : {tag} (docker build --tag {tag} .)"
    )
    return tag


def docker(*args: str, timeout: float = WAIT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def python_in(image: str, code: str, *flags: str) -> subprocess.CompletedProcess[str]:
    return docker("run", "--rm", *flags, "--entrypoint", "python", image, "-c", code)


@pytest.mark.image
def test_configuration_de_l_image(image):
    [config] = json.loads(docker("image", "inspect", image).stdout)
    assert config["Config"]["User"] == NONROOT
    assert config["Config"]["Entrypoint"] == ENTRYPOINT
    labels = config["Config"]["Labels"]
    assert labels["org.opencontainers.image.source"].endswith(
        "/contract-decision-graph"
    )
    names = [e.split("=", 1)[0] for e in config["Config"]["Env"]]
    assert not [n for n in names if re.search("KEY|PASSWORD|SECRET|TOKEN", n)]


@pytest.mark.image
def test_serveur_factice_de_mistral_absent_de_l_image(image):
    """Le serveur factice des tests du cluster a son image de test à part (ADR 005, PR C3) :
    ni module importable, ni fichier, dans l'image de l'application."""
    code = (
        "import importlib.util, os, sys\n"
        "module = importlib.util.find_spec('mistral_factice')\n"
        "files = [os.path.join(d, f) for d, _, fs in os.walk('/') for f in fs\n"
        "         if f.startswith('mistral_factice') and not d.startswith(('/proc', '/sys'))]\n"
        "print(module, files)\n"
        "sys.exit(1 if module or files else 0)\n"
    )
    result = python_in(image, code)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.image
def test_image_de_test_du_serveur_factice_repond(image):
    """Construite sur l'image de l'application, elle n'y ajoute que le serveur factice."""
    tag = "cdg-mistral-factice:verification"
    built = docker(
        "build",
        "--file",
        "docker/mistral-factice/Dockerfile",
        "--build-arg",
        f"APPLICATION={image}",
        "--tag",
        tag,
        str(ROOT),
        timeout=300,
    )
    assert built.returncode == 0, built.stderr
    started = docker("run", "--detach", *HARDENED, "--publish", "127.0.0.1::8080", tag)
    assert started.returncode == 0, started.stderr
    container = started.stdout.strip()
    try:
        address = published(container, 8080)
        deadline = time.monotonic() + WAIT
        body = None
        while time.monotonic() < deadline and body is None:
            try:
                with urllib.request.urlopen(
                    f"{address}/controle", timeout=2
                ) as response:
                    body = json.load(response)
            except OSError:
                time.sleep(0.5)
        assert body == {"delai": 0.0, "recues": 0, "appels": {}, "refus": 0}, docker(
            "logs", container
        ).stdout
    finally:
        docker("rm", "--force", container)


@pytest.mark.image
@pytest.mark.parametrize("tool", ["sh", "bash", "apt-get", "dpkg", "uv", "pip", "pip3"])
def test_aucun_shell_ni_outil_dans_l_image(image, tool):
    result = docker("run", "--rm", "--entrypoint", tool, image)
    assert result.returncode == 127, (tool, result.stderr)  # exécutable introuvable


@pytest.mark.image
def test_python_de_l_image_sans_pip_ni_outils_de_developpement(image):
    code = (
        "import importlib.util, json, os, platform; print(json.dumps({"
        "'version': platform.python_version(), 'uid': os.getuid(), 'gid': os.getgid(), "
        "'presents': [m for m in ('pip', 'ensurepip', 'idlelib', 'tkinter', 'lib2to3') "
        "if importlib.util.find_spec(m)]}))"
    )
    result = python_in(image, code, *HARDENED)
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    [arg] = [
        r for k, r in instructions() if k == "ARG" and r.startswith("PYTHON_VERSION=")
    ]
    assert seen["version"] == arg.split("=", 1)[1]
    assert (seen["uid"], seen["gid"]) == (65532, 65532)
    assert seen["presents"] == []


@pytest.mark.image
def test_dependances_natives_chargees_en_lecture_seule(image):
    code = """
import json
import fastembed, numpy, onnxruntime, tokenizers
import psycopg, pgvector.psycopg, psycopg_pool
import fastapi, jinja2, langgraph, uvicorn
import cdg.cli
from cdg.domain.config import DEFAULT_CONFIG_PATH, load_config
load_config()
print(json.dumps({
    "libpq": psycopg.pq.version(),
    "implementation": psycopg.pq.__impl__,
    "configuration": str(DEFAULT_CONFIG_PATH),
}))
"""
    result = python_in(image, code, *HARDENED)
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["implementation"] == "binary" and seen["libpq"] > 0
    assert seen["configuration"] == "/app/config/decision.yaml"


@pytest.mark.image
def test_code_et_python_non_modifiables_par_le_processus(image):
    # sans --read-only : c'est la propriété des fichiers qui protège le code
    code = """
import json
refused = []
for path in ("/app/src/cdg/essai", "/app/.venv/essai", "/app/config/essai", "/python/essai"):
    try:
        open(path, "w").close()
    except PermissionError:
        refused.append(path)
print(json.dumps(refused))
"""
    result = python_in(image, code)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 4


def published(container: str, port: int) -> str:
    mapping = docker("port", container, f"{port}/tcp").stdout.splitlines()[0]
    return f"http://{mapping.strip()}"


@pytest.mark.image
def test_demonstration_en_lecture_seule_sondes_journaux_json_arret_propre(image):
    import httpx

    started = docker(
        "run",
        "--detach",
        *HARDENED,
        "--publish",
        "127.0.0.1::8081",
        "--publish",
        "127.0.0.1::8000",
        image,
        "web",
        "--demo",
        "--port-sante",
        "8081",
        "--hote-sante",
        "0.0.0.0",
    )
    assert started.returncode == 0, started.stderr
    container = started.stdout.strip()
    try:
        probes = published(container, 8081)
        deadline = time.monotonic() + WAIT
        status = None
        while time.monotonic() < deadline:
            try:
                status = httpx.get(f"{probes}/sante/pret", timeout=2).status_code
            except httpx.TransportError:
                status = None
            if status == 200:
                break
            time.sleep(0.5)
        assert status == 200, docker("logs", container).stderr
        assert httpx.get(f"{probes}/sante/vie", timeout=2).status_code == 200
        # l'interface n'écoute que sur 127.0.0.1 dans le conteneur : même publiée, rien
        with pytest.raises(httpx.TransportError):
            httpx.get(published(container, 8000), timeout=2)
        stopped = docker("stop", "--time", "30", container)
        assert stopped.returncode == 0, stopped.stderr
        state = json.loads(docker("inspect", container).stdout)[0]["State"]
        assert state["ExitCode"] == 0, docker("logs", container).stderr
        logs = docker("logs", container)
        assert logs.stderr == ""  # ni avertissement, ni erreur hors des journaux
        # une ligne JSON par entrée, puis le résultat de la commande, sur une ligne
        *entries, result = [json.loads(line) for line in logs.stdout.splitlines()]
        assert result == {"web": "arrêtée"}
        assert entries and all(
            {"horodatage", "niveau", "journal"} <= set(e) for e in entries
        )
        assert not [e for e in entries if e["niveau"] in {"ERROR", "CRITICAL"}]
    finally:
        docker("rm", "--force", container)

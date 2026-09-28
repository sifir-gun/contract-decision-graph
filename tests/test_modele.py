"""Image du modèle d'embedding (phase Kubernetes, PR B ; ADR 005).

Les poids ne sont jamais téléchargés au démarrage d'un pod : une image dédiée les porte, sans
système ni outil, montée en lecture seule à EMBEDDING_CACHE_DIR (volume `image`, PR C).
Elle est construite par un workflow à part (`.github/workflows/modele.yml`) : à la demande
pour la publier, et sur chaque pull request qui la touche pour la vérifier. Le contenu est
figé par les empreintes de ses fichiers (`docker/modele/empreintes.sha256`), pas par la
révision du dépôt Hugging Face, qui change sans toucher aux poids (README, 24/09/2026).

Deux familles de tests : sur le manifeste, le script, le Dockerfile et le workflow, toujours
lancés ; sur l'image construite (marqueur `modele`, `--modele ÉTIQUETTE` et `--image`).
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from cdg.adapters.fastembed import flat_dir
from cdg.domain.config import load_config

ROOT = Path(__file__).resolve().parents[1]
MODELE = ROOT / "docker" / "modele"
WORKFLOW = ROOT / ".github" / "workflows" / "modele.yml"
FILES = {
    "config.json",
    "model.onnx",
    "model.onnx_data",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
}


def modele():
    path = ROOT / "scripts" / "modele.py"
    spec = importlib.util.spec_from_file_location("modele", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- manifeste et vérification ----------------------------------------------------------------


def test_manifeste_des_fichiers_que_charge_l_application():
    flat = flat_dir(load_config().embedding, Path("."))
    entries = modele().expected(MODELE / "empreintes.sha256")
    assert {Path(path).parent for path in entries} == {flat}
    assert {Path(path).name for path in entries} == FILES
    assert all(len(digest) == 64 for digest in entries.values())


def fake_cache(tmp_path: Path) -> tuple[Path, Path]:
    """Un cache minuscule et son manifeste, pour tester la vérification."""
    flat = tmp_path / "cache" / "flat" / "modele"
    flat.mkdir(parents=True)
    lines = []
    for name, content in (("a.json", b"{}"), ("b.onnx", b"poids")):
        (flat / name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{digest}  flat/modele/{name}")
    manifest = tmp_path / "empreintes.sha256"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path / "cache", manifest


def test_verification_du_cache_conforme(tmp_path):
    cache, manifest = fake_cache(tmp_path)
    assert modele().verify(cache, manifest) == 0


@pytest.mark.parametrize("change", ["modifie", "absent", "en_trop"])
def test_verification_refuse_un_cache_different(tmp_path, capsys, change):
    cache, manifest = fake_cache(tmp_path)
    flat = cache / "flat" / "modele"
    if change == "modifie":
        (flat / "b.onnx").write_bytes(b"autres poids")
    elif change == "absent":
        (flat / "a.json").unlink()
    else:
        (flat / "c.bin").write_bytes(b"?")
    assert modele().verify(cache, manifest) == 1
    err = capsys.readouterr().err
    assert {"modifie": "b.onnx", "absent": "a.json", "en_trop": "c.bin"}[change] in err


# --- Dockerfile de l'image du modèle -------------------------------------------------------------


def dockerfile() -> list[str]:
    lines, current = [], ""
    for line in (MODELE / "Dockerfile").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        lines.append(" ".join((current + stripped).split()))
        current = ""
    return lines


def test_image_du_modele_sans_systeme_ni_outil():
    lines = dockerfile()
    assert [line for line in lines if line.startswith("FROM")] == ["FROM scratch"]
    assert not [line for line in lines if line.startswith("RUN")]
    assert "COPY . /" in lines  # le cache vérifié, tel quel (liens physiques compris)
    assert "COPY --from=notice LICENCE-MODELE.md /LICENCE-MODELE.md" in lines
    labels = " ".join(line for line in lines if line.startswith("LABEL"))
    assert 'org.opencontainers.image.licenses="MIT"' in labels
    assert "org.opencontainers.image.source=" in labels


def test_artefacts_du_telechargement_hors_de_l_image():
    # BuildKit lit le fichier d'exclusion propre au Dockerfile (Dockerfile.dockerignore) :
    # les verrous et la liste des fichiers du dépôt (trees/, écrite en 0600, illisible par
    # l'utilisateur de l'application) ne servent qu'au téléchargement
    ignored = (MODELE / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    patterns = [
        line.strip()
        for line in ignored.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert patterns == [".locks/", "models--*/trees/"]


def test_notice_de_licence_du_modele():
    notice = (MODELE / "LICENCE-MODELE.md").read_text(encoding="utf-8")
    for source in (
        "https://huggingface.co/intfloat/multilingual-e5-large",
        "https://huggingface.co/Qdrant/multilingual-e5-large-onnx",
        "https://github.com/microsoft/unilm",
    ):
        assert source in notice
    assert "Copyright (c) Microsoft Corporation" in notice
    assert "Permission is hereby granted, free of charge" in notice


# --- workflow à part ---------------------------------------------------------------------------

FETCH = "uv run --no-sync python -m cdg.cli fetch-embedding-model"
VERIFY = 'uv run --no-sync python scripts/modele.py verifier "$RUNNER_TEMP/modele"'
BUILD_MODEL = (
    "docker build --file docker/modele/Dockerfile --build-context notice=docker/modele "
    '--tag cdg-modele:verification "$RUNNER_TEMP/modele"'
)
BUILD_APP = "docker build --tag cdg:verification ."
TESTS = (
    "uv run --no-sync pytest -m modele --image cdg:verification "
    "--modele cdg-modele:verification"
)


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def runs(job: str) -> list[str]:
    steps = workflow()["jobs"][job]["steps"]
    return [" ".join(s["run"].split()) for s in steps if "run" in s]


def test_workflow_a_la_demande_et_sur_les_pull_requests_qui_le_touchent():
    # PyYAML lit la clé « on » comme le booléen True
    triggers = workflow()[True]
    assert set(triggers) == {"workflow_dispatch", "pull_request"}
    paths = set(triggers["pull_request"]["paths"])
    assert {
        "docker/modele/**",
        "scripts/modele.py",
        ".github/workflows/modele.yml",
    } <= paths
    # ce qui décide du chargement des poids : l'adaptateur, l'image de l'application, ses
    # dépendances (fastembed, onnxruntime), la configuration du modèle, et ce test lui-même
    assert {
        "src/cdg/adapters/fastembed.py",
        "Dockerfile",
        "pyproject.toml",
        "uv.lock",
        "config/decision.yaml",
        "tests/test_modele.py",
    } <= paths
    assert workflow()["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("job", ["verification", "publication"])
def test_telechargement_verifie_puis_image_testee(job):
    steps = runs(job)
    order = [FETCH, VERIFY, BUILD_MODEL, BUILD_APP, TESTS]
    positions = [steps.index(command) for command in order]
    assert positions == sorted(positions)
    [fetch] = [s for s in workflow()["jobs"][job]["steps"] if s.get("run") == FETCH]
    assert fetch["env"]["EMBEDDING_CACHE_DIR"] == "${{ runner.temp }}/modele"
    # la seule exception à « aucun téléchargement du modèle en CI » (plan validé)
    assert "HF_HUB_OFFLINE" not in fetch.get("env", {})


def test_verification_sur_pull_request_sans_aucun_droit_d_ecriture():
    job = workflow()["jobs"]["verification"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert "permissions" not in job


def test_publication_a_la_demande_depuis_main_signee_et_attestee():
    job = workflow()["jobs"]["publication"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'"
    )
    assert job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert job["env"]["IMAGE"] == "ghcr.io/${{ github.repository }}/modele-embedding"
    text = "\n".join(runs("publication"))
    assert 'cosign sign --yes "$IMAGE@$DIGEST"' in text
    assert "cosign verify" in text and "gh attestation verify" in text
    [installer] = [
        s
        for s in job["steps"]
        if s.get("uses", "").startswith("sigstore/cosign-installer@")
    ]
    assert installer["with"]["cosign-release"] == "v3.1.3"  # celui de scripts/chaine.py
    [attest] = [
        s for s in job["steps"] if s.get("uses", "").startswith("actions/attest@")
    ]
    assert attest["with"]["push-to-registry"] is True
    assert attest["with"]["create-storage-record"] is False
    for step in job["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_meme_construction_de_l_application_qu_en_ci():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"run: {BUILD_APP}" in ci
    uv = {
        s["with"]["version"]
        for s in workflow()["jobs"]["verification"]["steps"]
        if s.get("uses", "").startswith("astral-sh/setup-uv@")
    }
    assert uv == {"0.12.19"}


# --- l'image construite (--modele ÉTIQUETTE, --image ÉTIQUETTE) ----------------------------

WAIT = 300
# ajoutés par `docker export` (conteneur créé), absents de l'image
RUNTIME = (".dockerenv", "dev/", "etc/", "proc/")


@pytest.fixture(scope="module")
def images(request) -> tuple[str, str]:
    app, weights = (
        request.config.getoption("--image"),
        request.config.getoption("--modele"),
    )
    assert app, "--image ÉTIQUETTE requis : l'image de l'application charge les poids"
    for tag in (app, weights):
        found = subprocess.run(
            ["docker", "image", "inspect", tag], capture_output=True, check=False
        )
        assert found.returncode == 0, f"image introuvable : {tag}"
    return app, weights


@pytest.fixture(scope="module")
def extracted(images, tmp_path_factory) -> Path:
    """Contenu de l'image du modèle, extrait tel quel (liens physiques compris) : ce que
    le pod voit dans son volume `image`, monté en lecture seule."""
    _, weights = images
    target = tmp_path_factory.mktemp("modele")
    created = subprocess.run(
        ["docker", "create", weights, "absent"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    try:
        export = subprocess.Popen(["docker", "export", created], stdout=subprocess.PIPE)
        subprocess.run(
            ["tar", "-xpf", "-", "-C", str(target)], stdin=export.stdout, check=True
        )
        assert export.wait(WAIT) == 0
    finally:
        subprocess.run(["docker", "rm", created], capture_output=True, check=False)
    return target


@pytest.fixture(scope="module")
def container(images) -> Iterator[str]:
    """Un conteneur créé (jamais lancé) sur l'image du modèle, pour en exporter le
    contenu."""
    _, weights = images
    created = subprocess.run(
        ["docker", "create", weights, "absent"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    yield created
    subprocess.run(["docker", "rm", created], capture_output=True, check=False)


EXTRACT = f"""
import sys, tarfile
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
    for member in archive:
        if not member.name.startswith({RUNTIME!r}):
            archive.extract(member, "/modele", filter="tar")
"""


@pytest.fixture(scope="module")
def volume(images, container) -> Iterator[str]:
    """Le contenu de l'image du modèle dans un volume Docker, extrait par root là où
    tournent les conteneurs : propriétaire, droits et liens physiques de l'image, comme
    dans le volume `image` d'un pod. Un dossier de l'hôte ne vaut pas : pytest crée les
    siens en 0700, et Docker Desktop n'applique pas les droits des dossiers partagés
    (premier essai en CI, run 36397656485 : poids introuvables sous l'uid 65532)."""
    app, _ = images
    name = f"cdg-modele-test-{uuid.uuid4().hex[:12]}"
    try:
        export = subprocess.Popen(
            ["docker", "export", container], stdout=subprocess.PIPE
        )
        extract = subprocess.run(
            ["docker", "run", "--rm", "-i", "--user", "0", "--network", "none"]
            + ["-v", f"{name}:/modele", "--entrypoint", "python", app, "-c", EXTRACT],
            stdin=export.stdout,
            capture_output=True,
            text=True,
            timeout=WAIT,
            check=False,
        )
        assert export.wait(WAIT) == 0
        assert extract.returncode == 0, extract.stderr[-2000:]
        yield name
    finally:
        subprocess.run(
            ["docker", "volume", "rm", "-f", name], capture_output=True, check=False
        )


@pytest.mark.modele
def test_contenu_lisible_par_l_utilisateur_de_l_application(container):
    # dans le pod, les fichiers de l'image appartiennent à root ; l'application tourne
    # sous 65532 : tout doit être lisible, et chaque dossier traversable, par les autres
    refused = []
    export = subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE)
    with tarfile.open(fileobj=export.stdout, mode="r|") as archive:
        for member in archive:
            if member.name.startswith(RUNTIME):
                continue
            directory = member.isdir() and member.mode & 0o005 != 0o005
            if directory or (member.isfile() and not member.mode & 0o004):
                refused.append(f"{member.name} {oct(member.mode)}")
    assert export.wait(WAIT) == 0
    assert refused == []


@pytest.mark.modele
def test_image_du_modele_conforme_au_manifeste(extracted):
    assert modele().verify(extracted, MODELE / "empreintes.sha256") == 0
    assert (extracted / "LICENCE-MODELE.md").is_file()


@pytest.mark.modele
def test_liens_physiques_conserves_dans_l_image(extracted):
    # l'adaptateur charge les poids par une copie à plat en liens physiques : sans eux,
    # il tenterait de les refaire, sur un volume en lecture seule
    flat = flat_dir(load_config().embedding, extracted)
    for name in ("model.onnx", "model.onnx_data"):
        assert os.stat(flat / name).st_nlink >= 2, name


@pytest.mark.modele
def test_l_application_charge_les_poids_sans_reseau_ni_ecriture(images, volume):
    app, _ = images
    code = """
import json
from pathlib import Path
from cdg.adapters.fastembed import FastembedEmbedder
from cdg.domain.config import load_config
embedder = FastembedEmbedder(load_config().embedding, Path("/modele"))
print(json.dumps({"dimension": len(embedder.embed_query("clause de responsabilité"))}))
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",  # ni téléchargement, ni télémétrie : rien ne sort
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "-v",
            f"{volume}:/modele:ro",
            "-e",
            "EMBEDDING_CACHE_DIR=/modele",
            "--entrypoint",
            "python",
            app,
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=WAIT,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout.splitlines()[-1]) == {"dimension": 1024}

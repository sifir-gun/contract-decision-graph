"""CI : la base PostgreSQL + pgvector des tests est celle de docker-compose.yml.

L'image est mise à jour à la main, délibérément, sans Dependabot (décision du 26/09) : ce
test échoue si l'empreinte diverge entre docker-compose.yml et le workflow.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PINNED = re.compile(r"(?P<name>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})")


def _load(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def images() -> dict[str, str]:
    compose = _load("docker-compose.yml")["services"]["db"]["image"]
    workflow = _load(".github/workflows/ci.yml")["jobs"]["tests"]["services"]
    return {"docker-compose.yml": compose, "ci.yml": workflow["postgres"]["image"]}


def test_image_postgres_figee_par_empreinte():
    for source, image in images().items():
        assert PINNED.fullmatch(image), (
            f"{source} : image non figée par empreinte : {image}"
        )


def test_meme_empreinte_en_local_et_en_ci():
    pinned = {source: PINNED.fullmatch(image) for source, image in images().items()}
    digests = {source: match["digest"] for source, match in pinned.items() if match}
    names = {source: match["name"] for source, match in pinned.items() if match}
    assert len(set(digests.values())) == 1, f"empreintes divergentes : {digests}"
    assert len(set(names.values())) == 1, f"images divergentes : {names}"


# --- scripts/check.sh : mêmes vérifications que la CI (décision du 26/09) ------------------

SCRIPT = ROOT / "scripts" / "check.sh"


def _one_line(command: str) -> str:
    return " ".join(command.split())


def ci_commands() -> list[str]:
    """Commandes de vérification du workflow, dans l'ordre des jobs. Hors comparaison :
    l'installation, propre à chaque job (`uv sync`), et la migration dans le conteneur de
    service (`docker`)."""
    jobs = _load(".github/workflows/ci.yml")["jobs"]
    commands = []
    for job in jobs.values():
        for step in job["steps"]:
            run = step.get("run")
            if run is None:
                continue
            command = _one_line(run)
            if not command.startswith(("uv sync", "docker")):
                commands.append(command)
    return commands


def script_commands() -> list[str]:
    lines = [line.strip() for line in SCRIPT.read_text(encoding="utf-8").splitlines()]
    return [
        _one_line(line)
        for line in lines
        if line.startswith("uv ") and not line.startswith("uv sync")
    ]


def test_check_sh_lance_exactement_les_commandes_de_la_ci():
    assert ci_commands(), "aucune commande lue dans le workflow : test à revoir"
    assert script_commands() == ci_commands(), (
        "scripts/check.sh diverge de .github/workflows/ci.yml : aligner le script"
    )


def test_check_sh_executable_et_arrete_au_premier_echec():
    text = SCRIPT.read_text(encoding="utf-8")
    assert SCRIPT.stat().st_mode & 0o111, "scripts/check.sh doit être exécutable"
    assert "set -euo pipefail" in text
    # une seule installation, tous groupes, depuis uv.lock
    assert [line for line in text.splitlines() if line.startswith("uv sync")] == [
        "uv sync --locked --all-groups"
    ]

"""Chaîne d'approvisionnement de l'image (phase Kubernetes, PR B ; ADR 005).

- Dependabot propose les mises à jour des images de base du Dockerfile, qui passent par la
  CI ; uv et l'image PostgreSQL restent mis à jour à la main.
- Toute action des workflows est épinglée par empreinte de commit, version en commentaire.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED_ACTION = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w./-]+)@(?P<sha>[0-9a-f]{40})\s+#\s+v\d+(\.\d+)*$"
)


def dependabot() -> list[dict]:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["updates"]


# --- Dependabot ---------------------------------------------------------------------------


def test_dependabot_suit_les_images_de_base_du_dockerfile():
    [docker] = [u for u in dependabot() if u["package-ecosystem"] == "docker"]
    assert docker["directory"] == "/"
    others = [u for u in dependabot() if u["package-ecosystem"] != "docker"]
    assert all(docker["schedule"] == u["schedule"] for u in others)  # lundi, 6 h
    assert docker["commit-message"]["prefix"] == "Image : "


def test_uv_reste_mis_a_jour_a_la_main():
    # uv : celui du poste, de la CI et de l'étape de construction, ensemble
    # (tests/test_image.py) ; l'étape de construction ne part pas dans l'image finale
    [docker] = [u for u in dependabot() if u["package-ecosystem"] == "docker"]
    assert {"dependency-name": "astral-sh/uv"} in docker["ignore"]


def test_image_postgres_hors_de_dependabot():
    # l'écosystème docker lit les Dockerfile et les manifestes Kubernetes (YAML avec
    # apiVersion et kind, dependabot-core) : docker-compose.yml n'en est pas un, et
    # l'écosystème docker-compose n'est pas configuré
    ecosystems = {u["package-ecosystem"] for u in dependabot()}
    assert "docker-compose" not in ecosystems
    for path in ROOT.glob("*.y*ml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not {"apiVersion", "kind"} <= set(document), path.name


# --- actions épinglées ------------------------------------------------------------------------


def test_actions_epinglees_par_empreinte_version_en_commentaire():
    assert WORKFLOWS
    for workflow in WORKFLOWS:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(("uses:", "- uses:")):
                used = line.strip().removeprefix("- ")
                assert PINNED_ACTION.fullmatch(used), f"{workflow.name} : {used}"

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

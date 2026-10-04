"""Exception aux signatures (ADR 008, décision du 03/10) : les images de la composition de
Langfuse (web, worker, ClickHouse, Valkey) ne publient ni signature ni attestation. Elles
ne servent qu'aux tests, sur le poste (`compose.observabilite.yaml`) et dans le cluster
de test de la CI (`cluster/langfuse.yaml`). L'exception les nomme une à une, par empreinte,
justifiée, datée, 90 jours au plus ; elle ne couvre jamais une image du produit.
"""

import re
from datetime import date
from pathlib import Path

import pytest
import yaml
from test_chaine_approvisionnement import chaine
from test_cluster_outillage import cluster

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 3)
LANGFUSE_IMAGES = {"langfuse-web", "langfuse-worker", "langfuse-clickhouse"}


def repository(image: str) -> str:
    """Dépôt d'une image, sans étiquette ni empreinte, ni préfixe implicite."""
    name = image.split("@", 1)[0]
    if ":" in name.rsplit("/", 1)[-1]:
        name = name.rsplit(":", 1)[0]
    return name.removeprefix("docker.io/").removeprefix("library/")


def product_repositories() -> set[str]:
    """Dépôts des images du produit : charts, bases des Dockerfile du projet."""
    found: set[str] = set()
    for values in ROOT.glob("chart/*/values.yaml"):
        text = values.read_text(encoding="utf-8")
        found |= set(re.findall(r"repository:\s*(\S+)", text))
    for dockerfile in [ROOT / "Dockerfile", *ROOT.glob("docker/*/Dockerfile")]:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM ") and "/" in line:
                found.add(line.split()[1])
    return {repository(image) for image in found}


def exceptions() -> dict[str, str]:
    module = chaine()
    return module.signature_exceptions(module.SIGNATURE_EXCEPTIONS, TODAY)


def test_exceptions_justifiees_datees_limitees_aux_tests():
    entries = yaml.safe_load(
        (ROOT / "securite" / "exceptions-signatures.yaml").read_text(encoding="utf-8")
    )["exceptions"]
    assert {e["portee"] for e in entries} == {"tests"}
    assert set(exceptions()) == {e["image"] for e in entries}
    assert len(entries) == 4


def test_exactement_les_images_de_la_composition_de_langfuse():
    """Chaque image de Langfuse, de ClickHouse et de Valkey du poste et du cluster est
    couverte ; rien d'autre. PostgreSQL et SeaweedFS sont ceux du projet."""
    compose = yaml.safe_load(
        (ROOT / "compose.observabilite.yaml").read_text(encoding="utf-8")
    )["services"]
    used = {s["image"] for n, s in compose.items() if n in LANGFUSE_IMAGES}
    used.add(compose["langfuse-valkey"]["image"])
    assert used == set(exceptions())
    unsigned = {k: v for k, v in cluster().LANGFUSE_IMAGES.items() if k != "POSTGRES"}
    assert set(unsigned.values()) == set(exceptions())


def test_jamais_une_image_du_produit():
    product = product_repositories()
    assert "ghcr.io/sifir-gun/contract-decision-graph" in product
    assert "gcr.io/distroless/cc-debian13" in product
    for image in exceptions():
        assert repository(image) not in product, image
        assert not image.startswith("ghcr.io/sifir-gun/"), image


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda e: e.update(portee="production"), "tests"),
        (lambda e: e.update(motif=" "), "motif"),
        (lambda e: e.update(image="docker.io/langfuse/langfuse:4.50.0"), "empreinte"),
        (lambda e: e.update(expire=date(2027, 3, 1)), "90 jours"),
        (
            lambda e: e.update(decidee=date(2026, 9, 1), expire=date(2026, 10, 1)),
            "expirée",
        ),
        (lambda e: e.update(decidee=date(2026, 10, 10)), "à venir"),
        (lambda e: e.update(expire=date(2026, 9, 1)), "avant sa décision"),
    ],
)
def test_exception_mal_formee_refusee(tmp_path, change, message):
    path = ROOT / "securite" / "exceptions-signatures.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data["exceptions"][0])
    written = tmp_path / "exceptions.yaml"
    written.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        chaine().signature_exceptions(written, TODAY)

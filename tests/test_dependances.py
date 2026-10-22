"""Dépendances forcées (`[tool.uv] override-dependencies` de pyproject.toml) : chacune
porte une version corrigée qu'une bibliothèque refuse encore par sa borne déclarée, et
disparaît dès que plus aucune ne la refuse.

21/10/2026 : pillow 13.0.0, seule version corrigée de huit failles (CVE-2026-112395 et
suivantes), alors que fastembed 0.8.1 déclare `pillow<13.0` (`docs/journal.md`).
"""

import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
OVERRIDES = PROJECT["tool"]["uv"].get("override-dependencies", [])


def locked_version(name: str) -> Version:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    [version] = [p["version"] for p in lock["package"] if p["name"] == name]
    return Version(version)


def refusals(name: str, version: Version) -> list[str]:
    """Paquets installés dont une exigence sur `name`, applicable ici (marqueurs évalués
    pour cet interpréteur, sans extra), exclut `version`."""
    found = []
    for dist in metadata.distributions():
        for line in dist.requires or []:
            req = Requirement(line)
            if canonicalize_name(req.name) != canonicalize_name(name):
                continue
            if req.marker is not None and not req.marker.evaluate():
                continue
            if not req.specifier.contains(version, prereleases=True):
                found.append(f"{dist.metadata['Name']} {dist.version} : {line}")
    return found


def test_pillow_verrouillee_a_une_version_corrigee():
    """Sans réseau, en complément de pip-audit : avis du 21/10/2026, corrigés en 13.0.0
    seulement."""
    assert locked_version("pillow") >= Version("13.0.0")


@pytest.mark.parametrize("override", OVERRIDES)
def test_chaque_override_sert_encore(override):
    """Un override qu'aucun paquet installé ne contredit est inutile : le retirer de
    pyproject.toml, puis `uv lock`. Exemple : une version de fastembed qui admet pillow
    13."""
    req = Requirement(override)
    installed = Version(metadata.version(req.name))
    assert installed == locked_version(req.name), (
        f"{req.name} {installed} installé, {locked_version(req.name)} verrouillé : "
        "environnement à synchroniser (uv sync)"
    )
    assert req.specifier.contains(installed, prereleases=True)
    assert refusals(req.name, installed), (
        f"override {override!r} inutile : aucun paquet installé ne refuse "
        f"{req.name} {installed} ; le retirer de pyproject.toml, puis uv lock"
    )

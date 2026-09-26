"""Couverture : le badge du README affiche le seuil appliqué par la CI, jamais une autre valeur.

Le badge est statique (aucun service tiers ni droit d'écriture pour la CI) : ce test échoue
dès qu'il diverge de `fail_under`, lu par pytest-cov dans `pyproject.toml`.
"""

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
BADGE = re.compile(r"https://img\.shields\.io/badge/couverture-([^-)]+)-[a-z]+\)")


def fail_under() -> float:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["coverage"]["report"]["fail_under"]


def test_seuil_de_couverture_configure():
    assert 0 < fail_under() <= 100


def test_badge_du_readme_affiche_le_seuil_de_la_ci():
    [message] = BADGE.findall((ROOT / "README.md").read_text(encoding="utf-8"))
    assert unquote(message) == f"≥ {fail_under():g} %"

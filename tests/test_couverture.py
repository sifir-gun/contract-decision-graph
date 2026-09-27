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
# décision du 27/09/2026 (audit de publication) : l'entier juste sous la couverture
# mesurée, 98,71 % en lignes et en branches ; 96 % depuis le 26/09
DECIDED_THRESHOLD = 98


def fail_under() -> float:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["coverage"]["report"]["fail_under"]


def test_seuil_de_couverture_decide():
    assert fail_under() == DECIDED_THRESHOLD


def test_badge_du_readme_affiche_le_seuil_de_la_ci():
    [message] = BADGE.findall((ROOT / "README.md").read_text(encoding="utf-8"))
    assert unquote(message) == f"≥ {fail_under():g} %"

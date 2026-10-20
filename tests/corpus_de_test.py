"""Corpus synthétique des tests (docs/spec-versions-corpus.md) : un article fictif à deux
versions successives, rattaché à deux domaines, et une fiche qui le cite. Données de
test, aucun texte réel ; écrites dans un dossier temporaire par le fixture
`corpus_synthetique` (conftest.py), qui y redirige `application/ingestion.py`."""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

T1 = "C. test, art. T1"
FICHE = "Fiche projet : Fiche de test"
SECOND_FILE = "T1_2027-01-01.txt"

FIRST_TEXT = """Article T1
Version en vigueur du 26 avril 2019 au 01 janvier 2027
Modifié par Texte de test n°1 - art. 1

Alinéa commun aux deux versions.

Alinéa propre à la première version.
"""

SECOND_TEXT = """Article T1
Version en vigueur à partir du 01 janvier 2027
Modifié par Texte de test n°2 - art. 2

Alinéa commun aux deux versions.

Alinéa propre à la seconde version.

NOTA :
Conformément à l'article 9 du texte de test n°2, ces dispositions entrent en vigueur le 1er janvier 2027.
"""

FICHE_TEXT = """---
id: fiche-test
titre: Fiche de test
clauses: [delai_paiement]
versions_verifiees:
  code-test:
    T1: [2019-04-26, 2027-01-01]
---
Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique.

# Fiche de test

## Ce que dit le texte

- Alinéa commun aux deux versions (source : code-test, art. T1).

## Comment le projet l'applique

- Choix de test.
"""


def manifest() -> dict[str, Any]:
    return {
        "sources": {
            "code-test": {
                "format": "legifrance",
                "citation": "C. test",
                "title": "Code de test",
                "directory": "code-test",
                "file": "{article}.txt",
                "retrieved_at": date(2026, 10, 1),
                "articles": {"T1": ["delai_paiement", "preavis_resiliation"]},
                "later_versions": {
                    "T1": [
                        {
                            "file": SECOND_FILE,
                            "url": "https://example.org/T1",
                            "retrieved_at": date(2026, 10, 16),
                        }
                    ]
                },
            }
        }
    }


def write(root: Path) -> Path:
    """Écrit le corpus sous `root` (raw/, fiches/, manifest.yaml) ; rend `root`."""
    raw = root / "raw" / "code-test"
    raw.mkdir(parents=True)
    (raw / "T1.txt").write_text(FIRST_TEXT, encoding="utf-8")
    (raw / SECOND_FILE).write_text(SECOND_TEXT, encoding="utf-8")
    (root / "fiches").mkdir()
    (root / "fiches" / "test.md").write_text(FICHE_TEXT, encoding="utf-8")
    write_manifest(root, manifest())
    return root


def write_manifest(root: Path, content: dict[str, Any]) -> None:
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(content, allow_unicode=True), encoding="utf-8"
    )


def edit_manifest(root: Path, change: Callable[[dict[str, Any]], None]) -> None:
    content = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    change(content["sources"]["code-test"])
    write_manifest(root, content)


def raw(root: Path, name: str) -> Path:
    return root / "raw" / "code-test" / name


def replace_line(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")

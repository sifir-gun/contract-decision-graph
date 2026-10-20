"""Corpus synthétique des tests (docs/spec-versions-corpus.md) : un article fictif à deux
versions successives, rattaché à deux domaines, et une fiche qui le cite. Données de
test, aucun texte réel ; écrites dans un dossier temporaire par le fixture
`corpus_synthetique` (conftest.py), qui y redirige `application/ingestion.py`.
`add_points` y ajoute, à la demande d'un test, une source EUR-Lex à points d'annexe."""

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


# --- source EUR-Lex à points d'annexe (spec des profils, « Nouvelles sources EUR-Lex ») ---
# Formes relevées sur EUR-Lex le 20/10/2026 (règl. 1302/2014, point 4.2.10.2.1 ; règl.
# 748/2012, point 21.A.307), texte fictif : numéro et intitulé sur une même ligne, séparés
# par des espaces insécables, avec ou sans point final ; sous-points seuls sur leur ligne ;
# marques de consolidation sur leur propre ligne ; appel de note « ( 9 ) ».

POINTS = "annexe-test"
POINT_A, POINT_B = "9.1.2.1", "9.A.12"
POINT_A_REF = f"Règl. (UE) test, annexe, point {POINT_A}"
POINT_B_REF = f"Règl. (UE) test, annexe, point {POINT_B}"
NBSP = "\xa0"

POINT_A_TEXT = f"""9.1.2.1.{NBSP * 3}Exigences de test

1){NBSP}Premier alinéa du point de test.

2){NBSP}Second alinéa du point de test, pour les catégories définies ci-dessous:

—{NBSP}
première catégorie de test;
—{NBSP}
seconde catégorie de test.
"""

POINT_B_TEXT = f"""9.A.12{NBSP * 3} Conditions de test applicables aux pièces

a){NBSP}

Premier sous-point du point de test.

b){NBSP}

Second sous-point, qui renvoie à un texte de test{NBSP}( 9 ); et

▼M12

c){NBSP}

Troisième sous-point, ajouté par un texte modificateur de test.

▼M7

d){NBSP}

Quatrième sous-point du point de test.
"""

# citée par la fiche de test : deux entrées, la seconde avec un sous-point « 2) »
POINT_CLAIM = (
    "- Alinéas des points de test (sources : annexe-test, point 9.A.12 ; "
    "annexe-test, point 9.1.2.1, 2)).\n"
)


def point_source() -> dict[str, Any]:
    return {
        "format": "eurlex",
        "citation": "Règl. (UE) test",
        "title": "Règlement de test",
        "unit": "annexe, point",
        "directory": POINTS,
        "file": "point-{article}.txt",
        "retrieved_at": date(2026, 10, 20),
        "articles": {POINT_A: ["delai_paiement"], POINT_B: ["delai_paiement"]},
    }


def add_points(root: Path) -> None:
    """Ajoute au corpus écrit par `write` la source à points d'annexe : ses deux
    fichiers, sa source au manifeste, et une paraphrase de la fiche qui les cite."""
    directory = root / "raw" / POINTS
    directory.mkdir()
    (directory / f"point-{POINT_A}.txt").write_text(POINT_A_TEXT, encoding="utf-8")
    (directory / f"point-{POINT_B}.txt").write_text(POINT_B_TEXT, encoding="utf-8")
    content = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    content["sources"][POINTS] = point_source()
    write_manifest(root, content)
    replace_line(
        root / "fiches" / "test.md",
        "\n## Comment le projet",
        POINT_CLAIM + "\n## Comment le projet",
    )


def point_raw(root: Path, number: str) -> Path:
    return root / "raw" / POINTS / f"point-{number}.txt"


def replace_line(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new), encoding="utf-8")

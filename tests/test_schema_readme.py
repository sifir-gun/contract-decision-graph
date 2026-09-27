"""Le schéma du graphe dans le README est celui que LangGraph dessine depuis le code
(`draw_mermaid`) : ce test échoue si le README diverge du graphe réel. Pour le
régénérer : `uv run python scripts/schema_graphe.py`.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "schema_graphe.py"
    spec = importlib.util.spec_from_file_location("schema_graphe", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = _load_script()


def test_schema_du_readme_identique_au_graphe_reel():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert schema.readme_block(readme) == schema.mermaid(), (
        "schéma du README périmé : uv run python scripts/schema_graphe.py"
    )


def test_schema_de_haut_en_bas():
    # lisibilité vérifiée au rendu : de gauche à droite, le schéma est illisible
    assert "graph TD;" in schema.mermaid().splitlines()


def test_schema_montre_chaque_noeud_du_graphe():
    drawn = schema.mermaid()
    for node in (
        "validate_input",
        "extract_clauses",
        "verify_extraction",
        "analyst",
        "decision_gate",
        "human_review",
        "explain",
        "audit_seal",
        "reject",
    ):
        assert f"\t{node}({node})\n" in drawn


def test_ecriture_ne_remplace_que_le_bloc_entre_les_balises():
    text = f"avant\n{schema.BEGIN}\n```mermaid\nancien\n```\n{schema.END}\naprès\n"
    new = schema.replace_block(text, "graph TD;\n\ta --> b;\n")
    assert new == (
        f"avant\n{schema.BEGIN}\n```mermaid\ngraph TD;\n\ta --> b;\n```\n"
        f"{schema.END}\naprès\n"
    )
    assert schema.readme_block(new) == "graph TD;\n\ta --> b;\n"


@pytest.mark.parametrize(
    "text",
    [
        "pas de schéma\n",
        f"{schema.BEGIN}\n```mermaid\ngraph TD;\n```\n",  # balise de fin absente
        (
            f"{schema.BEGIN}\n```mermaid\na\n```\n{schema.END}\n"
            f"{schema.BEGIN}\n```mermaid\nb\n```\n{schema.END}\n"
        ),  # deux blocs
    ],
)
def test_balises_absentes_ou_repetees_erreur_explicite(text):
    with pytest.raises(ValueError, match="balises"):
        schema.readme_block(text)
    with pytest.raises(ValueError, match="balises"):
        schema.replace_block(text, "graph TD;\n")


def test_aucune_dependance_appelee_pour_dessiner():
    with pytest.raises(RuntimeError, match="schéma seulement"):
        schema.inert()

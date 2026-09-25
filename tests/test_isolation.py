"""Règles d'architecture (ports et adaptateurs), vérifiées sur l'AST des sources.

1. Chaque bibliothèque externe n'est importée que dans son adaptateur.
2. Sens des dépendances : domain <- ports <- application <- adapters <- cli.
3. Un adaptateur n'importe pas une autre famille d'adaptateurs.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "cdg"

# bibliothèque -> seuls chemins (relatifs à src/cdg) où elle peut être importée
LIBRARIES = {
    "langgraph": ("adapters/langgraph/",),
    # PostgresSaver exige une connexion psycopg
    "psycopg": ("adapters/postgres/", "adapters/langgraph/checkpointer.py"),
    "pgvector": ("adapters/postgres/",),
    "fastembed": ("adapters/fastembed.py",),
    "mistralai": ("adapters/llm/mistral.py",),
    "anthropic": ("adapters/llm/anthropic.py",),
}

# couche -> modules internes (cdg.<nom>) qu'elle peut importer, en plus d'elle-même ;
# cli.py, racine de composition, importe tout
LAYERS = {
    "domain": set(),
    "ports": {"domain"},
    "application": {"domain", "ports"},
    "adapters": {"domain", "ports", "application", "settings"},
}


def _targets(path: Path) -> list[str]:
    """Modules importés, en nom absolu ; `from a import b` donne `a` et `a.b`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets = []
    for node in ast.walk(tree):  # imports locaux (dans une fonction) compris
        if isinstance(node, ast.Import):
            targets += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path} : import relatif interdit (nom absolu attendu)")
            targets.append(node.module)
            targets += [f"{node.module}.{alias.name}" for alias in node.names]
    return targets


def _sources(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*.py"))}


def library_offenders(root: Path, library: str, allowed: tuple[str, ...]) -> list[str]:
    return [
        rel
        for rel, path in _sources(root).items()
        if not rel.startswith(allowed)
        and any(t == library or t.startswith(library + ".") for t in _targets(path))
    ]


def layer_offenders(root: Path, layer: str) -> list[str]:
    offenders = []
    for rel, path in _sources(root).items():
        if not rel.startswith(layer + "/"):
            continue
        for target in _targets(path):
            parts = target.split(".")
            if parts[0] != "cdg" or len(parts) < 2 or parts[1] == layer:
                continue
            if parts[1] not in LAYERS[layer]:
                offenders.append(f"{rel} -> {target}")
    return offenders


def adapter_family(parts: list[str]) -> str:
    """adapters/llm/mistral.py -> llm ; adapters/fastembed.py -> fastembed."""
    return parts[1].removesuffix(".py")


def cross_adapter_offenders(root: Path) -> list[str]:
    offenders = []
    for rel, path in _sources(root).items():
        parts = rel.split("/")
        if parts[0] != "adapters" or len(parts) < 2 or parts[1] == "__init__.py":
            continue
        own = adapter_family(parts)
        for target in _targets(path):
            t = target.split(".")
            if t[:2] == ["cdg", "adapters"] and len(t) > 2 and t[2] != own:
                offenders.append(f"{rel} -> {target}")
    return offenders


# --- Sources du dépôt ------------------------------------------------------------------


def test_sources_trouvees():
    assert {"domain", "ports", "application", "adapters"} <= {
        p.name for p in SRC.iterdir() if p.is_dir()
    }


@pytest.mark.parametrize("library", LIBRARIES)
def test_bibliotheque_confinee_a_son_adaptateur(library):
    allowed = LIBRARIES[library]
    assert library_offenders(SRC, library, allowed) == []
    # la règle n'est pas vide : la bibliothèque est bien importée là où elle est permise
    assert library_offenders(SRC, library, ()) != [], f"{library} importé nulle part"


@pytest.mark.parametrize("layer", LAYERS)
def test_sens_des_dependances(layer):
    assert layer_offenders(SRC, layer) == []


def test_adaptateurs_independants_entre_familles():
    assert cross_adapter_offenders(SRC) == []


# --- Les vérifications détectent bien une violation ---------------------------------------


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, code in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "code",
    ["import langgraph", "from langgraph.types import Send", "import langgraph.graph as g"],
)
def test_detection_bibliotheque(tmp_path, code):
    root = _tree(tmp_path, {"application/nodes/x.py": code, "adapters/langgraph/ok.py": code})
    assert library_offenders(root, "langgraph", LIBRARIES["langgraph"]) == [
        "application/nodes/x.py"
    ]


def test_detection_bibliotheque_import_local(tmp_path):
    root = _tree(tmp_path, {"domain/x.py": "def f():\n    from psycopg import sql\n"})
    assert library_offenders(root, "psycopg", LIBRARIES["psycopg"]) == ["domain/x.py"]


def test_nom_voisin_non_confondu(tmp_path):
    root = _tree(tmp_path, {"domain/x.py": "import langgraphx\nfrom pydantic import BaseModel"})
    assert library_offenders(root, "langgraph", LIBRARIES["langgraph"]) == []


@pytest.mark.parametrize(
    ("layer", "rel", "code"),
    [
        ("domain", "domain/x.py", "from cdg.ports.llm import LLMProvider"),
        ("domain", "domain/x.py", "from cdg.application import deps"),
        ("domain", "domain/x.py", "from cdg import settings"),
        ("ports", "ports/x.py", "from cdg.application.deps import Crag"),
        ("application", "application/x.py", "from cdg.adapters import fastembed"),
        ("application", "application/x.py", "import cdg.adapters.postgres.rag_store"),
        ("adapters", "adapters/llm/x.py", "from cdg import cli"),
    ],
)
def test_detection_sens_des_dependances(tmp_path, layer, rel, code):
    root = _tree(tmp_path, {rel: code})
    offenders = layer_offenders(root, layer)
    assert offenders
    assert all(o.startswith(f"{rel} -> ") for o in offenders)


def test_dependances_permises_non_signalees(tmp_path):
    root = _tree(
        tmp_path,
        {
            "ports/x.py": "from cdg.domain.state import Usage",
            "application/x.py": "from cdg.ports.llm import LLMProvider\nfrom cdg.domain import corpus",
            "adapters/llm/x.py": "from cdg.application.deps import Deps\nfrom cdg import settings",
        },
    )
    assert [layer_offenders(root, layer) for layer in LAYERS] == [[], [], [], []]


def test_detection_adaptateur_d_une_autre_famille(tmp_path):
    root = _tree(
        tmp_path,
        {
            "adapters/langgraph/x.py": "from cdg.adapters.postgres import rag_store",
            "adapters/langgraph/ok.py": "from cdg.adapters.langgraph import checkpointer",
            "adapters/fastembed.py": "from cdg.adapters.llm import build_provider",
        },
    )
    assert cross_adapter_offenders(root) == [
        "adapters/fastembed.py -> cdg.adapters.llm",
        "adapters/fastembed.py -> cdg.adapters.llm.build_provider",
        "adapters/langgraph/x.py -> cdg.adapters.postgres",
        "adapters/langgraph/x.py -> cdg.adapters.postgres.rag_store",
    ]


def test_import_relatif_refuse(tmp_path):
    root = _tree(tmp_path, {"domain/x.py": "from . import state"})
    with pytest.raises(AssertionError, match="import relatif"):
        layer_offenders(root, "domain")

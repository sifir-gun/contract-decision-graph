"""Règle d'architecture : seul orchestrator.py importe langgraph."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "cdg"
ALLOWED = SRC / "orchestrator.py"


def _imports_langgraph(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        if any(name == "langgraph" or name.startswith("langgraph.") for name in names):
            return True
    return False


def test_seul_orchestrator_importe_langgraph():
    sources = sorted(SRC.rglob("*.py"))
    assert sources, f"aucun fichier source trouvé sous {SRC}"
    offenders = [str(p.relative_to(SRC)) for p in sources
                 if p != ALLOWED and _imports_langgraph(p)]
    assert offenders == []


def test_detection_import_langgraph(tmp_path):
    for code in ("import langgraph", "from langgraph.types import Send",
                 "import langgraph.graph as g"):
        f = tmp_path / "m.py"
        f.write_text(code)
        assert _imports_langgraph(f), code
    f = tmp_path / "m.py"
    f.write_text("import langgraphx\nfrom pydantic import BaseModel")
    assert not _imports_langgraph(f)

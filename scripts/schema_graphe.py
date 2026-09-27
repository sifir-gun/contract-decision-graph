"""Schéma du graphe pour le README, dessiné par LangGraph depuis le code.

`uv run python scripts/schema_graphe.py` réécrit le bloc Mermaid du README entre ses
balises ; `tests/test_schema_readme.py` échoue si le README diverge du graphe réel.

Le graphe est câblé par `build_graph`, avec la configuration du projet et des
dépendances inertes : le dessin ne dépend que du câblage, aucune n'est appelée.
"""

import re
from pathlib import Path

from langchain_core.runnables.graph import NodeStyles

from cdg.adapters.langgraph.orchestrator import build_graph
from cdg.application.deps import Deps
from cdg.domain.config import load_config

README = Path(__file__).resolve().parents[1] / "README.md"
BEGIN = (
    "<!-- schéma généré par scripts/schema_graphe.py : ne pas modifier à la main -->"
)
END = "<!-- fin du schéma généré -->"
BLOCK = re.compile(
    re.escape(BEGIN) + r"\n```mermaid\n(?P<mermaid>.*?)```\n" + re.escape(END),
    re.DOTALL,
)
# styles de LangGraph, texte foncé en plus : lisible sur fond clair comme en thème sombre
STYLES = NodeStyles(
    default="fill:#f2f0ff,color:#1f1f1f,line-height:1.2",
    first="fill:#bfb6fc,color:#1f1f1f",
    last="fill:#bfb6fc,color:#1f1f1f",
)


def inert(*args, **kwargs):
    raise RuntimeError("schéma seulement : aucune dépendance n'est appelée")


def mermaid() -> str:
    deps = Deps(
        extractor=inert, crag=inert, audit_store=inert, clock=inert, explainer=inert
    )
    graph = build_graph(load_config(), deps).compile()
    return graph.get_graph().draw_mermaid(node_colors=STYLES).rstrip("\n") + "\n"


def _single_block(text: str) -> re.Match[str]:
    blocks = list(BLOCK.finditer(text))
    if len(blocks) != 1:
        raise ValueError(
            f"README : {len(blocks)} bloc(s) entre les balises du schéma, 1 attendu"
        )
    return blocks[0]


def readme_block(text: str) -> str:
    return _single_block(text)["mermaid"]


def replace_block(text: str, drawn: str) -> str:
    block = _single_block(text)
    new = f"{BEGIN}\n```mermaid\n{drawn}```\n{END}"
    return text[: block.start()] + new + text[block.end() :]


if __name__ == "__main__":
    README.write_text(
        replace_block(README.read_text(encoding="utf-8"), mermaid()), encoding="utf-8"
    )
    print(f"schéma écrit dans {README}")

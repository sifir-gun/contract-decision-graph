"""Aides des tests du serveur MCP : un vrai client MCP en mémoire (`mode="legacy"` :
JSON-RPC sur des flux en mémoire et poignée de main `initialize`, comme Claude Code avec
un serveur stdio), sous anyio."""

import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import anyio
from mcp import Client
from mcp.types import CallToolResult, Tool

from cdg.domain.authorization import Actor

MCP_ACTOR = Actor(canal="mcp", authentifie=False, operateur="assistant-test")


def session[T](server: Any, steps: Callable[[Client], Awaitable[T]]) -> T:
    """Une session du client sur le serveur, le temps des étapes."""

    async def main() -> T:
        async with Client(server, mode="legacy") as client:
            return await steps(client)

    return anyio.run(main)


def call(server: Any, name: str, arguments: dict[str, Any] | None = None):
    return session(server, lambda client: client.call_tool(name, arguments or {}))


def tools(server: Any) -> dict[str, Tool]:
    listed = session(server, lambda client: client.list_tools())
    return {tool.name: tool for tool in listed.tools}


def seen(result: CallToolResult) -> str:
    """Tout ce que voit l'assistant d'un résultat : le texte, puis le contenu structuré."""
    texts = [block.text for block in result.content if block.type == "text"]
    structured = json.dumps(result.structured_content, ensure_ascii=False)
    return "\n".join([*texts, structured])


# une enveloppe, telle qu'elle paraît dans du JSON (sauts de ligne échappés) ou en clair
ENVELOPE = re.compile(
    r"<<<CONTENU-NON-FIABLE-([0-9a-f]+)>>>.*?<<<FIN-CONTENU-NON-FIABLE-\1>>>", re.DOTALL
)
WINDOW = 6  # mots consécutifs : une fuite partielle se voit, pas une coïncidence


def escaped(text: str) -> str:
    """Le texte tel qu'il paraît dans du JSON."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def outside(out: str) -> str:
    """Ce qui reste d'une réponse hors des enveloppes : délimité par les balises, pas
    par la liste qui les porte (ses métadonnées restent contrôlées)."""
    return ENVELOPE.sub("", out)


def leaks(source: str, out: str, allowed: Iterable[str] = ()) -> list[str]:
    """Fenêtres de six mots consécutifs du texte source trouvées dans la réponse, hors
    des textes écrits par le code (`allowed` : constats des règles, synthèse…), qui
    reprennent parfois quelques mots du contrat (« à 50 % du montant annuel »)."""
    for text in allowed:
        out = out.replace(escaped(text), "")
    found = set()
    for line in source.splitlines():
        words = line.split()
        for i in range(len(words) - WINDOW + 1):
            window = " ".join(words[i : i + WINDOW])
            if escaped(window) in out:
                found.add(window)
    return sorted(found)


def written_by_code(fiche: dict) -> list[str]:
    """Textes d'une fiche écrits par le code : constats, synthèse, textes du gabarit."""
    texts = [c for d in fiche["domaines"] for c in d["constats"]]
    explanation = fiche.get("explication")
    if explanation is not None:
        texts.append(explanation["synthese"])
        texts += [c["texte"] for c in explanation["constats"] if c["texte"]]
    return texts


def error_text(result: CallToolResult) -> str:
    assert result.is_error, seen(result)
    return "\n".join(block.text for block in result.content if block.type == "text")

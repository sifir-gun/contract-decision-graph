"""Aides des tests du serveur MCP : un vrai client MCP en mémoire (`mode="legacy"` :
JSON-RPC sur des flux en mémoire et poignée de main `initialize`, comme Claude Code avec
un serveur stdio), sous anyio."""

import json
from collections.abc import Awaitable, Callable
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


def error_text(result: CallToolResult) -> str:
    assert result.is_error, seen(result)
    return "\n".join(block.text for block in result.content if block.type == "text")

"""Port LLM : sortie structurée validée par un modèle Pydantic, consommation mesurée.

Adaptateurs : `adapters/llm/` (Mistral, Anthropic), choisis par la configuration.
"""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from cdg.domain.state import Usage

Tier = Literal["main", "light"]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMOutputError(Exception):
    """Réponse du modèle inexploitable : non structurée, ou consommation absente."""


class LLMProvider(Protocol):
    name: str

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]: ...

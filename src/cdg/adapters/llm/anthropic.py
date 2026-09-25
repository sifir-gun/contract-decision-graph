"""Fournisseur Anthropic : `messages.parse` avec `output_format` (SDK anthropic 1.x).

`messages.parse` n'accepte pas `temperature` dans anthropic 1.8.0 : le réglage
`llm.temperature` ne s'applique qu'à Mistral.
"""

import time

from anthropic import Anthropic

from cdg.domain.config import LLMConfig
from cdg.domain.state import Usage
from cdg.ports.llm import LLMOutputError, SchemaT, Tier


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config: LLMConfig, *, api_key: str | None = None, client=None):
        self._config = config
        self._client = client or Anthropic(api_key=api_key, timeout=config.timeout_seconds)

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]:
        model = self._config.model(tier, provider=self.name)
        start = time.monotonic()
        response = self._client.messages.parse(
            model=model,
            max_tokens=self._config.max_output_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if response.parsed_output is None:
            raise LLMOutputError(f"{model} : réponse vide ou non structurée ({node})")
        if response.usage is None:
            raise LLMOutputError(f"{model} : consommation absente de la réponse ({node})")
        usage = Usage(
            node=node,
            model=model,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
        return schema.model_validate(response.parsed_output.model_dump()), usage

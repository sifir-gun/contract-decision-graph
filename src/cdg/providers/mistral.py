"""Fournisseur Mistral : `chat.parse` avec un modèle Pydantic (SDK mistralai 2.x)."""

import time

from mistralai.client import Mistral

from cdg.config import LLMConfig
from cdg.deps import SchemaT, Tier
from cdg.state import Usage


class MistralProvider:
    name = "mistral"

    def __init__(self, config: LLMConfig, *, api_key: str | None = None, client=None):
        self._config = config
        self._client = client or Mistral(api_key=api_key, timeout_ms=config.timeout_seconds * 1000)

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]:
        from cdg.providers import LLMOutputError

        model = self._config.model(tier, provider=self.name)
        start = time.monotonic()
        response = self._client.chat.parse(
            response_format=schema,
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._config.temperature,
            max_tokens=self._config.max_output_tokens,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        parsed = response.choices[0].message.parsed if response.choices else None
        if parsed is None:
            raise LLMOutputError(f"{model} : réponse vide ou non structurée ({node})")
        if response.usage is None:
            raise LLMOutputError(f"{model} : consommation absente de la réponse ({node})")
        usage = Usage(
            node=node,
            model=model,
            tokens_in=response.usage.prompt_tokens or 0,
            tokens_out=response.usage.completion_tokens or 0,
            latency_ms=latency_ms,
        )
        return schema.model_validate(parsed.model_dump()), usage

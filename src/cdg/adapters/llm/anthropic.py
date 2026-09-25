"""Fournisseur Anthropic : `messages.parse` avec `output_format` (SDK anthropic 1.x).

`messages.parse` n'accepte pas `temperature` dans anthropic 1.8.0 : le réglage
`llm.temperature` ne s'applique qu'à Mistral. Le SDK ne reprend rien (`max_retries=0`,
2 par défaut) : une erreur passagère devient `LLMTransientError`, reprise par la
RetryPolicy du nœud, réglée dans la configuration.
"""

import time

from anthropic import Anthropic, APIStatusError, APITimeoutError

from cdg.domain.config import LLMConfig
from cdg.domain.models import Usage
from cdg.ports.llm import LLMOutputError, LLMTransientError, SchemaT, Tier


def _transient_reason(exc: Exception) -> str | None:
    """Motif d'une erreur passagère (429, 5xx dont 529 surcharge, délai dépassé), sinon None."""
    if isinstance(exc, APITimeoutError):
        return "délai dépassé"
    if isinstance(exc, APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500):
        return f"HTTP {exc.status_code}"
    return None


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config: LLMConfig, *, api_key: str | None = None, client=None):
        self._config = config
        self._client = client or Anthropic(
            api_key=api_key, timeout=config.timeout_seconds, max_retries=0
        )

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]:
        model = self._config.model(tier, provider=self.name)
        start = time.monotonic()
        try:
            response = self._client.messages.parse(
                model=model,
                max_tokens=self._config.max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except Exception as exc:
            reason = _transient_reason(exc)
            if reason is None:
                raise
            raise LLMTransientError(f"{model} : erreur passagère, {reason} ({node})") from exc
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

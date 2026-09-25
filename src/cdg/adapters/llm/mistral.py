"""Fournisseur Mistral : `chat.parse` avec un modèle Pydantic (SDK mistralai 2.x).

Le SDK ne reprend rien (`retry_config=None`) : une erreur passagère devient
`LLMTransientError`, reprise par la RetryPolicy du nœud, réglée dans la configuration.
"""

import time

from mistralai.client import Mistral, errors

from cdg.domain.config import LLMConfig
from cdg.domain.models import Usage
from cdg.ports.llm import LLMOutputError, LLMTransientError, SchemaT, Tier


def _transient_reason(exc: Exception) -> str | None:
    """Motif d'une erreur passagère (429, 5xx, délai dépassé), sinon None."""
    if isinstance(exc, errors.MistralError):
        if exc.status_code == 429:
            limit = exc.headers.get("x-ratelimit-limit-req-minute")
            if limit is None:
                return "HTTP 429"
            unit = "requête" if limit in ("0", "1") else "requêtes"
            return f"HTTP 429, limite du compte : {limit} {unit} par minute"
        return f"HTTP {exc.status_code}" if exc.status_code >= 500 else None
    # délai dépassé : exception httpx transmise telle quelle par le SDK, reconnue par sa
    # classe sans importer httpx (dépendance du SDK, non déclarée par le projet)
    for cls in type(exc).__mro__:
        if cls.__name__ == "TimeoutException" and cls.__module__.split(".")[0] == "httpx":
            return "délai dépassé"
    return None


class MistralProvider:
    name = "mistral"

    def __init__(self, config: LLMConfig, *, api_key: str | None = None, client=None):
        self._config = config
        self._client = client or Mistral(
            api_key=api_key, timeout_ms=config.timeout_seconds * 1000, retry_config=None
        )

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]:
        model = self._config.model(tier, provider=self.name)
        start = time.monotonic()
        try:
            response = self._client.chat.parse(
                response_format=schema,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_output_tokens,
            )
        except Exception as exc:
            reason = _transient_reason(exc)
            if reason is None:
                raise
            raise LLMTransientError(f"{model} : erreur passagère, {reason} ({node})") from exc
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

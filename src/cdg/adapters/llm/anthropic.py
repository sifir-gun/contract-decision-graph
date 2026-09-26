"""Fournisseur Anthropic : `messages.parse` avec `output_format` (SDK anthropic 1.x).

`messages.parse` n'accepte pas `temperature` dans anthropic 1.8.0 : le réglage
`llm.temperature` ne s'applique qu'à Mistral. Le SDK ne reprend rien (`max_retries=0`,
2 par défaut) : une erreur passagère devient `LLMTransientError`, reprise par la
RetryPolicy du nœud, réglée dans la configuration ; un 429 dont la limite du compte vaut 0
devient `LLMQuotaError`, jamais reprise.
"""

import time

from anthropic import Anthropic, APIConnectionError, APIStatusError, APITimeoutError

from cdg.domain.config import LLMConfig
from cdg.domain.models import Usage
from cdg.ports.llm import LLMOutputError, LLMQuotaError, LLMTransientError, SchemaT, Tier

# limites du compte (documentation Anthropic, « Rate limits », vérifiée le 2026-09-25)
QUOTA_HEADERS = ("anthropic-ratelimit-requests-limit", "anthropic-ratelimit-tokens-limit")


def _port_error(exc: Exception, model: str, node: str) -> Exception | None:
    """Erreur du port qui remplace l'erreur du SDK, ou None si elle reste telle quelle."""
    if isinstance(exc, APITimeoutError):  # sous-classe d'APIConnectionError : avant elle
        reason = "délai dépassé"
    elif isinstance(exc, APIConnectionError):
        reason = "connexion refusée ou impossible"
    elif isinstance(exc, APIStatusError) and exc.status_code == 429:
        zero = [h for h in QUOTA_HEADERS if exc.response.headers.get(h) == "0"]
        if zero:
            limits = ", ".join(f"{h} = 0" for h in zero)
            return LLMQuotaError(
                f"{model} : quota nul sur le compte Anthropic ({limits}), erreur non "
                "passagère : vérifier l'offre du compte dans la console Anthropic, ou "
                f"changer de modèle dans config/decision.yaml ({node})"
            )
        reason = "HTTP 429"
    elif isinstance(exc, APIStatusError) and exc.status_code >= 500:  # dont 529, surcharge
        reason = f"HTTP {exc.status_code}"
    else:
        return None
    return LLMTransientError(f"{model} : erreur passagère, {reason} ({node})")


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
            error = _port_error(exc, model, node)
            if error is None:
                raise
            raise error from exc
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

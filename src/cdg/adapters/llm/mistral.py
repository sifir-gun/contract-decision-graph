"""Fournisseur Mistral : `chat.parse` avec un modèle Pydantic (SDK mistralai 2.x).

Le SDK ne reprend rien (`retry_config=None`) : une erreur passagère devient
`LLMTransientError`, reprise par la RetryPolicy du nœud, réglée dans la configuration ;
un 429 dont la limite du compte vaut 0 devient `LLMQuotaError`, jamais reprise.
"""

import time

from mistralai.client import Mistral, errors

from cdg.domain.config import LLMConfig
from cdg.domain.models import Usage
from cdg.ports.llm import (
    LLMOutputError,
    LLMQuotaError,
    LLMTransientError,
    SchemaT,
    Tier,
)

# limites du compte renvoyées avec un 429 ; l'une à 0 : quota nul, pas un débit dépassé
QUOTA_HEADERS = ("x-ratelimit-limit-req-minute", "x-ratelimit-limit-tokens-minute")
# exceptions httpx transmises telles quelles par le SDK, reconnues par leur classe sans
# importer httpx (dépendance du SDK, non déclarée par le projet)
_HTTPX_TRANSIENT = {
    "TimeoutException": "délai dépassé",
    "ConnectError": "connexion refusée ou impossible",
}


def _port_error(exc: Exception, model: str, node: str) -> Exception | None:
    """Erreur du port qui remplace l'erreur du SDK, ou None si elle reste telle quelle."""
    if isinstance(exc, errors.MistralError):
        if exc.status_code == 429:
            zero = [h for h in QUOTA_HEADERS if exc.headers.get(h) == "0"]
            if zero:
                limits = ", ".join(f"{h} = 0" for h in zero)
                return LLMQuotaError(
                    f"{model} : quota nul sur le compte Mistral ({limits}), erreur non "
                    "passagère : vérifier l'offre du compte dans la console Mistral, ou "
                    f"changer de modèle dans config/decision.yaml ({node})"
                )
            limit = exc.headers.get("x-ratelimit-limit-req-minute")
            detail = (
                ""
                if limit is None
                else f", limite du compte : {limit} requêtes par minute"
            )
            reason = f"HTTP 429{detail}"
        elif exc.status_code >= 500:
            reason = f"HTTP {exc.status_code}"
        else:
            return None
    else:
        httpx_classes = [
            c.__name__
            for c in type(exc).__mro__
            if c.__module__.split(".")[0] == "httpx"
        ]
        known = next(
            (_HTTPX_TRANSIENT[n] for n in httpx_classes if n in _HTTPX_TRANSIENT), None
        )
        if known is None:
            return None
        reason = known
    return LLMTransientError(f"{model} : erreur passagère, {reason} ({node})")


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
            error = _port_error(exc, model, node)
            if error is None:
                raise
            raise error from exc
        latency_ms = int((time.monotonic() - start) * 1000)
        message = response.choices[0].message if response.choices else None
        parsed = message.parsed if message is not None else None
        if parsed is None:
            raise LLMOutputError(f"{model} : réponse vide ou non structurée ({node})")
        if response.usage is None:
            raise LLMOutputError(
                f"{model} : consommation absente de la réponse ({node})"
            )
        usage = Usage(
            node=node,
            model=model,
            tokens_in=response.usage.prompt_tokens or 0,
            tokens_out=response.usage.completion_tokens or 0,
            latency_ms=latency_ms,
        )
        return schema.model_validate(parsed.model_dump()), usage

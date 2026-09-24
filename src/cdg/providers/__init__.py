"""Fournisseurs LLM derrière l'interface `deps.LLMProvider`, choisis par la configuration."""

import os

from cdg.config import LLMConfig
from cdg.deps import LLMProvider
from cdg.settings import SettingsError

API_KEY_VARS = {"mistral": "MISTRAL_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class LLMOutputError(Exception):
    """Réponse du modèle inexploitable : non structurée, ou consommation absente."""


def build_provider(config: LLMConfig) -> LLMProvider:
    var = API_KEY_VARS[config.provider]
    api_key = os.environ.get(var, "")
    if not api_key:
        raise SettingsError(
            f"variable d'environnement absente ou vide : {var} (fournisseur {config.provider})"
        )
    if config.provider == "mistral":
        from cdg.providers.mistral import MistralProvider

        return MistralProvider(config, api_key=api_key)
    from cdg.providers.anthropic import AnthropicProvider

    return AnthropicProvider(config, api_key=api_key)

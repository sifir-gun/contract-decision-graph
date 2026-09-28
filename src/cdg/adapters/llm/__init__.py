"""Fournisseurs LLM derrière le port `ports.llm.LLMProvider`, choisis par la configuration.

Adresse de l'API de Mistral : celle du SDK (`https://api.mistral.ai`), sauf si
MISTRAL_SERVER_URL la remplace (paramètre de déploiement, pas de décision : serveur
factice des tests du cluster). En production, le proxy de sortie ne laisse passer que
l'API de Mistral : ce réglage ne peut pas détourner les appels (ADR 005).
"""

import os
from urllib.parse import urlsplit

from cdg.domain.config import LLMConfig
from cdg.ports.llm import LLMProvider
from cdg.settings import SettingsError

API_KEY_VARS = {"mistral": "MISTRAL_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
SERVER_URL_VAR = "MISTRAL_SERVER_URL"


def mistral_server_url() -> str | None:
    """Adresse de l'API de Mistral, si MISTRAL_SERVER_URL la remplace ; sinon None (celle
    du SDK). Une adresse mal formée est refusée ; le message ne la reprend jamais, elle
    pourrait contenir des identifiants."""
    value = os.environ.get(SERVER_URL_VAR, "")
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise SettingsError(
            f"{SERVER_URL_VAR} mal formée : adresse http ou https absolue attendue"
        )
    if parts.username or parts.password:
        raise SettingsError(
            f"{SERVER_URL_VAR} refusée : identifiants dans l'adresse (la clé d'API passe "
            "par MISTRAL_API_KEY)"
        )
    return value


def build_provider(config: LLMConfig) -> LLMProvider:
    var = API_KEY_VARS[config.provider]
    api_key = os.environ.get(var, "")
    if not api_key:
        raise SettingsError(
            f"variable d'environnement absente ou vide : {var} (fournisseur {config.provider})"
        )
    if config.provider == "mistral":
        from cdg.adapters.llm.mistral import MistralProvider

        return MistralProvider(config, api_key=api_key, server_url=mistral_server_url())
    from cdg.adapters.llm.anthropic import AnthropicProvider

    return AnthropicProvider(config, api_key=api_key)

"""Fournisseurs LLM : sortie structurée, niveau de modèle, consommation, erreurs explicites.

Faux clients imitant les réponses des SDK installés : aucun appel réseau.
"""

from types import SimpleNamespace

import anthropic as anthropic_sdk
import httpx
import httpx2
import pytest
from mistralai.client import errors as mistral_errors
from pydantic import BaseModel

from cdg.adapters.llm import build_provider
from cdg.adapters.llm.anthropic import AnthropicProvider
from cdg.adapters.llm.mistral import MistralProvider
from cdg.domain.config import load_config
from cdg.ports.llm import LLMOutputError, LLMQuotaError, LLMTransientError
from cdg.settings import SettingsError

CONFIG = load_config()


class Answer(BaseModel):
    verdict: str


OK = Answer(verdict="ok")


class FakeMistralChat:
    def __init__(self, parsed=OK, usage=(120, 30)):
        self.parsed, self.usage, self.calls = parsed, usage, []

    def parse(self, response_format, **kwargs):
        self.calls.append({"response_format": response_format, **kwargs})
        usage = (
            None
            if self.usage is None
            else SimpleNamespace(prompt_tokens=self.usage[0], completion_tokens=self.usage[1])
        )
        message = SimpleNamespace(parsed=self.parsed)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeAnthropicMessages:
    def __init__(self, parsed=OK, usage=(200, 40)):
        self.parsed, self.usage, self.calls = parsed, usage, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = (
            None
            if self.usage is None
            else SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1])
        )
        return SimpleNamespace(parsed_output=self.parsed, usage=usage)


def mistral(**kwargs):
    chat = FakeMistralChat(**kwargs)
    return MistralProvider(CONFIG.llm, client=SimpleNamespace(chat=chat)), chat


def anthropic(**kwargs):
    messages = FakeAnthropicMessages(**kwargs)
    return AnthropicProvider(CONFIG.llm, client=SimpleNamespace(messages=messages)), messages


# --- Mistral ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,model", [("main", "mistral-small-2603"), ("light", "ministral-8b-2512")]
)
def test_mistral_modele_selon_le_niveau(tier, model):
    provider, chat = mistral()
    answer, usage = provider.structured(
        tier=tier, system="S", user="U", schema=Answer, node="extract_clauses"
    )
    assert answer == Answer(verdict="ok")
    [call] = chat.calls
    assert call["model"] == model and call["response_format"] is Answer
    assert call["temperature"] == 0 and call["max_tokens"] == CONFIG.llm.max_output_tokens
    assert call["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    assert (usage.node, usage.model, usage.tokens_in, usage.tokens_out) == (
        "extract_clauses",
        model,
        120,
        30,
    )
    assert usage.latency_ms >= 0


def test_mistral_reponse_non_structuree_leve():
    provider, _ = mistral(parsed=None)
    with pytest.raises(LLMOutputError, match="structurée"):
        provider.structured(tier="main", system="S", user="U", schema=Answer, node="n")


def test_mistral_consommation_absente_leve():
    provider, _ = mistral(usage=None)
    with pytest.raises(LLMOutputError, match="consommation"):
        provider.structured(tier="main", system="S", user="U", schema=Answer, node="n")


# --- Anthropic ------------------------------------------------------------------------


def test_anthropic_modele_et_sortie_structuree():
    provider, messages = anthropic()
    answer, usage = provider.structured(
        tier="light", system="S", user="U", schema=Answer, node="crag_grade"
    )
    assert answer == Answer(verdict="ok")
    [call] = messages.calls
    assert call["model"] == "claude-haiku-4-5-20251001" and call["output_format"] is Answer
    assert call["system"] == "S" and call["messages"] == [{"role": "user", "content": "U"}]
    assert "temperature" not in call  # absent de messages.parse dans anthropic 1.8.0
    assert (usage.model, usage.tokens_in, usage.tokens_out) == (
        "claude-haiku-4-5-20251001",
        200,
        40,
    )


def test_anthropic_reponse_non_structuree_leve():
    provider, _ = anthropic(parsed=None)
    with pytest.raises(LLMOutputError):
        provider.structured(tier="main", system="S", user="U", schema=Answer, node="n")


# --- Construction depuis la configuration ------------------------------------------------


def test_fournisseur_choisi_par_la_configuration(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-de-test")
    assert isinstance(build_provider(CONFIG.llm), MistralProvider)


def test_cle_d_api_absente_erreur_explicite(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = CONFIG.llm.model_copy(update={"provider": "anthropic"})
    with pytest.raises(SettingsError, match="ANTHROPIC_API_KEY"):
        build_provider(llm)


# --- Erreurs passagères (429, 5xx, délai dépassé) : traduites pour la reprise ----------

MISTRAL_REQUEST = httpx.Request("POST", "https://api.mistral.test/v1/chat/completions")
ANTHROPIC_REQUEST = httpx2.Request("POST", "https://api.anthropic.test/v1/messages")


def mistral_status(status: int, headers: dict | None = None):
    response = httpx.Response(
        status, headers=headers or {}, text='{"message":"x"}', request=MISTRAL_REQUEST
    )
    return mistral_errors.SDKError("API error occurred", response)


def anthropic_status(cls, status: int):
    response = httpx2.Response(status, text='{"message":"x"}', request=ANTHROPIC_REQUEST)
    return cls("erreur", response=response, body=None)


class RaisingChat:
    def __init__(self, error):
        self.error = error

    def parse(self, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (mistral_status(429), True),
        (mistral_status(503), True),
        (httpx.ReadTimeout("délai", request=MISTRAL_REQUEST), True),
        (httpx.ConnectTimeout("délai", request=MISTRAL_REQUEST), True),
        (mistral_status(400), False),
        (mistral_status(401), False),
        (httpx.ConnectError("refus", request=MISTRAL_REQUEST), True),  # décision du 25/09
    ],
)
def test_mistral_erreur_passagere_traduite(error, transient):
    provider = MistralProvider(CONFIG.llm, client=SimpleNamespace(chat=RaisingChat(error)))
    call = {"tier": "main", "system": "s", "user": "u", "schema": Answer, "node": "n"}
    if transient:
        with pytest.raises(LLMTransientError) as info:
            provider.structured(**call)
        assert info.value.__cause__ is error and "mistral-small-2603" in str(info.value)
    else:
        with pytest.raises(type(error)):
            provider.structured(**call)


def test_mistral_429_indique_la_limite_du_compte():
    error = mistral_status(429, {"x-ratelimit-limit-req-minute": "188"})
    provider = MistralProvider(CONFIG.llm, client=SimpleNamespace(chat=RaisingChat(error)))
    with pytest.raises(LLMTransientError, match="limite du compte : 188 requêtes par minute"):
        provider.structured(tier="main", system="s", user="u", schema=Answer, node="n")


# --- Quota nul (429 avec une limite du compte à 0) : non passager, jamais repris -----------


@pytest.mark.parametrize(
    "headers",
    [
        {"x-ratelimit-limit-req-minute": "0", "x-ratelimit-remaining-req-minute": "0"},
        {"x-ratelimit-limit-tokens-minute": "0"},
    ],
)
def test_mistral_quota_nul_erreur_non_passagere(headers):
    error = mistral_status(429, headers)
    provider = MistralProvider(CONFIG.llm, client=SimpleNamespace(chat=RaisingChat(error)))
    with pytest.raises(LLMQuotaError, match="vérifier l'offre du compte") as info:
        provider.structured(tier="main", system="s", user="u", schema=Answer, node="n")
    assert not isinstance(info.value, LLMTransientError) and info.value.__cause__ is error
    assert "mistral-small-2603" in str(info.value) and "= 0" in str(info.value)


class RaisingMessages:
    def __init__(self, error):
        self.error = error

    def parse(self, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (anthropic_status(anthropic_sdk.RateLimitError, 429), True),
        (anthropic_status(anthropic_sdk.InternalServerError, 500), True),
        (anthropic_status(anthropic_sdk.APIStatusError, 529), True),  # surcharge
        (anthropic_sdk.APITimeoutError(request=ANTHROPIC_REQUEST), True),
        (anthropic_status(anthropic_sdk.BadRequestError, 400), False),
        (anthropic_sdk.APIConnectionError(request=ANTHROPIC_REQUEST), True),  # décision du 25/09
    ],
)
def test_anthropic_erreur_passagere_traduite(error, transient):
    client = SimpleNamespace(messages=RaisingMessages(error))
    provider = AnthropicProvider(CONFIG.llm, client=client)
    call = {"tier": "light", "system": "s", "user": "u", "schema": Answer, "node": "n"}
    if transient:
        with pytest.raises(LLMTransientError) as info:
            provider.structured(**call)
        assert info.value.__cause__ is error
    else:
        with pytest.raises(type(error)):
            provider.structured(**call)


def test_pas_de_reprise_cachee_dans_les_sdk():
    # la reprise est réglée dans la configuration (RetryPolicy), jamais en double
    assert AnthropicProvider(CONFIG.llm, api_key="test")._client.max_retries == 0
    mistral_client = MistralProvider(CONFIG.llm, api_key="test")._client
    assert mistral_client.sdk_configuration.retry_config is None


@pytest.mark.parametrize(
    "header", ["anthropic-ratelimit-requests-limit", "anthropic-ratelimit-tokens-limit"]
)
def test_anthropic_quota_nul_erreur_non_passagere(header):
    response = httpx2.Response(
        429, headers={header: "0"}, text='{"message":"x"}', request=ANTHROPIC_REQUEST
    )
    error = anthropic_sdk.RateLimitError("limite", response=response, body=None)
    provider = AnthropicProvider(
        CONFIG.llm, client=SimpleNamespace(messages=RaisingMessages(error))
    )
    with pytest.raises(LLMQuotaError, match="vérifier l'offre du compte") as info:
        provider.structured(tier="light", system="s", user="u", schema=Answer, node="n")
    assert info.value.__cause__ is error


def test_anthropic_429_avec_limite_non_nulle_reste_passager():
    response = httpx2.Response(
        429,
        headers={"anthropic-ratelimit-requests-limit": "1000"},
        text='{"message":"x"}',
        request=ANTHROPIC_REQUEST,
    )
    error = anthropic_sdk.RateLimitError("limite", response=response, body=None)
    provider = AnthropicProvider(
        CONFIG.llm, client=SimpleNamespace(messages=RaisingMessages(error))
    )
    with pytest.raises(LLMTransientError):
        provider.structured(tier="light", system="s", user="u", schema=Answer, node="n")

"""Fournisseurs LLM : sortie structurée, niveau de modèle, consommation, erreurs explicites.

Faux clients imitant les réponses des SDK installés : aucun appel réseau.
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from cdg.adapters.llm import build_provider
from cdg.adapters.llm.anthropic import AnthropicProvider
from cdg.adapters.llm.mistral import MistralProvider
from cdg.domain.config import load_config
from cdg.ports.llm import LLMOutputError
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

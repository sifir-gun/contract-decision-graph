"""Conformité aux ports : chaque adaptateur et chaque doublure expose les attributs et
les méthodes de son interface, avec la même signature.

Un `Protocol` n'est pas contrôlé à l'exécution, et le projet n'a pas de vérificateur
de types : ce test en tient lieu. AuditStore s'y ajoutera avec son implémentation (J4).
"""

import inspect

import pytest
from doubles import FakeCrag, FakeLLM, FakeRetriever, FixedExtractor, HashEmbedder

from cdg.adapters.fastembed import FastembedEmbedder
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.llm.anthropic import AnthropicProvider
from cdg.adapters.llm.mistral import MistralProvider
from cdg.adapters.postgres.rag_store import PgvectorRetriever
from cdg.application.deps import Crag, Extractor
from cdg.application.extraction import LLMExtractor
from cdg.domain.config import load_config
from cdg.ports.embedder import Embedder
from cdg.ports.llm import LLMProvider
from cdg.ports.retriever import Retriever

CONFIG = load_config()

# (port, fabrique de l'implémentation) ; aucun appel réseau ni poids chargés
IMPLEMENTATIONS = [
    (LLMProvider, lambda: MistralProvider(CONFIG.llm, client=object())),
    (LLMProvider, lambda: AnthropicProvider(CONFIG.llm, client=object())),
    (LLMProvider, FakeLLM),
    (Embedder, lambda: FastembedEmbedder(CONFIG.embedding, model=object())),
    (Embedder, HashEmbedder),
    (Retriever, lambda: PgvectorRetriever("", HashEmbedder())),
    (Retriever, FakeRetriever),
    (Extractor, lambda: LLMExtractor(FakeLLM())),
    (Extractor, lambda: FixedExtractor([])),
    (Crag, FakeCrag),
    (Crag, lambda: orchestrator.crag_runner(FakeRetriever(), FakeLLM(), CONFIG)),
]


def _methods(port) -> list[str]:
    return [
        name
        for name, member in vars(port).items()
        if inspect.isfunction(member)
        and (not name.startswith("_") or name == "__call__")
    ]


def _shape(func) -> list[tuple]:
    """Noms, genres (positionnel, nommé seulement…) et valeurs par défaut, sans `self`."""
    return [
        (p.name, p.kind, p.default)
        for p in inspect.signature(func).parameters.values()
        if p.name != "self"
    ]


def _id(case) -> str:
    port, factory = case
    impl = factory()
    name = impl.__name__ if inspect.isfunction(impl) else type(impl).__name__
    return f"{port.__name__}-{name}"


@pytest.mark.parametrize("case", IMPLEMENTATIONS, ids=[_id(c) for c in IMPLEMENTATIONS])
def test_implementation_conforme_au_port(case):
    port, factory = case
    impl = factory()
    for attribute in getattr(port, "__annotations__", {}):
        assert hasattr(impl, attribute), f"attribut manquant : {attribute}"
    for method in _methods(port):
        # une fonction nue tient lieu de __call__
        actual = impl if inspect.isfunction(impl) else getattr(type(impl), method, None)
        assert actual is not None, f"méthode manquante : {method}"
        assert _shape(actual) == _shape(getattr(port, method)), method


def test_detection_signature_differente():
    class Wrong:
        name = "faux"

        def structured(self, *, tier, system, user, schema):  # `node` manque
            raise NotImplementedError

    assert _shape(Wrong.structured) != _shape(LLMProvider.structured)


def test_chaque_port_a_ses_methodes():
    assert _methods(LLMProvider) == ["structured"]
    assert _methods(Embedder) == ["embed_passages", "embed_query"]
    assert _methods(Retriever) == ["search"]
    assert _methods(Extractor) == ["__call__"]
    assert _methods(Crag) == ["__call__"]

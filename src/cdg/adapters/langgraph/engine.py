"""Adaptateur du port `ContractEngine` (`ports/engine.py`).

Chaque opération ouvre le graphe avec les dépendances qu'elle exige, comme chaque commande
de la CLI : l'analyse, le fournisseur LLM et le corpus ; la reprise, l'explication seule ;
la lecture, aucune. Les dépendances sont construites à l'appel : une clé d'API absente
n'empêche ni de lire un dossier ni de trancher une revue.

Le checkpointer est PostgreSQL (CLI, interface web) ou en mémoire (`memory_opener` :
démonstration, tests), avec le même sérialiseur strict.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.application.deps import Deps
from cdg.domain.config import DecisionConfig
from cdg.ports.engine import ThreadError

GraphOpener = Callable[[Deps], AbstractContextManager[CompiledStateGraph]]


@dataclass(frozen=True)
class EngineDeps:
    """Dépendances de chaque opération, construites à l'appel."""

    run: Callable[[], Deps]
    resume: Callable[[], Deps]
    expire: Callable[[], Deps]
    read: Callable[[], Deps]


def memory_opener(config: DecisionConfig) -> GraphOpener:
    """Graphes qui partagent un checkpointer en mémoire, le temps du processus."""
    saver = InMemorySaver(serde=strict_serializer())

    @contextmanager
    def open_graph(deps: Deps) -> Iterator[CompiledStateGraph]:
        yield orchestrator.build_graph(config, deps).compile(checkpointer=saver)

    return open_graph


class LangGraphEngine:
    def __init__(
        self, config: DecisionConfig, open_graph: GraphOpener, deps: EngineDeps
    ):
        self._config, self._open, self._deps = config, open_graph, deps

    def run(
        self,
        contract_id: str,
        raw_text: str,
        parties: Sequence[str],
        analysis_date: date,
    ) -> dict[str, Any]:
        deps = self._deps.run()  # le fournisseur d'abord : clé absente, rien d'ouvert
        with self._open(deps) as graph:
            return orchestrator.run_contract(
                graph,
                contract_id,
                raw_text,
                parties,
                analysis_date=analysis_date,
                config=self._config,
            )

    def resume(self, thread_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        with self._open(self._deps.resume()) as graph:
            return orchestrator.resume_thread(
                graph, thread_id, answer, config=self._config
            )

    def status(self, thread_id: str) -> dict[str, Any]:
        with self._open(self._deps.read()) as graph:
            _known(graph, thread_id)
            return orchestrator.thread_status(graph, thread_id)

    def values(self, thread_id: str) -> dict[str, Any]:
        with self._open(self._deps.read()) as graph:
            return _known(graph, thread_id)

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        with self._open(self._deps.read()) as graph:
            return orchestrator.thread_history(graph, thread_id)

    def thread_ids(self) -> list[str]:
        with self._open(self._deps.read()) as graph:
            return orchestrator.list_threads(graph)

    def expire(self, older_than: timedelta, now: datetime) -> list[dict[str, Any]]:
        with self._open(self._deps.expire()) as graph:
            return orchestrator.expire_threads(graph, older_than, now)


def _known(graph: CompiledStateGraph, thread_id: str) -> dict[str, Any]:
    values = graph.get_state({"configurable": {"thread_id": thread_id}}).values
    if not values:
        raise ThreadError(f"thread inconnu : {thread_id}")
    return dict(values)

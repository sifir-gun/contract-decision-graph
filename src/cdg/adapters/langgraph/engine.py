"""Adaptateur du port `ContractEngine` (`ports/engine.py`).

Chaque opération ouvre le graphe avec les dépendances qu'elle exige, comme chaque commande
de la CLI : l'analyse, le fournisseur LLM et le corpus ; la reprise, l'explication seule ;
la lecture, aucune. Les dépendances sont construites à l'appel : une clé d'API absente
n'empêche ni de lire un dossier ni de trancher une revue.

Le checkpointer est PostgreSQL (CLI, interface web) ou en mémoire (`memory_opener` :
démonstration, tests), avec le même sérialiseur strict.
"""

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import langsmith
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.application.deps import Deps
from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig
from cdg.ports.engine import ThreadError
from cdg.ports.locks import ContractLocks
from cdg.ports.resumes import ResumeCounter

GraphOpener = Callable[[Deps], AbstractContextManager[CompiledStateGraph]]


@dataclass(frozen=True)
class EngineDeps:
    """Dépendances de chaque opération, construites à l'appel."""

    run: Callable[[], Deps]
    resume: Callable[[], Deps]
    expire: Callable[[], Deps]
    read: Callable[[], Deps]


class LockedMemorySaver(InMemorySaver):
    """`InMemorySaver` sous verrou. Celui de langgraph-checkpoint 4.2.0 n'en a aucun :
    `list` parcourt son stockage pendant qu'une écriture peut y ajouter un thread, et
    `get_tuple` y insère un thread inconnu. Or l'interface sert ses requêtes dans des
    threads : chaque accès au stockage passe par le verrou. Les versions asynchrones
    appellent celles-ci."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._storage_lock = threading.RLock()

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        with self._storage_lock:
            return super().get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self._storage_lock:  # collectés sous le verrou, rendus ensuite
            found = [*super().list(config, filter=filter, before=before, limit=limit)]
        yield from found

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with self._storage_lock:
            return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._storage_lock:
            super().put_writes(config, writes, task_id, task_path)

    def delete_thread(self, thread_id: str) -> None:
        with self._storage_lock:
            super().delete_thread(thread_id)

    def get_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        with self._storage_lock:
            return super().get_delta_channel_history(config=config, channels=channels)


def memory_opener(config: DecisionConfig) -> GraphOpener:
    """Graphes qui partagent un checkpointer en mémoire, le temps du processus."""
    saver = LockedMemorySaver(serde=strict_serializer())

    @contextmanager
    def open_graph(deps: Deps) -> Iterator[CompiledStateGraph]:
        yield orchestrator.build_graph(config, deps).compile(checkpointer=saver)

    return open_graph


class LangGraphEngine:
    """Chaque modification d'un contrat (création, revue, expiration) se fait sous son
    verrou (`ports/locks.py`) : un seul processus à la fois, quel que soit le réplica."""

    def __init__(
        self,
        config: DecisionConfig,
        open_graph: GraphOpener,
        deps: EngineDeps,
        locks: ContractLocks,
        resumes: ResumeCounter,
    ):
        self._config, self._open, self._deps = config, open_graph, deps
        self._locks, self._resumes = locks, resumes
        # LangSmith jamais actif (ADR 008) : ce réglage global passe avant l'environnement,
        # où LANGCHAIN_TRACING_V2=true l'emporterait sur LANGSMITH_TRACING=false ; la CLI
        # refuse en plus de démarrer avec une telle variable
        langsmith.configure(enabled=False)

    def run(
        self,
        contract_id: str,
        raw_text: str,
        parties: Sequence[str],
        analysis_date: date,
        actor: Actor,
        relaunch_of: str | None = None,
    ) -> dict[str, Any]:
        deps = self._deps.run()  # le fournisseur d'abord : clé absente, rien d'ouvert
        # verrou d'abord : un contrat en cours ailleurs est refusé sans rien ouvrir, et la
        # vérification d'existence puis la création se font sous le verrou
        with self._locks.hold(contract_id), self._open(deps) as graph:
            return orchestrator.run_contract(
                graph,
                contract_id,
                raw_text,
                parties,
                analysis_date=analysis_date,
                config=self._config,
                actor=actor,
                code=deps.code_version,
                relaunch_of=relaunch_of,
            )

    def resume(self, thread_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        with self._locks.hold(thread_id), self._open(self._deps.resume()) as graph:
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

    def overview(self) -> list[dict[str, Any]]:
        with self._open(self._deps.read()) as graph:
            return orchestrator.threads_overview(graph)

    def expire(
        self, older_than: timedelta, now: datetime, actor: Actor
    ) -> list[dict[str, Any]]:
        with self._open(self._deps.expire()) as graph:
            return orchestrator.expire_threads(
                graph, older_than, now, hold=self._locks.hold, actor=actor
            )

    def resume_interrupted(self) -> list[dict[str, Any]]:
        # les dépendances d'une analyse : la reprise refait les étapes interrompues
        with self._open(self._deps.run()) as graph:
            return orchestrator.resume_interrupted(
                graph,
                hold=self._locks.hold,
                record=self._resumes.record,
                limit=self._config.interrupted.max_resumes,
                config=self._config,
            )


def _known(graph: CompiledStateGraph, thread_id: str) -> dict[str, Any]:
    values = graph.get_state({"configurable": {"thread_id": thread_id}}).values
    if not values:
        raise ThreadError(f"thread inconnu : {thread_id}")
    return dict(values)

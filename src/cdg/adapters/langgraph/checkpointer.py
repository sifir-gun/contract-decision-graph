"""Checkpointer PostgreSQL de LangGraph et sérialiseur strict des checkpoints.

Seul module, avec `adapters/postgres/`, qui importe psycopg : `PostgresSaver` exige
une connexion psycopg.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection, sql
from psycopg.rows import dict_row

from cdg.domain.models import (
    AgentVerdict,
    Clause,
    ClauseRetrieval,
    HumanDecision,
    NodeFailure,
    RetrievalTrace,
    Usage,
)
from cdg.settings import APP_ROLE

# --- Sérialiseur des checkpoints ---------------------------------------------------

# seuls types métier relus depuis la base ; les types sûrs de LangGraph
# (Send, Interrupt, datetime...) restent admis par la bibliothèque
CHECKPOINT_TYPES = (
    Clause,
    AgentVerdict,
    ClauseRetrieval,
    HumanDecision,
    NodeFailure,
    RetrievalTrace,
    Usage,
)
_BLOCKED_KINDS = {"msgpack_blocked", "msgpack_method_blocked"}
_serde_watch = threading.local()


class BlockedDeserialization(Exception):
    """Type hors liste autorisée rencontré en relisant un checkpoint."""


def _collect_serde_event(event: dict) -> None:
    # appelé dans le fil qui désérialise ; les exceptions d'un écouteur sont
    # avalées par LangGraph, d'où la collecte puis la levée après lecture
    events = getattr(_serde_watch, "events", None)
    if events is not None:
        events.append(event)


register_serde_event_listener(_collect_serde_event)


class StrictSerializer(JsonPlusSerializer):
    """Lève au lieu de dégrader : par défaut, un type bloqué revient en dict."""

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        previous = getattr(_serde_watch, "events", None)
        _serde_watch.events = events = []
        try:
            value = super().loads_typed(data)
        finally:
            _serde_watch.events = previous
        blocked = sorted(
            {f"{e['module']}.{e['name']}" for e in events if e["kind"] in _BLOCKED_KINDS}
        )
        if blocked:
            raise BlockedDeserialization(
                "type hors liste autorisée dans un checkpoint : " + ", ".join(blocked)
            )
        return value


def strict_serializer() -> StrictSerializer:
    return StrictSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


# --- Checkpointer PostgreSQL -----------------------------------------------------

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

# Requêtes de PostgresSaver 3.1.2 hors setup() et delete_thread() : SELECT,
# INSERT ... ON CONFLICT DO NOTHING / DO UPDATE (d'où UPDATE). Aucun DELETE.
# une commande par requête : prepare_threshold=0 prépare chaque requête
_CHECKPOINT_GRANTS = [
    sql.SQL(
        "REVOKE ALL ON checkpoints, checkpoint_blobs, checkpoint_writes,"
        " checkpoint_migrations FROM {role}"
    ),
    sql.SQL(
        "GRANT SELECT, INSERT, UPDATE ON checkpoints, checkpoint_blobs, checkpoint_writes TO {role}"
    ),
]


@contextmanager
def open_saver(conninfo: str) -> Iterator[PostgresSaver]:
    # paramètres de PostgresSaver.from_conn_string, qui n'accepte pas de serde
    with Connection.connect(
        conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        yield PostgresSaver(conn, serde=strict_serializer())


def setup_database(admin_conninfo: str) -> None:
    """Tables du checkpointer (droits administrateur), puis droits d'app_role."""
    with open_saver(admin_conninfo) as saver:
        saver.setup()
        for statement in _CHECKPOINT_GRANTS:
            saver.conn.execute(statement.format(role=sql.Identifier(APP_ROLE)))


def delete_thread(conninfo: str, thread_id: str) -> None:
    """Ménage des tests uniquement : exige DELETE, qu'app_role n'a pas."""
    with open_saver(conninfo) as saver:
        saver.delete_thread(thread_id)

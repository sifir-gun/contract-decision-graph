"""Checkpointer PostgreSQL de LangGraph et sérialiseur strict des checkpoints.

Seul module, avec `adapters/postgres/`, qui importe psycopg : `PostgresSaver` exige
une connexion psycopg.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.event_hooks import (
    SerdeEvent,
    register_serde_event_listener,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from cdg.domain.authorization import Actor
from cdg.domain.explanation import ExplainedFinding, Explanation
from cdg.domain.models import (
    AgentVerdict,
    Clause,
    ClauseRetrieval,
    HumanDecision,
    HumanReview,
    NodeFailure,
    RetrievalTrace,
    Usage,
)
from cdg.ports.connections import ConnectionsExhausted
from cdg.settings import APP_ROLE

# une chaîne de connexion (administration, tests) ou le pool du processus
# une chaîne de connexion, ou la fonction qui la rend, relue à chaque connexion (mot de
# passe tourné sans redémarrage, ADR 005) ; ou le pool du processus
Conninfo = str | Callable[[], str]
Source = Conninfo | ConnectionPool[Connection[DictRow]]

# --- Sérialiseur des checkpoints ---------------------------------------------------

# seuls types métier relus depuis la base ; les types sûrs de LangGraph
# (Send, Interrupt, datetime...) restent admis par la bibliothèque
CHECKPOINT_TYPES = (
    Clause,
    AgentVerdict,
    ClauseRetrieval,
    ExplainedFinding,
    Explanation,
    HumanDecision,  # v1, états d'avant la PR D2
    HumanReview,
    Actor,
    NodeFailure,
    RetrievalTrace,
    Usage,
)
_BLOCKED_KINDS = {"msgpack_blocked", "msgpack_method_blocked"}
_serde_watch = threading.local()


class BlockedDeserialization(Exception):
    """Type hors liste autorisée rencontré en relisant un checkpoint."""


def _collect_serde_event(event: SerdeEvent) -> None:
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
        events: list[SerdeEvent] = []
        _serde_watch.events = events
        try:
            value = super().loads_typed(data)
        finally:
            _serde_watch.events = previous
        blocked = sorted(
            {
                f"{e['module']}.{e['name']}"
                for e in events
                if e["kind"] in _BLOCKED_KINDS
            }
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
def open_saver(source: Source) -> Iterator[PostgresSaver]:
    """Checkpointer sur le pool du processus (réglé par `adapters/postgres/connexions.py`
    comme l'exige PostgresSaver), ou sur une connexion directe ouverte depuis une chaîne
    (administration, tests). Pool épuisé : `ConnectionsExhausted`."""
    if isinstance(source, ConnectionPool):
        try:
            yield PostgresSaver(source, serde=strict_serializer())
        except PoolTimeout as exc:
            raise ConnectionsExhausted() from exc
        return
    # paramètres de PostgresSaver.from_conn_string, qui n'accepte pas de serde
    with Connection.connect(
        source() if callable(source) else source,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as conn:
        yield PostgresSaver(conn, serde=strict_serializer())


def setup_database(admin_conninfo: Conninfo) -> None:
    """Tables du checkpointer (droits administrateur), puis droits d'app_role."""
    with open_saver(admin_conninfo) as saver:
        saver.setup()
        conn = saver.conn
        # open_saver passe une connexion, jamais un pool
        if not isinstance(conn, Connection):
            raise TypeError(f"connexion psycopg attendue, reçu {type(conn).__name__}")
        # retrait et octroi dans une seule transaction : aucune session d'app_role ne voit
        # la table sans droits (le 05/10, en autocommit, une autre session était refusée
        # entre les deux ; dans le cluster, setup-db est la tâche pre-upgrade, lancée
        # pendant que les anciens pods servent)
        with conn.transaction():
            for statement in _CHECKPOINT_GRANTS:
                conn.execute(statement.format(role=sql.Identifier(APP_ROLE)))


def delete_thread(conninfo: Conninfo, thread_id: str) -> None:
    """Ménage des tests uniquement : exige DELETE, qu'app_role n'a pas."""
    with open_saver(conninfo) as saver:
        saver.delete_thread(thread_id)

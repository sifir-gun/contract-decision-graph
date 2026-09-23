"""Seul module qui importe LangGraph : adaptateurs (Send, interrupt) et
câblage du graphe."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.event_hooks import register_serde_event_listener
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt
from psycopg import Connection, sql
from psycopg.rows import dict_row

from cdg import policy
from cdg.config import DecisionConfig
from cdg.deps import Deps
from cdg.nodes.analyst import analyst
from cdg.nodes.audit_seal import audit_seal
from cdg.nodes.decision_gate import decision_gate
from cdg.nodes.explain import explain
from cdg.nodes.extract_clauses import extract_clauses
from cdg.nodes.reject import reject
from cdg.nodes.validate_input import validate_input
from cdg.nodes.verify_extraction import verify_extraction
from cdg.settings import APP_ROLE
from cdg.state import (DOMAINS, AgentVerdict, AnalystInput, Clause,
                       ContractState, HumanDecision, Usage)


def read_route(state: ContractState) -> str:
    """Arête conditionnelle : renvoie la route écrite par le nœud précédent."""
    return state["route"]


def route_after_verify(state: ContractState) -> str | list[Send]:
    """Route `analysts` : un `Send` par domaine ;
    sinon la route telle quelle."""
    if state["route"] == "analysts":                # décision lue dans l'état
        return [Send("analyst", {"domain": d, "clauses": state["clauses"]})
                for d in DOMAINS]
    return state["route"]


def human_review(state: ContractState,
                 decision_config: DecisionConfig) -> dict:
    """Adaptateur de l'arbitrage humain : interrupt() puis policy.review.

    Réexécuté depuis le début à chaque reprise : aucun effet de bord avant
    interrupt(). Une réponse mal formée ou refusée par la politique est
    redemandée, avec le motif dans la charge utile.
    """
    request = policy.build_request(state, decision_config)
    while True:
        human, error = policy.review(interrupt(request),
                                     state.get("verdicts", []),
                                     decision_config)
        if error is None:
            return {"human": human, "final_decision": human.decision}
        request = {**request, "error": error}


def build_graph(config: DecisionConfig, deps: Deps) -> StateGraph:
    """Câble les 9 nœuds, dépendances liées ; renvoie le graphe non compilé."""
    builder = StateGraph(ContractState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("extract_clauses", partial(extract_clauses,
                                                extractor=deps.extractor))
    builder.add_node("verify_extraction", verify_extraction)
    # input_schema explicite : LangGraph ne le déduit pas d'un partial
    # (voir docs/journal.md)
    builder.add_node("analyst", partial(
        analyst, crag=deps.crag, decision_config=config),
                     input_schema=AnalystInput)
    builder.add_node("decision_gate", partial(
        decision_gate, decision_config=config))
    builder.add_node("human_review", partial(
        human_review, decision_config=config))
    builder.add_node("explain", explain)
    builder.add_node("audit_seal", audit_seal)
    builder.add_node("reject", reject)

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges("validate_input", read_route,
                                  ["extract_clauses", "reject"])
    builder.add_edge("extract_clauses", "verify_extraction")
    builder.add_conditional_edges("verify_extraction", route_after_verify,
                                  ["extract_clauses", "analyst",
                                   "human_review"])
    builder.add_edge("analyst", "decision_gate")
    builder.add_conditional_edges("decision_gate", read_route,
                                  ["human_review", "explain"])
    builder.add_edge("human_review", "explain")
    builder.add_edge("explain", "audit_seal")
    builder.add_edge("reject", "audit_seal")       # un rejet est scellé aussi
    builder.add_edge("audit_seal", END)
    return builder


# --- Sérialiseur des checkpoints ---------------------------------------------------

# seuls types métier relus depuis la base ; les types sûrs de LangGraph
# (Send, Interrupt, datetime...) restent admis par la bibliothèque
CHECKPOINT_TYPES = (Clause, AgentVerdict, HumanDecision, Usage)
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
        blocked = sorted({f"{e['module']}.{e['name']}" for e in events
                          if e["kind"] in _BLOCKED_KINDS})
        if blocked:
            raise BlockedDeserialization(
                "type hors liste autorisée dans un checkpoint : "
                + ", ".join(blocked))
        return value


def strict_serializer() -> StrictSerializer:
    return StrictSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)


# --- Checkpointer PostgreSQL -----------------------------------------------------

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

# Requêtes de PostgresSaver 3.1.2 hors setup() et delete_thread() : SELECT,
# INSERT ... ON CONFLICT DO NOTHING / DO UPDATE (d'où UPDATE). Aucun DELETE.
# une commande par requête : prepare_threshold=0 prépare chaque requête
_CHECKPOINT_GRANTS = [
    sql.SQL("REVOKE ALL ON checkpoints, checkpoint_blobs, checkpoint_writes,"
            " checkpoint_migrations FROM {role}"),
    sql.SQL("GRANT SELECT, INSERT, UPDATE"
            " ON checkpoints, checkpoint_blobs, checkpoint_writes TO {role}"),
]


@contextmanager
def _saver(conninfo: str) -> Iterator[PostgresSaver]:
    # paramètres de PostgresSaver.from_conn_string, qui n'accepte pas de serde
    with Connection.connect(conninfo, autocommit=True, prepare_threshold=0,
                            row_factory=dict_row) as conn:
        yield PostgresSaver(conn, serde=strict_serializer())


def setup_database(admin_conninfo: str) -> None:
    """Tables du checkpointer (droits administrateur), puis droits d'app_role."""
    with _saver(admin_conninfo) as saver:
        saver.setup()
        for statement in _CHECKPOINT_GRANTS:
            saver.conn.execute(
                statement.format(role=sql.Identifier(APP_ROLE)))


@contextmanager
def open_graph(config: DecisionConfig, deps: Deps,
               conninfo: str) -> Iterator[CompiledStateGraph]:
    """Graphe compilé avec le checkpointer PostgreSQL et le sérialiseur strict."""
    with _saver(conninfo) as saver:
        yield build_graph(config, deps).compile(checkpointer=saver)


def delete_thread(conninfo: str, thread_id: str) -> None:
    """Ménage des tests uniquement : exige DELETE, qu'app_role n'a pas."""
    with _saver(conninfo) as saver:
        saver.delete_thread(thread_id)

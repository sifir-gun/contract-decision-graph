"""Seul module qui importe LangGraph : adaptateurs (Send, interrupt) et câblage du graphe."""

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

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
from cdg.state import DOMAINS, AnalystInput, ContractState


def read_route(state: ContractState) -> str:
    return state["route"]


def route_after_verify(state: ContractState) -> str | list[Send]:
    if state["route"] == "analysts":                  # décision lue dans l'état
        return [Send("analyst", {"domain": d, "clauses": state["clauses"]}) for d in DOMAINS]
    return state["route"]


def human_review(state: ContractState) -> dict:
    # TODO J2 : interrupt() puis contrôle par policy.py ; écrit human et final_decision.
    return {}


def build_graph(config: DecisionConfig, deps: Deps) -> StateGraph:
    builder = StateGraph(ContractState)
    builder.add_node("validate_input", validate_input)
    builder.add_node("extract_clauses", partial(extract_clauses, extractor=deps.extractor))
    builder.add_node("verify_extraction", verify_extraction)
    # input_schema explicite : LangGraph ne le déduit pas d'un partial (voir docs/journal.md)
    builder.add_node("analyst", partial(analyst, crag=deps.crag, decision_config=config),
                     input_schema=AnalystInput)
    builder.add_node("decision_gate", partial(decision_gate, decision_config=config))
    builder.add_node("human_review", human_review)
    builder.add_node("explain", explain)
    builder.add_node("audit_seal", audit_seal)
    builder.add_node("reject", reject)

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges("validate_input", read_route, ["extract_clauses", "reject"])
    builder.add_edge("extract_clauses", "verify_extraction")
    builder.add_conditional_edges("verify_extraction", route_after_verify,
                                  ["extract_clauses", "analyst", "human_review"])
    builder.add_edge("analyst", "decision_gate")
    builder.add_conditional_edges("decision_gate", read_route, ["human_review", "explain"])
    builder.add_edge("human_review", "explain")
    builder.add_edge("explain", "audit_seal")
    builder.add_edge("reject", "audit_seal")           # un rejet est scellé aussi
    builder.add_edge("audit_seal", END)
    return builder

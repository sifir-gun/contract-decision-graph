"""Adaptateur d'orchestration : câblage du graphe, Send, interrupt, exécution d'un thread.

Le checkpointer PostgreSQL et son sérialiseur strict sont dans `checkpointer.py`.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import get_runtime
from langgraph.types import Command, RetryPolicy, Send, StateSnapshot, interrupt

from cdg.adapters.langgraph import checkpointer
from cdg.application import crag
from cdg.application.deps import Crag, Deps, RetrievalResult
from cdg.application.failures import escalate
from cdg.application.nodes.analyst import analyst
from cdg.application.nodes.audit_seal import audit_seal
from cdg.application.nodes.decision_gate import decision_gate
from cdg.application.nodes.explain import explain
from cdg.application.nodes.extract_clauses import extract_clauses
from cdg.application.nodes.reject import reject
from cdg.application.nodes.validate_input import validate_input
from cdg.application.nodes.verify_extraction import verify_extraction
from cdg.domain import expiry, masking, policy
from cdg.domain.config import AnalystRetry, DecisionConfig
from cdg.domain.state import DOMAINS, AnalystInput, Clause, ContractState, Domain, NodeFailure
from cdg.ports.llm import LLMProvider
from cdg.ports.retriever import Retriever


def read_route(state: ContractState) -> str:
    """Arête conditionnelle : renvoie la route écrite par le nœud précédent."""
    return state["route"]


def read_crag_route(state: crag.CragState) -> str:
    """Arête du sous-graphe CRAG. Annotée à part : LangGraph déduit un schéma de
    l'annotation d'une fonction de routage, et celui de ContractState entrerait en
    conflit avec l'état du CRAG (voir le journal)."""
    return state["route"]


def route_after_verify(state: ContractState) -> str | list[Send]:
    """Route `analysts` : un `Send` par domaine ;
    sinon la route telle quelle."""
    if state["route"] == "analysts":  # décision lue dans l'état
        return [
            Send(
                "analyst",
                {"domain": d, "clauses": state["clauses"], "analysis_date": state["analysis_date"]},
            )
            for d in DOMAINS
        ]
    return state["route"]


def human_review(state: ContractState, decision_config: DecisionConfig) -> dict:
    """Adaptateur de l'arbitrage humain : interrupt() puis policy.review.

    Réexécuté depuis le début à chaque reprise : aucun effet de bord avant
    interrupt(). Une réponse mal formée ou refusée par la politique est
    redemandée, avec le motif dans la charge utile.
    """
    request = policy.build_request(state, decision_config)
    while True:
        human, error = policy.review(interrupt(request), state.get("verdicts", []), decision_config)
        if error is None:
            return {"human": human, "final_decision": human.decision}
        request = {**request, "error": error}


def build_crag_graph(
    retriever: Retriever, llm: LLMProvider, config: DecisionConfig
) -> CompiledStateGraph:
    """Sous-graphe CRAG, nœuds purs de `application/crag.py`.

    Compilé sans checkpointer (décision du 25/09/2026, voir le journal) : par défaut,
    il hériterait de celui du parent. Un analyste relancé refait donc son CRAG de zéro ;
    le résumé du CRAG est porté par le verdict, lui-même checkpointé.
    """
    builder = StateGraph(crag.CragState)
    builder.add_node(
        "retrieve", partial(crag.retrieve, retriever=retriever, top_k=config.crag.top_k)
    )
    builder.add_node("grade", partial(crag.grade, llm=llm, max_passes=config.crag.max_passes))
    builder.add_node("rewrite", partial(crag.rewrite, llm=llm))
    builder.add_node("generate", crag.generate)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges("grade", read_crag_route, ["rewrite", "generate"])
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=False)


def crag_runner(retriever: Retriever, llm: LLMProvider, config: DecisionConfig) -> Crag:
    """CRAG injecté dans les analystes : invoque le sous-graphe compilé."""
    graph = build_crag_graph(retriever, llm, config)

    def run(domain: Domain, clauses: list[Clause], analysis_date: date) -> RetrievalResult:
        return graph.invoke(crag.start(domain, clauses, analysis_date))["result"]

    return run


def guard(
    name: str,
    fn: Callable[[Any], dict],
    *,
    retry: RetryPolicy | None = None,
    on_failure: Callable[[list[NodeFailure]], dict] | None = None,
) -> Callable[[Any], dict]:
    """Garde d'échec de nœud : une exception devient un `NodeFailure` dans `failures`.

    - le contrôle de LangGraph (interruption, commande) passe au travers ;
    - avec `retry`, une erreur transitoire est relancée tant qu'il reste des tentatives :
      la RetryPolicy du nœud la reprend, et la garde n'intervient qu'après la dernière ;
    - `on_failure` complète la mise à jour d'un nœud à plusieurs sorties (route vers
      l'humain), à partir de tous les échecs connus.
    """

    def node(state: Any) -> dict:
        try:
            return fn(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            attempt = get_runtime().execution_info.node_attempt  # 1 à la première
            # retry_on : prédicat par défaut de RetryPolicy (erreurs réseau, 5xx...)
            if retry is not None and retry.retry_on(exc) and attempt < retry.max_attempts:
                raise
            failure = NodeFailure(
                node=name,
                error=type(exc).__name__,
                message=str(exc),
                attempts=attempt,
                domain=state.get("domain"),  # analyste seulement
            )
            update: dict = {"failures": [failure]}
            if on_failure is not None:
                update |= on_failure([*state.get("failures", []), failure])
            return update

    return node


def retry_policy(settings: AnalystRetry) -> RetryPolicy:
    return RetryPolicy(
        initial_interval=settings.initial_interval_seconds,
        backoff_factor=settings.backoff_factor,
        max_interval=settings.max_interval_seconds,
        max_attempts=settings.max_attempts,
        jitter=settings.jitter,
    )


def build_graph(config: DecisionConfig, deps: Deps) -> StateGraph:
    """Câble les 9 nœuds, dépendances liées et gardes d'échec ; graphe non compilé.

    Tout nœud est gardé, sauf `human_review`, où aboutissent les échecs. Un nœud à
    plusieurs sorties en échec route vers l'humain (ESCALADE) ; un analyste en échec
    ne fait pas échouer le fan-out, `decision_gate` escalade ; l'échec de l'extraction
    est escaladé par `verify_extraction`.
    """
    retry = retry_policy(config.analyst_retry)
    builder = StateGraph(ContractState)
    builder.add_node(
        "validate_input",
        guard(
            "validate_input", partial(validate_input, decision_config=config), on_failure=escalate
        ),
    )
    builder.add_node(
        "extract_clauses",
        guard("extract_clauses", partial(extract_clauses, extractor=deps.extractor)),
    )
    builder.add_node(
        "verify_extraction",
        guard(
            "verify_extraction",
            partial(verify_extraction, decision_config=config),
            on_failure=escalate,
        ),
    )
    # input_schema explicite : LangGraph ne le déduit pas d'un partial
    # (voir docs/journal.md)
    builder.add_node(
        "analyst",
        guard("analyst", partial(analyst, crag=deps.crag, decision_config=config), retry=retry),
        input_schema=AnalystInput,
        retry_policy=retry,
    )
    builder.add_node(
        "decision_gate",
        guard("decision_gate", partial(decision_gate, decision_config=config), on_failure=escalate),
    )
    builder.add_node("human_review", partial(human_review, decision_config=config))
    builder.add_node("explain", guard("explain", explain))
    builder.add_node("audit_seal", guard("audit_seal", audit_seal))
    builder.add_node("reject", guard("reject", reject))

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input", read_route, ["extract_clauses", "reject", "human_review"]
    )
    builder.add_edge("extract_clauses", "verify_extraction")
    builder.add_conditional_edges(
        "verify_extraction", route_after_verify, ["extract_clauses", "analyst", "human_review"]
    )
    builder.add_edge("analyst", "decision_gate")
    builder.add_conditional_edges("decision_gate", read_route, ["human_review", "explain"])
    builder.add_edge("human_review", "explain")
    builder.add_edge("explain", "audit_seal")
    builder.add_edge("reject", "audit_seal")  # un rejet est scellé aussi
    builder.add_edge("audit_seal", END)
    return builder


@contextmanager
def open_graph(config: DecisionConfig, deps: Deps, conninfo: str) -> Iterator[CompiledStateGraph]:
    """Graphe compilé avec le checkpointer PostgreSQL et le sérialiseur strict."""
    with checkpointer.open_saver(conninfo) as saver:
        yield build_graph(config, deps).compile(checkpointer=saver)


# --- Exécution d'un thread : run, resume, history ---------------------------------


class ThreadError(Exception):
    """Thread inconnu, déjà existant, ou pas en attente d'une décision humaine."""


def _thread(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _awaits_human(snapshot: StateSnapshot) -> bool:
    # pas snapshot.next : après une réponse refusée, human_review s'interrompt
    # de nouveau mais langgraph 1.2.12 ne la compte plus dans `next`
    return any(task.name == "human_review" and task.interrupts for task in snapshot.tasks)


def thread_status(graph: CompiledStateGraph, thread_id: str) -> dict:
    """État d'un thread, sérialisable en JSON ; `demande` : charge utile en attente."""
    snapshot = graph.get_state(_thread(thread_id))
    values = snapshot.values
    human = values.get("human")
    pending = [i.value for i in snapshot.interrupts]
    return {
        "thread_id": thread_id,
        "statut": ("suspendu" if pending else "en_cours" if snapshot.next else "termine"),
        "route": values.get("route"),
        "proposed_decision": values.get("proposed_decision"),
        "final_decision": values.get("final_decision"),
        "margin": values.get("margin"),
        "failure_report": values.get("failure_report"),
        "failures": [f.model_dump() for f in values.get("failures", [])],
        "reject_reason": values.get("reject_reason"),
        "human": human.model_dump() if human else None,
        "verdicts": [v.model_dump(exclude={"evidence_ids"}) for v in values.get("verdicts", [])],
        "demande": pending[0] if pending else None,
    }


def run_contract(
    graph: CompiledStateGraph,
    contract_id: str,
    raw_text: str,
    parties: Sequence[str] = (),
    *,
    analysis_date: date,
) -> dict:
    """Un contrat = un thread ; refuse un thread existant plutôt que d'y cumuler.

    Le texte est masqué AVANT l'invocation : l'entrée du graphe est écrite dans le
    premier checkpoint, le texte original n'atteint donc jamais la base ni le LLM.
    `analysis_date` fixe la date à laquelle les versions des textes sont jugées ; elle est
    écrite dans l'état, donc rejouable.
    """
    if graph.get_state(_thread(contract_id)).values:
        raise ThreadError(
            f"le thread {contract_id} existe déjà : utiliser "
            "resume, ou un autre identifiant de contrat"
        )
    masked = masking.mask(raw_text, parties)
    graph.invoke(
        {"contract_id": contract_id, "raw_text": masked.text, "analysis_date": analysis_date},
        _thread(contract_id),
    )
    return {**thread_status(graph, contract_id), "masquage": masked.counts}


def resume_thread(graph: CompiledStateGraph, thread_id: str, answer: dict) -> dict:
    """Reprise par Command(resume=...) d'un thread en attente d'un humain."""
    snapshot = graph.get_state(_thread(thread_id))
    if not snapshot.values:
        raise ThreadError(f"thread inconnu : {thread_id}")
    if not _awaits_human(snapshot):
        raise ThreadError(f"le thread {thread_id} n'est pas en attente d'une décision humaine")
    graph.invoke(Command(resume=answer), _thread(thread_id))
    return thread_status(graph, thread_id)


def thread_history(graph: CompiledStateGraph, thread_id: str) -> list[dict]:
    """Checkpoints du thread, du plus ancien au plus récent."""
    history = list(graph.get_state_history(_thread(thread_id)))
    if not history:
        raise ThreadError(f"thread inconnu : {thread_id}")
    return [
        {
            "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
            "step": snap.metadata.get("step"),
            "source": snap.metadata.get("source"),
            "created_at": snap.created_at,
            "next": list(snap.next),
            "route": snap.values.get("route"),
            "proposed_decision": snap.values.get("proposed_decision"),
            "final_decision": snap.values.get("final_decision"),
        }
        for snap in reversed(history)
    ]


def expire_threads(
    graph: CompiledStateGraph,
    older_than: timedelta,
    now: datetime,
    thread_ids: set[str] | None = None,
) -> list[dict]:
    """Reprend en NO_GO système les threads en attente depuis plus de `older_than`.

    `thread_ids` restreint la recherche : les tests ne touchent ainsi jamais aux
    autres threads en attente de la base. La CLI n'en passe pas.
    """
    # list() garde le verrou (non réentrant) du checkpointer tant que le
    # générateur n'est pas épuisé : collecter d'abord, interroger ensuite
    found = {
        checkpoint.config["configurable"]["thread_id"]
        for checkpoint in graph.checkpointer.list(None)
    }
    if thread_ids is not None:
        found &= thread_ids
    pending = []
    for thread_id in sorted(found):
        snapshot = graph.get_state(_thread(thread_id))
        if _awaits_human(snapshot):
            # dernier checkpoint = suspension : l'attente ne bouge plus ensuite
            pending.append((thread_id, datetime.fromisoformat(snapshot.created_at)))
    return [
        resume_thread(graph, thread_id, expiry.system_decision(now - since, older_than))
        for thread_id, since in expiry.expired(pending, older_than, now)
    ]

"""Adaptateur d'orchestration : câblage du graphe, Send, interrupt, exécution d'un thread.

Le checkpointer PostgreSQL et son sérialiseur strict sont dans `checkpointer.py`.
"""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointMetadata
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import get_runtime
from langgraph.types import (
    Command,
    Durability,
    RetryPolicy,
    Send,
    StateSnapshot,
    StateUpdate,
    interrupt,
)

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
from cdg.application.observation import NoTelemetry
from cdg.application.state import AnalystInput, ContractState
from cdg.domain import audit, expiry, masking, policy, resumption
from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig, RetrySettings
from cdg.domain.models import DOMAINS, Clause, Domain, NodeFailure
from cdg.domain.version import CodeVersion
from cdg.ports.engine import ThreadError
from cdg.ports.llm import LLMProvider, LLMQuotaError, LLMTransientError
from cdg.ports.locks import ContractBusy
from cdg.ports.retriever import Retriever
from cdg.ports.telemetry import Telemetry

log = logging.getLogger(__name__)

# Checkpoints écrits avant l'étape suivante (LangGraph 1.2.12, `langgraph/types.py`,
# `Durability`) : par défaut (« async »), l'écriture court pendant l'étape suivante, et
# un processus tué à ce moment perd ou tronque son dernier checkpoint ; l'analyse ne peut
# plus reprendre (ADR 005, arrêt propre et reprise).
DURABILITY: Durability = "sync"


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
                {
                    "domain": d,
                    "clauses": state["clauses"],
                    "analysis_date": state["analysis_date"],
                },
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
        human, error = policy.review(
            interrupt(request),
            state.get("verdicts", []),
            decision_config,
            state.get("analyse_par"),
            analysis_allows_override=policy.analysis_allows_override(
                state, decision_config
            ),
        )
        # acceptée : policy.review ne rend alors aucun motif de refus
        if human is not None:
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
    builder.add_node(
        "grade", partial(crag.grade, llm=llm, max_passes=config.crag.max_passes)
    )
    builder.add_node("rewrite", partial(crag.rewrite, llm=llm))
    builder.add_node("generate", crag.generate)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges("grade", read_crag_route, ["rewrite", "generate"])
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=False)


# Le CRAG tourne comme un graphe racine, sans rien hériter de l'exécution du parent.
# Invoqué dans un analyste, il recevrait sinon la configuration ambiante du parent, dont
# sa durabilité (DURABILITY, « sync ») ; compilé sans checkpointer, il attendait alors en
# fin d'étape une écriture jamais lancée (`_put_checkpoint_fut`, `pregel/main.py`), et
# chaque analyste échouait (défaut du 28/09, journal du 01/10). LangGraph 1.2.12
# (`_internal/_config.py`, `ensure_config`) abandonne la configuration ambiante quand
# l'appel fournit son propre fil : sans checkpointer, rien ne s'écrit sous ce fil. Passer
# `durability` explicitement ferait aussi l'affaire, mais LangGraph avertirait alors à
# chaque appel (« no effect when no checkpointer »), hors des journaux JSON.
CRAG_ROOT: RunnableConfig = {"configurable": {"thread_id": "crag"}}


def crag_runner(retriever: Retriever, llm: LLMProvider, config: DecisionConfig) -> Crag:
    """CRAG injecté dans les analystes : le sous-graphe compilé, une fois par type de clause."""
    graph = build_crag_graph(retriever, llm, config)

    def run(
        domain: Domain, clauses: list[Clause], analysis_date: date
    ) -> RetrievalResult:
        return crag.per_clause(
            domain,
            clauses,
            analysis_date,
            lambda state: graph.invoke(state, CRAG_ROOT)["result"],
        )

    return run


class Node(Protocol):
    """Nœud gardé, tel que l'attend `StateGraph.add_node`."""

    def __call__(self, state: Any) -> dict[str, Any]: ...


def retries(policy: RetryPolicy, exc: Exception) -> bool:
    """La RetryPolicy reprendrait-elle cette erreur ? Même règle que LangGraph 1.2.12
    (`pregel/_retry.py`, `_should_retry_on`) : `retry_on` est une liste ou une classe
    d'exceptions, ou un prédicat."""
    retry_on = policy.retry_on
    if isinstance(retry_on, Sequence):
        return isinstance(exc, tuple(retry_on))
    if isinstance(retry_on, type):
        if issubclass(retry_on, Exception):
            return isinstance(exc, retry_on)
        raise TypeError(f"retry_on : classe d'exception attendue, reçu {retry_on!r}")
    return bool(retry_on(exc))


def _retried(policy: RetryPolicy, exc: Exception, attempt: int) -> bool:
    """La RetryPolicy relancera-t-elle le nœud après cette tentative (1 à la première) ?"""
    return retries(policy, exc) and attempt < policy.max_attempts


def _attempt(name: str) -> int:
    """Tentative en cours du nœud, 1 à la première ; renseignée par LangGraph pendant
    l'exécution d'un nœud."""
    info = get_runtime().execution_info
    if info is None:
        raise RuntimeError(f"{name} : hors d'une exécution de nœud")
    return info.node_attempt


def will_retry(name: str, policy: RetryPolicy) -> Callable[[Exception], bool]:
    """Prédicat donné à un nœud qui se replie lui-même après la dernière tentative
    (`explain`) : l'erreur sera-t-elle reprise par la RetryPolicy du nœud ?"""
    return lambda exc: _retried(policy, exc, _attempt(name))


_NO_TELEMETRY = NoTelemetry()  # sans destination : aucune étape tracée


def _node_attempt() -> int | None:
    """Rang de l'exécution du nœud (1 à la première), pour sa trace ; None hors d'une
    exécution du graphe (un nœud appelé directement, dans un test)."""
    try:
        info = get_runtime().execution_info
    except RuntimeError:  # LangGraph : appelé hors d'un contexte d'exécution
        return None
    return info.node_attempt if info is not None else None


def stepped(
    name: str, fn: Callable[[Any], dict[str, Any]], telemetry: Telemetry
) -> Node:
    """Nœud non gardé (`human_review`, où aboutissent les échecs), avec son étape (ADR
    008) ; l'interruption de la revue n'est pas un échec."""

    def node(state: Any) -> dict[str, Any]:
        with telemetry.step(
            name, attempt=_node_attempt(), domain=None, passthrough=(GraphBubbleUp,)
        ):
            return fn(state)

    return node


def guard(
    name: str,
    fn: Callable[[Any], dict[str, Any]],
    *,
    retry: RetryPolicy | None = None,
    on_failure: Callable[[list[NodeFailure]], dict[str, Any]] | None = None,
    telemetry: Telemetry = _NO_TELEMETRY,
) -> Node:
    """Garde d'échec de nœud : une exception devient un `NodeFailure` dans `failures`.

    - le contrôle de LangGraph (interruption, commande) passe au travers ;
    - avec `retry`, une erreur transitoire est relancée tant qu'il reste des tentatives :
      la RetryPolicy du nœud la reprend, et la garde n'intervient qu'après la dernière ;
    - `on_failure` complète la mise à jour d'un nœud à plusieurs sorties (route vers
      l'humain), à partir de tous les échecs connus.
    `explain` se replie lui-même sur le gabarit : sa garde ne voit qu'une erreur d'avant
    l'appel au LLM.
    Chaque exécution a son étape (ADR 008) : l'exception la traverse avant la garde,
    l'étape en garde le type ; le contrôle de LangGraph la ferme sans échec.
    """

    def node(state: Any) -> dict[str, Any]:
        try:
            with telemetry.step(
                name,
                attempt=_node_attempt(),
                domain=state.get("domain"),  # analyste seulement
                passthrough=(GraphBubbleUp,),
            ):
                return fn(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            info = get_runtime().execution_info
            if info is None:  # renseigné par LangGraph pendant l'exécution d'un nœud
                raise RuntimeError(
                    f"garde {name} : hors d'une exécution de nœud"
                ) from exc
            attempt = info.node_attempt  # 1 à la première
            # retry_on : prédicat par défaut de RetryPolicy (erreurs réseau, 5xx...)
            if retry is not None and _retried(retry, exc, attempt):
                raise
            failure = NodeFailure(
                node=name,
                error=type(exc).__name__,
                message=str(exc),
                attempts=attempt,
                domain=state.get("domain"),  # analyste seulement
            )
            update: dict[str, Any] = {"failures": [failure]}
            if on_failure is not None:
                update |= on_failure([*state.get("failures", []), failure])
            return update

    return node


def retry_policy(
    settings: RetrySettings, retry_on: Callable[[Exception], bool] | None = None
) -> RetryPolicy:
    """RetryPolicy réglée par la configuration ; `retry_on` par défaut : celui de LangGraph."""
    options = {} if retry_on is None else {"retry_on": retry_on}
    return RetryPolicy(
        initial_interval=settings.initial_interval_seconds,
        backoff_factor=settings.backoff_factor,
        max_interval=settings.max_interval_seconds,
        max_attempts=settings.max_attempts,
        jitter=settings.jitter,
        **options,
    )


def transient(exc: Exception) -> bool:
    """Erreur passagère du fournisseur LLM (429, 5xx, délai dépassé, connexion refusée),
    traduite par l'adaptateur. Seule reprise sur l'extraction."""
    return isinstance(exc, LLMTransientError)


_LANGGRAPH_DEFAULT = RetryPolicy()  # prédicat par défaut de LangGraph


def analyst_retryable(exc: Exception) -> bool:
    """Prédicat par défaut de LangGraph (réseau, 5xx, erreurs hors bogues…), sauf le quota
    nul, qui n'est jamais passager."""
    return not isinstance(exc, LLMQuotaError) and retries(_LANGGRAPH_DEFAULT, exc)


def current_thread(state: ContractState) -> str:
    """Thread LangGraph de l'exécution en cours, scellé avec le contrat. Sans checkpointer
    (graphe de test), il n'y a pas de thread : l'identifiant du contrat en tient lieu,
    comme dans `run_contract`, où un contrat est un thread."""
    thread = get_config().get("configurable", {}).get("thread_id")
    return state["contract_id"] if thread is None else str(thread)


def build_graph(config: DecisionConfig, deps: Deps) -> StateGraph:
    """Câble les 9 nœuds, dépendances liées et gardes d'échec ; graphe non compilé.

    Tout nœud est gardé, sauf `human_review`, où aboutissent les échecs. Un nœud à
    plusieurs sorties en échec route vers l'humain (ESCALADE) ; un analyste en échec
    ne fait pas échouer le fan-out, `decision_gate` escalade ; l'échec de l'extraction
    est escaladé par `verify_extraction`.
    """
    retry = retry_policy(config.analyst_retry, retry_on=analyst_retryable)
    extraction_retry = retry_policy(config.extraction_retry, retry_on=transient)
    explain_retry = retry_policy(config.explain_retry, retry_on=transient)
    # chaque nœud gardé a son étape (ADR 008)
    guarded = partial(guard, telemetry=deps.telemetry)
    builder = StateGraph(ContractState)
    builder.add_node(
        "validate_input",
        guarded(
            "validate_input",
            partial(validate_input, decision_config=config),
            on_failure=escalate,
        ),
    )
    builder.add_node(
        "extract_clauses",
        guarded(
            "extract_clauses",
            partial(extract_clauses, extractor=deps.extractor),
            retry=extraction_retry,
        ),
        retry_policy=extraction_retry,
    )
    builder.add_node(
        "verify_extraction",
        guarded(
            "verify_extraction",
            partial(verify_extraction, decision_config=config),
            on_failure=escalate,
        ),
    )
    # input_schema explicite : LangGraph ne le déduit pas d'un partial
    # (voir docs/journal.md)
    builder.add_node(
        "analyst",
        guarded(
            "analyst",
            partial(analyst, crag=deps.crag, decision_config=config),
            retry=retry,
        ),
        input_schema=AnalystInput,
        retry_policy=retry,
    )
    builder.add_node(
        "decision_gate",
        guarded(
            "decision_gate",
            partial(decision_gate, decision_config=config),
            on_failure=escalate,
        ),
    )
    builder.add_node(
        "human_review",
        stepped(
            "human_review",
            partial(human_review, decision_config=config),
            deps.telemetry,
        ),
    )
    # explain se replie sur le gabarit, sauf pour une erreur passagère encore reprise
    builder.add_node(
        "explain",
        guarded(
            "explain",
            partial(
                explain,
                explainer=deps.explainer,
                decision_config=config,
                retrying=will_retry("explain", explain_retry),
            ),
            retry=explain_retry,
        ),
        retry_policy=explain_retry,
    )
    builder.add_node(
        "audit_seal",
        guarded(
            "audit_seal",
            lambda state: audit_seal(
                state,
                audit_store=deps.audit_store,
                clock=deps.clock,
                decision_config=config,
                code_version=deps.code_version,
                thread_id=current_thread(state),
            ),
        ),
    )
    builder.add_node("reject", guarded("reject", reject))

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input", read_route, ["extract_clauses", "reject", "human_review"]
    )
    builder.add_edge("extract_clauses", "verify_extraction")
    builder.add_conditional_edges(
        "verify_extraction",
        route_after_verify,
        ["extract_clauses", "analyst", "human_review"],
    )
    builder.add_edge("analyst", "decision_gate")
    builder.add_conditional_edges(
        "decision_gate", read_route, ["human_review", "explain"]
    )
    builder.add_edge("human_review", "explain")
    builder.add_edge("explain", "audit_seal")
    builder.add_edge("reject", "audit_seal")  # un rejet est scellé aussi
    builder.add_edge("audit_seal", END)
    return builder


@contextmanager
def open_graph(
    config: DecisionConfig, deps: Deps, source: checkpointer.Source
) -> Iterator[CompiledStateGraph]:
    """Graphe compilé avec le checkpointer PostgreSQL (pool du processus, ou chaîne de
    connexion) et le sérialiseur strict."""
    with checkpointer.open_saver(source) as saver:
        yield build_graph(config, deps).compile(checkpointer=saver)


# --- Exécution d'un thread : run, resume, history ---------------------------------


__all__ = ["ThreadError"]  # défini par le port ; relevé ici par la CLI et les tests


def _thread(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def _awaits_human(snapshot: StateSnapshot) -> bool:
    # pas snapshot.next : après une réponse refusée, human_review s'interrompt
    # de nouveau mais langgraph 1.2.12 ne la compte plus dans `next`
    return any(
        task.name == "human_review" and task.interrupts for task in snapshot.tasks
    )


def thread_status(graph: CompiledStateGraph, thread_id: str) -> dict:
    """État d'un thread, sérialisable en JSON ; `demande` : charge utile en attente."""
    snapshot = graph.get_state(_thread(thread_id))
    values = snapshot.values
    human, explanation = values.get("human"), values.get("explanation")
    pending = [i.value for i in snapshot.interrupts]
    return {
        "thread_id": thread_id,
        "analysis_date": values.get("analysis_date"),
        "statut": (
            "suspendu" if pending else "en_cours" if snapshot.next else "termine"
        ),
        "route": values.get("route"),
        "proposed_decision": values.get("proposed_decision"),
        "final_decision": values.get("final_decision"),
        "margin": values.get("margin"),
        "failure_report": values.get("failure_report"),
        "failures": [f.model_dump() for f in values.get("failures", [])],
        "reject_reason": values.get("reject_reason"),
        "input_findings": values.get("input_findings", []),
        "config_hash": values.get("config_hash"),
        "decision_hash": values.get("decision_hash"),
        "chain_hash": values.get("chain_hash"),
        # constats du scellement (code modifié depuis l'analyse…), montrés au réviseur
        "sealing_findings": values.get("sealing_findings", []),
        # reprise escaladée car la configuration avait changé ; relance d'un tel contrat
        "configuration_changee": values.get("configuration_changee"),
        "relance_de": values.get("relance_de"),
        "human": human.model_dump(mode="json") if human else None,
        "analyse_par": values["analyse_par"].model_dump(mode="json")
        if values.get("analyse_par")
        else None,
        "explanation": explanation.model_dump(mode="json") if explanation else None,
        "verdicts": [
            v.model_dump(exclude={"evidence_ids"}) for v in values.get("verdicts", [])
        ],
        "demande": pending[0] if pending else None,
    }


def run_contract(
    graph: CompiledStateGraph,
    contract_id: str,
    raw_text: str,
    parties: Sequence[str] = (),
    *,
    analysis_date: date,
    config: DecisionConfig,
    actor: Actor,
    code: CodeVersion,
    relaunch_of: str | None = None,
) -> dict:
    """Un contrat = un thread ; refuse un thread existant plutôt que d'y cumuler.

    Le texte est masqué AVANT l'invocation : l'entrée du graphe est écrite dans le
    premier checkpoint, le texte original n'atteint donc jamais la base ni le LLM.
    `analysis_date` fixe la date à laquelle les versions des textes sont jugées ; elle est
    écrite dans l'état, donc rejouable. Le contexte d'analyse (empreinte de `config`, qui
    produit la décision, modèles et version du code) est posé dans l'état initial, avant
    tout nœud : c'est lui qui est scellé, comme l'acteur qui lance l'analyse (quatre
    yeux). `relaunch_of` : contrat escaladé pour changement de configuration dont
    celui-ci est la relance, sous la configuration actuelle.
    """
    if graph.get_state(_thread(contract_id)).values:
        raise ThreadError(
            f"le thread {contract_id} existe déjà : utiliser "
            "resume, ou un autre identifiant de contrat"
        )
    masked = masking.mask(raw_text, parties)
    graph.invoke(
        {
            "contract_id": contract_id,
            "raw_text": masked.text,
            "analysis_date": analysis_date,
            **audit.analysis_context(config, code),
            "analyse_par": actor,
            **({} if relaunch_of is None else {"relance_de": relaunch_of}),
        },
        _thread(contract_id),
        durability=DURABILITY,
    )
    return {**thread_status(graph, contract_id), "masquage": masked.counts}


def resume_thread(
    graph: CompiledStateGraph, thread_id: str, answer: dict, *, config: DecisionConfig
) -> dict:
    """Reprise humaine par Command(resume=...) d'un thread en attente. Refusée avant toute
    reprise si la configuration courante n'est pas celle de l'analyse : la décision a été
    produite par l'autre. Seule exception : un contrat escaladé par la reprise
    automatique parce que sa configuration avait changé (`configuration_changee`) ; le
    relecteur le tranche sous la configuration actuelle, et les deux empreintes sont
    scellées."""
    snapshot = _awaiting(graph, thread_id)
    analysed_with, current = (
        snapshot.values.get("config_hash"),
        audit.config_hash(config),
    )
    if analysed_with != current and not snapshot.values.get("configuration_changee"):
        raise ThreadError(
            f"configuration modifiée depuis l'analyse du thread {thread_id} "
            f"(analyse : {analysed_with}, courante : {current}) : reprise refusée. "
            "Relancer l'analyse (run, avec un nouvel identifiant de contrat) ou "
            "restaurer la configuration de l'analyse."
        )
    return _resume(graph, thread_id, answer)


def _awaiting(graph: CompiledStateGraph, thread_id: str) -> StateSnapshot:
    snapshot = graph.get_state(_thread(thread_id))
    if not snapshot.values:
        raise ThreadError(f"thread inconnu : {thread_id}")
    if not _awaits_human(snapshot):
        raise ThreadError(
            f"le thread {thread_id} n'est pas en attente d'une décision humaine"
        )
    return snapshot


def _resume(graph: CompiledStateGraph, thread_id: str, answer: dict) -> dict:
    _awaiting(graph, thread_id)
    graph.invoke(Command(resume=answer), _thread(thread_id), durability=DURABILITY)
    return thread_status(graph, thread_id)


def thread_history(graph: CompiledStateGraph, thread_id: str) -> list[dict]:
    """Checkpoints du thread, du plus ancien au plus récent."""
    history = list(graph.get_state_history(_thread(thread_id)))
    if not history:
        raise ThreadError(f"thread inconnu : {thread_id}")
    return [
        {
            "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
            "step": _metadata(snap).get("step"),
            "source": _metadata(snap).get("source"),
            "created_at": snap.created_at,
            "next": list(snap.next),
            "route": snap.values.get("route"),
            "proposed_decision": snap.values.get("proposed_decision"),
            "final_decision": snap.values.get("final_decision"),
            # retour ciblé posé par une extraction refusée, avant la suivante
            "extraction_feedback": list(snap.values.get("extraction_feedback", [])),
        }
        for snap in reversed(history)
    ]


def list_threads(graph: CompiledStateGraph) -> list[str]:
    """Threads du checkpointer, triés. list() garde le verrou (non réentrant) du
    checkpointer tant que le générateur n'est pas épuisé : collecter d'abord."""
    saver = graph.checkpointer
    if not isinstance(saver, BaseCheckpointSaver):
        raise ThreadError("la liste des threads exige un graphe avec un checkpointer")
    return sorted({c.config["configurable"]["thread_id"] for c in saver.list(None)})


def threads_overview(graph: CompiledStateGraph) -> list[dict]:
    """Statut de chaque thread du checkpointer, avec la date de son premier et de son
    dernier checkpoint : toute la liste des contrats sur un seul graphe ouvert."""
    overview = []
    for thread_id in list_threads(graph):
        history = thread_history(graph, thread_id)
        overview.append(
            {
                **thread_status(graph, thread_id),
                "started_at": history[0]["created_at"],
                "updated_at": history[-1]["created_at"],
            }
        )
    return overview


def _metadata(snapshot: StateSnapshot) -> CheckpointMetadata:
    if snapshot.metadata is None:  # écrites par le checkpointer à chaque étape
        raise ThreadError(f"checkpoint sans métadonnées : {snapshot.config}")
    return snapshot.metadata


def expire_threads(
    graph: CompiledStateGraph,
    older_than: timedelta,
    now: datetime,
    thread_ids: set[str] | None = None,
    *,
    hold: Callable[[str], AbstractContextManager[None]],
    actor: Actor,
) -> list[dict]:
    """Reprend en NO_GO système les threads en attente depuis plus de `older_than` ;
    `actor` : qui lance l'expiration, scellé avec la décision système.

    Chaque thread est expiré sous son verrou (`hold`), puis relu : un thread tranché ou
    suspendu de nouveau entre-temps n'est pas expiré ; un thread verrouillé ailleurs (une
    revue en cours) est laissé, et un avertissement le dit.

    `thread_ids` restreint la recherche : les tests ne touchent ainsi jamais aux
    autres threads en attente de la base. La CLI n'en passe pas.
    """
    found = set(list_threads(graph))
    if thread_ids is not None:
        found &= thread_ids
    expired = []
    for thread_id in sorted(found):
        if _pending_since(graph, thread_id) is None:
            continue
        try:
            with hold(thread_id):
                since = _pending_since(graph, thread_id)  # relu sous le verrou
                if since is None or not expiry.expired(
                    [(thread_id, since)], older_than, now
                ):
                    continue
                # expire continue même si la configuration a changé : NO_GO système,
                # scellé avec les deux empreintes et le constat de configuration modifiée
                answer = expiry.system_decision(now - since, older_than, actor)
                expired.append(_resume(graph, thread_id, answer))
        except ContractBusy as exc:
            log.warning("expiration : contrat %s laissé (%s)", thread_id, exc)
    return expired


def resume_interrupted(
    graph: CompiledStateGraph,
    *,
    hold: Callable[[str], AbstractContextManager[None]],
    record: Callable[[str], int],
    limit: int,
    config: DecisionConfig,
    thread_ids: set[str] | None = None,
) -> list[dict]:
    """Reprend depuis leur dernier checkpoint les threads restés en cours (un processus
    arrêté ou mort au milieu d'une analyse), chacun sous son verrou, relu sous le verrou.
    Un thread verrouillé ailleurs est en cours là-bas : laissé. Le scellement est sans
    doublon (`audit_seal` rejoué, index uniques du journal).

    Chaque reprise est comptée (`record`, durable) avant d'être faite : une reprise qui
    tue le processus est comptée quand même. Plus de reprise, mais une ESCALADE vers la
    revue humaine (`_escalate_interrupted`), avec un avertissement, au-delà de `limit`,
    ou si la configuration du processus (`config`) n'est pas celle du début de l'analyse :
    une analyse n'est jamais reprise sous une autre configuration. Les deux causes,
    cumulées, sont citées toutes deux.

    `thread_ids` restreint la reprise, comme pour l'expiration : les tests ne touchent
    jamais aux autres threads de la base. La CLI et l'interface n'en passent pas."""
    current = audit.config_hash(config)
    found = set(list_threads(graph))
    if thread_ids is not None:
        found &= thread_ids
    resumed = []
    for thread_id in sorted(found):
        if not _in_progress(graph, thread_id):
            continue
        try:
            with hold(thread_id):
                if not _in_progress(graph, thread_id):
                    continue  # fini entre-temps
                attempt = record(thread_id)
                snapshot = graph.get_state(_thread(thread_id))
                analysed_with = snapshot.values.get("config_hash")
                failures = resumption.escalation_failures(
                    snapshot.next, attempt, limit, analysed_with, current
                )
                if failures:
                    _escalate_interrupted(
                        graph,
                        thread_id,
                        failures,
                        resumption.config_change(analysed_with, current),
                        resumption.escalation_record(
                            attempt, limit, analysed_with, current
                        ),
                    )
                else:
                    log.info(
                        "reprise %d de l'analyse interrompue %s", attempt, thread_id
                    )
                    graph.invoke(None, _thread(thread_id), durability=DURABILITY)
                resumed.append(thread_status(graph, thread_id))
        except ContractBusy:
            continue  # en cours dans un autre processus
    return resumed


def _escalate_interrupted(
    graph: CompiledStateGraph,
    thread_id: str,
    failures: list[NodeFailure],
    config_change: dict[str, str | None] | None,
    cause: dict[str, Any],
) -> None:
    """Plus de reprise : l'analyse part en revue humaine, proposée ESCALADE, avec un
    rapport d'échec qui en cite chaque cause (maximum de reprises, configuration modifiée),
    la cause enregistrée (`escalade_reprise`, scellée, vérifiée par le rejeu) et, si la
    configuration a changé, le marqueur `configuration_changee`. En deux mises à
    jour de l'état (LangGraph 1.2.12, `bulk_update_state`) :
    1. `END` sans valeur vide les tâches en attente, en gardant les écritures des tâches
       déjà finies (verdicts des analystes rendus, sous la configuration de l'analyse) ;
    2. l'escalade est écrite au nom de `decision_gate`, dont l'arête lit `route` : la
       revue humaine suit, comme après tout échec de nœud escaladé.
    Puis l'exécution va jusqu'à l'interruption de `human_review` : le contrat attend un
    humain, et n'est plus jamais repris."""
    config = _thread(thread_id)
    snapshot = graph.get_state(config)
    for failure in failures:
        log.warning(
            "analyse %s : plus de reprise automatique, ESCALADE vers la revue humaine "
            "(%s)",
            thread_id,
            failure.message,
        )
    update = {
        "failures": failures,
        **escalate([*snapshot.values.get("failures", []), *failures]),
        "escalade_reprise": cause,
        **({} if config_change is None else {"configuration_changee": config_change}),
    }
    graph.bulk_update_state(
        config, [[StateUpdate(None, END)], [StateUpdate(update, "decision_gate")]]
    )
    graph.invoke(None, config, durability=DURABILITY)


def _in_progress(graph: CompiledStateGraph, thread_id: str) -> bool:
    """Analyse commencée, pas finie, et pas en attente d'un humain."""
    snapshot = graph.get_state(_thread(thread_id))
    return bool(snapshot.next) and not snapshot.interrupts


def _pending_since(graph: CompiledStateGraph, thread_id: str) -> datetime | None:
    """Date de la suspension d'un thread en attente d'un humain ; None sinon."""
    snapshot = graph.get_state(_thread(thread_id))
    if not _awaits_human(snapshot):
        return None
    # dernier checkpoint = suspension : l'attente ne bouge plus ensuite
    if snapshot.created_at is None:  # daté par le checkpointer
        raise ThreadError(f"thread {thread_id} : checkpoint sans date")
    return datetime.fromisoformat(snapshot.created_at)

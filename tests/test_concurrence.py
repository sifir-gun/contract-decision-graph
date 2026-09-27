"""Accès concurrents (audit de publication, 27/09). L'interface web sert ses pages dans le
pool de threads de FastAPI : analyse, décision humaine et expiration passent l'une après
l'autre, sous le verrou unique du service des contrats ; les lectures restent
concurrentes ; les stockages en mémoire du mode démonstration supportent les accès
simultanés. Vrai graphe, checkpointer en mémoire, doublures du LLM et du CRAG."""

import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from doubles import (
    ANALYSIS_DATE,
    CONTRACT_TEXT,
    FIXED_NOW,
    FakeCrag,
    FixedExtractor,
    MemoryAuditStore,
    clauses,
    make_deps,
)
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint
from web_helpers import BASE_URL, PENDING_TEXT, csrf

from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine, memory_opener
from cdg.adapters.web.app import create_app
from cdg.application.service import ContractService
from cdg.domain import audit
from cdg.domain.config import load_config
from cdg.ports.engine import ThreadError

CONFIG = load_config()
WAIT = 10  # secondes : borne de toute attente, pour qu'un échec ne bloque pas la suite
PAUSE = 0.5  # secondes : le temps laissé à un appel concurrent pour se manifester
MARK = "Annexe 9 - Sans objet."  # texte dont l'extraction reste bloquée
SLOW_TEXT = f"{CONTRACT_TEXT}\n{MARK}\n"
ANSWER = {
    "decision": "NO_GO",
    "reviewer": "Camille Relectrice",
    "reason": "tentative d'instruction dans le contrat",
    "overrides_block": False,
}


class BlockingExtractor:
    """Extraction qui s'arrête, pour un texte marqué, jusqu'à `release` : l'analyse reste
    en cours, verrou tenu, le temps d'observer ce qui se passe à côté."""

    def __init__(self) -> None:
        self.inner = FixedExtractor(clauses())
        self.started, self.release = threading.Event(), threading.Event()

    def __call__(self, raw_text: str, feedback: list[str]):
        if MARK in raw_text:
            self.started.set()
            if not self.release.wait(WAIT):
                raise TimeoutError("extraction jamais libérée")
        return self.inner(raw_text, feedback)


def background(call: Callable[[], Any]) -> Future:
    """Un appel dans un thread à lui ; le `Future` garde son résultat ou son exception."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(call)
    pool.shutdown(wait=False)
    return future


def running(future: Future, seconds: float) -> bool:
    """Encore en cours après `seconds` secondes ?"""
    try:
        future.exception(timeout=seconds)
    except TimeoutError:
        return True
    return False


def make_service(extractor=None, opener=None) -> ContractService:
    store = MemoryAuditStore()
    deps = make_deps(extractor, FakeCrag(()), audit_store=store)
    engine = LangGraphEngine(
        CONFIG,
        opener or memory_opener(CONFIG),
        EngineDeps(
            run=lambda: deps,
            resume=lambda: deps,
            expire=lambda: deps,
            read=lambda: deps,
        ),
    )
    return ContractService(
        engine=engine,
        audit_store=lambda: store,
        config=CONFIG,
        today=lambda: ANALYSIS_DATE,
        now=lambda: FIXED_NOW,
    )


@contextmanager
def analysis_in_progress(service, extractor) -> Iterator[Future]:
    """Analyse du contrat `c-lent`, arrêtée dans l'extraction ; libérée à la sortie."""
    analysis = background(lambda: service.analyse(SLOW_TEXT, contract_id="c-lent"))
    try:
        assert extractor.started.wait(WAIT), "l'analyse n'a pas atteint l'extraction"
        yield analysis
    finally:
        extractor.release.set()


@contextmanager
def frequent_switches() -> Iterator[None]:
    """Bascule entre threads bien plus souvent (5 ms par défaut) : une course qu'un
    verrou ne ferme pas se produit alors presque à coup sûr."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


# --- service : modifications en série, lectures concurrentes ---------------------------------


class PausingGraph:
    """Graphe dont chaque lecture d'un thread encore vide attend une autre lecture
    concurrente, `PAUSE` au plus. Sans verrou, deux analyses du même contrat passent
    ensemble la vérification d'existence de `run_contract`, puis lancent toutes deux le
    graphe sur le même thread."""

    def __init__(self, graph, barrier: threading.Barrier) -> None:
        self._graph, self._barrier = graph, barrier

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    def get_state(self, config, *args, **kwargs):
        snapshot = self._graph.get_state(config, *args, **kwargs)
        if not snapshot.values:
            try:
                self._barrier.wait(PAUSE)
            except threading.BrokenBarrierError:
                pass  # aucune lecture concurrente : l'analyse continue seule
        return snapshot


def pausing_opener(barrier: threading.Barrier):
    base = memory_opener(CONFIG)

    @contextmanager
    def open_graph(deps):
        with base(deps) as graph:
            yield PausingGraph(graph, barrier)

    return open_graph


def test_deux_analyses_simultanees_du_meme_contrat_une_seule_creee():
    service = make_service(opener=pausing_opener(threading.Barrier(2)))
    calls = [
        background(lambda: service.analyse(CONTRACT_TEXT, contract_id="c-double"))
        for _ in range(2)
    ]
    errors = [call.exception(timeout=WAIT) for call in calls]
    [error] = [e for e in errors if e is not None]
    assert isinstance(error, ThreadError) and "c-double existe déjà" in str(error)
    [done] = [call.result() for call, e in zip(calls, errors, strict=True) if e is None]
    assert done["statut"] == "termine"
    assert [row["thread_id"] for row in service.contracts()] == ["c-double"]
    assert [entry["thread_id"] for entry in service.journal()] == ["c-double"]


@pytest.mark.parametrize("action", ["analyse", "decide", "expire"])
def test_une_modification_attend_la_fin_de_l_analyse_en_cours(action):
    extractor = BlockingExtractor()
    service = make_service(extractor)
    service.analyse(PENDING_TEXT, contract_id="c-attente")  # en attente de revue
    calls = {
        "analyse": lambda: service.analyse(CONTRACT_TEXT, contract_id="c-autre"),
        "decide": lambda: service.decide("c-attente", ANSWER),
        "expire": lambda: service.expire(timedelta(hours=1)),
    }
    with analysis_in_progress(service, extractor) as analysis:
        waiting = background(calls[action])
        assert running(waiting, PAUSE), f"{action} lancé pendant l'analyse en cours"
    waiting.result(timeout=WAIT)  # terminée, sans erreur, après l'analyse
    assert analysis.result(timeout=WAIT)["statut"] == "termine"


def test_lectures_pendant_une_analyse_en_cours():
    extractor = BlockingExtractor()
    service = make_service(extractor)
    service.analyse(CONTRACT_TEXT, contract_id="c-fini")
    with analysis_in_progress(service, extractor) as analysis:
        reads = background(
            lambda: (
                service.contracts(),
                service.dossier("c-fini"),
                service.history("c-fini"),
                service.journal(),
                service.verify(),
                service.replay("c-fini"),
            )
        )
        rows, dossier, _, journal, report, replay = reads.result(timeout=WAIT)
        assert {row["thread_id"]: row["etat"] for row in rows} == {
            "c-fini": "termine",
            "c-lent": "en_cours",
        }
        assert dossier["etat"] == "termine" and len(journal) == 1
        assert report.ok and replay["identique"]
    assert analysis.result(timeout=WAIT)["statut"] == "termine"


# --- stockages en mémoire du mode démonstration ---------------------------------------------


def test_checkpointer_en_memoire_supporte_lectures_et_ecritures_simultanees():
    with memory_opener(CONFIG)(make_deps()) as graph:
        saver = graph.checkpointer
    written, errors = threading.Event(), []

    def write() -> None:
        try:
            for i in range(300):
                config = {"configurable": {"thread_id": f"t-{i}", "checkpoint_ns": ""}}
                saver.put(config, empty_checkpoint(), {}, {})
        finally:
            written.set()

    def read() -> None:
        while not written.is_set():
            try:
                list(saver.list(None))
                saver.get_tuple({"configurable": {"thread_id": "absent"}})
            except RuntimeError as exc:  # dictionnaire modifié pendant son parcours
                errors.append(exc)
                return

    with frequent_switches():
        threads = [threading.Thread(target=write), threading.Thread(target=read)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
    assert errors == []
    threads_ids = {t.config["configurable"]["thread_id"] for t in saver.list(None)}
    assert len(threads_ids) == 300


ACCESSES = [
    "get_tuple",
    "list",
    "put",
    "put_writes",
    "delete_thread",
    "get_delta_channel_history",
]


@pytest.mark.parametrize("access", ACCESSES)
def test_chaque_acces_au_checkpointer_en_memoire_attend_son_verrou(access):
    with memory_opener(CONFIG)(make_deps()) as graph:
        saver = graph.checkpointer
    config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}
    stored = saver.put(config, empty_checkpoint(), {}, {})
    calls = {
        "get_tuple": lambda: saver.get_tuple(stored),
        "list": lambda: list(saver.list(None)),
        "put": lambda: saver.put(config, empty_checkpoint(), {}, {}),
        "put_writes": lambda: saver.put_writes(stored, [("canal", 1)], "tache"),
        "delete_thread": lambda: saver.delete_thread("t"),
        "get_delta_channel_history": lambda: saver.get_delta_channel_history(
            config=stored, channels=[]
        ),
    }
    with saver._storage_lock:  # tenu par le test : l'accès doit l'attendre
        waiting = background(calls[access])
        assert running(waiting, PAUSE / 2), f"{access} sans verrou"
    waiting.result(timeout=WAIT)


def test_journal_en_memoire_supporte_les_ajouts_simultanes():
    store = MemoryAuditStore()

    def append(i: int):
        record = audit.build_record(
            {
                "contract_id": f"c-{i}",
                **audit.analysis_context(CONFIG),
                "reject_reason": "texte trop court",
            },
            thread_id=f"c-{i}",
            sealing_config_hash=audit.config_hash(CONFIG),
            sealed_at=FIXED_NOW,
        )
        return store.append(lambda head: audit.seal(record, head))

    with frequent_switches(), ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))
    entries = store.entries()
    assert len(entries) == 40 and audit.verify_chain(entries).ok
    assert sorted(e.id for e in entries) == list(range(1, 41))


# --- interface web : pages servies pendant une analyse ---------------------------------------


def post_analysis(web: TestClient, token: str, text: str, identifier: str):
    data = {"csrf": token, "source": "texte", "texte": text, "identifiant": identifier}
    return web.post("/analyse", data=data)


def web_client(service) -> TestClient:
    # dans un bloc `with`, toutes les requêtes partagent une seule boucle d'événements,
    # comme sous uvicorn (starlette/testclient.py, _portal_factory)
    return TestClient(create_app(service), base_url=BASE_URL, follow_redirects=False)


def test_interface_sert_les_lectures_pendant_une_analyse_en_cours():
    extractor = BlockingExtractor()
    service = make_service(extractor)
    service.analyse(CONTRACT_TEXT, contract_id="c-fini")
    pages = [
        "/",
        "/contrats/c-fini",
        "/contrats/c-fini/rejeu",
        "/journal",
        "/journal/verification",
        "/static/style.css",
    ]
    with web_client(service) as web:
        token = csrf(web)
        analysis = background(lambda: post_analysis(web, token, SLOW_TEXT, "c-lent"))
        try:
            assert extractor.started.wait(WAIT), (
                "l'analyse n'a pas atteint l'extraction"
            )
            reads = background(lambda: [web.get(page).status_code for page in pages])
            assert not running(reads, WAIT / 5), "pages bloquées par l'analyse en cours"
            assert reads.result() == [200] * len(pages)
        finally:
            extractor.release.set()
        assert analysis.result(timeout=WAIT).status_code == 303


def test_interface_deux_analyses_du_meme_contrat_la_seconde_refusee():
    extractor = BlockingExtractor()
    service = make_service(extractor)
    with web_client(service) as web:
        token = csrf(web)
        first = background(lambda: post_analysis(web, token, SLOW_TEXT, "c-double"))
        try:
            assert extractor.started.wait(WAIT), (
                "l'analyse n'a pas atteint l'extraction"
            )
            second = background(
                lambda: post_analysis(web, token, SLOW_TEXT, "c-double")
            )
            running(second, PAUSE)  # le temps d'arriver au service
        finally:
            extractor.release.set()
        assert first.result(timeout=WAIT).status_code == 303
        refused = second.result(timeout=WAIT)
    assert refused.status_code == 409
    assert "le thread c-double existe déjà" in refused.text
    assert [row["thread_id"] for row in service.contracts()] == ["c-double"]

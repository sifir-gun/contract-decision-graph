"""Processus réels des tests de concurrence sur PostgreSQL (`tests/test_verrous.py`) :
chaque fonction tourne dans un processus lancé par `multiprocessing` (méthode spawn), avec
son propre moteur, son propre checkpointer et ses propres verrous, comme deux réplicas.

Les fonctions sont au niveau du module : un processus lancé par spawn les importe par
leur nom. Le résultat revient par une file : ("ok", statut) ou ("erreur", type, message).
"""

from doubles import FakeCrag, FixedExtractor, clauses, make_deps

from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.adapters.postgres.locks import PostgresContractLocks
from cdg.domain.config import load_config

WAIT = 30  # secondes


class GatedExtractor:
    """Extraction qui signale son début, puis attend l'autorisation de finir."""

    def __init__(self, started, release):
        self.inner = FixedExtractor(clauses())
        self.started, self.release = started, release

    def __call__(self, raw_text, feedback):
        self.started.set()
        if not self.release.wait(WAIT):
            raise TimeoutError("extraction jamais libérée")
        return self.inner(raw_text, feedback)


def engine(conninfo: str, journal: str, extractor) -> LangGraphEngine:
    config = load_config()
    deps = make_deps(
        extractor, FakeCrag(()), audit_store=PostgresAuditStore(conninfo, table=journal)
    )
    return LangGraphEngine(
        config,
        lambda d: orchestrator.open_graph(config, d, conninfo),
        EngineDeps(
            run=lambda: deps,
            resume=lambda: deps,
            expire=lambda: deps,
            read=lambda: deps,
        ),
        PostgresContractLocks(lambda: conninfo),
    )


def analyse(conninfo, journal, thread_id, text, started, release, results) -> None:
    """Analyse d'un contrat par un « réplica » ; l'extraction attend `release`."""
    from datetime import date

    try:
        extractor = GatedExtractor(started, release)
        status = engine(conninfo, journal, extractor).run(
            thread_id, text, (), date(2026, 9, 25)
        )
        results.put(("ok", status["statut"]))
    except Exception as exc:  # noqa: BLE001 : rendue au test par la file
        results.put(("erreur", type(exc).__name__, str(exc)))

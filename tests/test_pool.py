"""Pool de connexions PostgreSQL (décision du 28/09) : un pool par processus pour
app_role, partagé par le checkpointer, le journal d'audit, la recherche et les verrous de
contrat. Le verrou compte dans sa taille ; un verrou de session ne suit jamais une
connexion rendue ; pool épuisé : erreur explicite, 503 dans l'interface."""

import psycopg
import pytest
from doubles import ANALYSIS_DATE, CONTRACT_TEXT, FakeCrag, HashEmbedder, make_deps
from psycopg.rows import dict_row
from test_audit_store import Sealer, record
from web_helpers import BASE_URL, memory_service

from cdg import cli
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine
from cdg.adapters.postgres import connexions, rag_store
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.adapters.postgres.locks import PostgresContractLocks, contract_lock_key
from cdg.adapters.postgres.resumes import PostgresResumeCounter
from cdg.adapters.web.app import create_app
from cdg.domain.config import load_config
from cdg.ports.connections import ConnectionsExhausted
from cdg.ports.locks import ContractBusy

CONFIG = load_config()


# --- réglages ----------------------------------------------------------------------------------


def test_pool_regle_pour_le_checkpointer_et_sans_verrou_oublie():
    pool = connexions.open_pool("", max_size=7, timeout=3.0, open=False)
    assert (pool.min_size, pool.max_size, pool.timeout) == (1, 7, 3.0)
    # réglages qu'exige PostgresSaver de langgraph-checkpoint-postgres
    assert pool.kwargs == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
    }
    assert pool._reset is connexions.release_session_locks
    assert pool._check is not None  # connexion vérifiée avant d'être prêtée


@pytest.mark.parametrize("size", [0, -1])
def test_taille_de_pool_invalide_refusee(size):
    with pytest.raises(ValueError, match="taille du pool"):
        connexions.open_pool("", max_size=size, open=False)


@pytest.fixture
def pool(pg):
    opened = connexions.open_pool(pg.app, max_size=connexions.DEFAULT_SIZE, timeout=5.0)
    yield opened
    opened.close()


# --- chaque usager du pool ---------------------------------------------------------------------


@pytest.mark.pg
def test_journal_d_audit_par_le_pool(pool, journal):
    store = PostgresAuditStore(pool, table=journal)
    first = store.append(Sealer(record("c-1")))
    second = store.append(Sealer(record("c-2")))
    assert second.prev_hash == first.chain_hash
    assert [e.thread_id for e in store.entries()] == ["c-1", "c-2"]


@pytest.mark.pg
def test_recherche_par_le_pool(pool):
    retriever = rag_store.PgvectorRetriever(pool, HashEmbedder())
    assert isinstance(
        retriever.search("financier", "délai", kind="delai_paiement", k=1), list
    )


@pytest.mark.pg
def test_verrous_par_le_pool_exclusifs(pool, thread_id):
    locks = PostgresContractLocks(lambda: pool)
    with locks.hold(thread_id), pytest.raises(ContractBusy), locks.hold(thread_id):
        pass
    with locks.hold(thread_id):  # rendu au pool, relâché
        pass


@pytest.mark.pg
def test_verrou_de_session_oublie_relache_au_retour_de_la_connexion(pg, thread_id):
    small = connexions.open_pool(pg.app, max_size=1, timeout=5.0)
    key = contract_lock_key(thread_id)
    try:
        with small.connection() as conn:  # verrou pris, jamais rendu par l'usager
            conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        with psycopg.connect(pg.app, autocommit=True) as other:
            row = other.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()
            assert row == (True,)  # le pool l'a relâché en reprenant la connexion
            other.execute("SELECT pg_advisory_unlock(%s)", (key,))
    finally:
        small.close()


@pytest.mark.pg
def test_analyse_complete_par_le_pool_de_taille_par_defaut(pool, thread_id, journal):
    deps = make_deps(
        crag=FakeCrag(()), audit_store=PostgresAuditStore(pool, table=journal)
    )
    engine = LangGraphEngine(
        CONFIG,
        lambda d: orchestrator.open_graph(CONFIG, d, pool),
        EngineDeps(
            run=lambda: deps,
            resume=lambda: deps,
            expire=lambda: deps,
            read=lambda: deps,
        ),
        PostgresContractLocks(lambda: pool),
        PostgresResumeCounter(lambda: pool),
    )
    status = engine.run(thread_id, CONTRACT_TEXT, (), ANALYSIS_DATE)
    assert status["statut"] == "termine" and status["chain_hash"]
    assert engine.status(thread_id)["final_decision"] == status["final_decision"]


# --- pool épuisé ---------------------------------------------------------------------------------


@pytest.mark.pg
def test_pool_epuise_erreur_explicite(pg, journal):
    small = connexions.open_pool(pg.app, max_size=1, timeout=0.2)
    store = PostgresAuditStore(small, table=journal)
    try:
        with small.connection(), pytest.raises(ConnectionsExhausted, match="saturée"):
            store.entries()
    finally:
        small.close()


class Exhausted:
    """Service dont la liste se heurte à un pool épuisé."""

    def __init__(self, service):
        self._service = service

    def __getattr__(self, name):
        return getattr(self._service, name)

    def contracts(self, **_):
        raise ConnectionsExhausted()


def test_interface_rend_503_quand_le_pool_est_epuise():
    from fastapi.testclient import TestClient

    web = TestClient(create_app(Exhausted(memory_service())), base_url=BASE_URL)
    response = web.get("/")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "Base de données saturée" in response.text


# --- CLI ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        ([], None, connexions.DEFAULT_SIZE),
        ([], "9", 9),
        (["--connexions", "4"], "9", 4),
    ],
)
def test_cli_taille_du_pool_option_ou_environnement(monkeypatch, argv, env, expected):
    if env is None:
        monkeypatch.delenv("CDG_CONNEXIONS", raising=False)
    else:
        monkeypatch.setenv("CDG_CONNEXIONS", env)
    assert cli.build_parser().parse_args([*argv, "list"]).connexions == expected


def test_cli_taille_du_pool_invalide_refusee(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--connexions", "0", "list"])
    assert exc.value.code == 2

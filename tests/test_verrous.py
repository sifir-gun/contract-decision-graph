"""Verrous de contrat (phase Kubernetes, point 1) : deux processus qui créent le même
contrat en même temps n'en créent qu'un, la seconde demande reçoit une erreur claire. Le
verrou vaut aussi pour la revue humaine et l'expiration, et se libère si le processus
meurt (verrou consultatif de session de PostgreSQL)."""

import multiprocessing
from datetime import UTC, datetime, timedelta

import processus
import psycopg
import pytest
from doubles import CONTRACT_TEXT
from psycopg import sql
from test_service import PENDING_TEXT, answer, make_service

from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.postgres.audit_store import lock_key
from cdg.adapters.postgres.locks import PostgresContractLocks, contract_lock_key
from cdg.adapters.postgres.resumes import PostgresResumeCounter
from cdg.ports.locks import ContractBusy

SPAWN = multiprocessing.get_context("spawn")
WAIT = 60  # secondes : démarrage d'un processus Python compris


# --- verrou local : démonstration et tests ---------------------------------------------------


def test_verrou_local_exclusif_par_contrat():
    locks = LocalContractLocks()
    with locks.hold("c1"):
        busy = pytest.raises(ContractBusy, match="c1 est en cours de traitement")
        with busy, locks.hold("c1"):
            pass
        with locks.hold("c2"):  # un autre contrat : indépendant
            pass
    with locks.hold("c1"):  # relâché à la sortie du bloc
        pass


def test_verrou_local_relache_sur_exception():
    locks = LocalContractLocks()
    with pytest.raises(RuntimeError), locks.hold("c1"):
        raise RuntimeError
    with locks.hold("c1"):
        pass


def test_cle_du_verrou_stable_sur_64_bits_et_distincte_du_journal():
    key = contract_lock_key("c1")
    assert key == contract_lock_key("c1") and key != contract_lock_key("c2")
    assert -(2**63) <= key < 2**63
    assert key != lock_key("c1")  # espace de noms distinct du verrou du journal d'audit


# --- le moteur prend le verrou pour chaque modification ---------------------------------------


def test_revue_refusee_tant_que_le_contrat_est_verrouille():
    locks = LocalContractLocks()
    service = make_service(locks=locks)
    service.analyse(PENDING_TEXT, contract_id="c-attente")
    with locks.hold("c-attente"), pytest.raises(ContractBusy):
        service.decide("c-attente", answer())
    assert service.decide("c-attente", answer())["statut"] == "termine"


def test_analyse_refusee_tant_que_le_contrat_est_verrouille():
    locks = LocalContractLocks()
    service = make_service(locks=locks)
    with locks.hold("c1"), pytest.raises(ContractBusy):
        service.analyse(CONTRACT_TEXT, contract_id="c1")
    assert service.contracts() == []  # rien de créé


def test_expiration_laisse_un_contrat_verrouille_et_le_dit(caplog):
    caplog.set_level("WARNING", logger="cdg")
    locks = LocalContractLocks()
    later = datetime.now(UTC) + timedelta(days=3)
    service = make_service(locks=locks, now=lambda: later)
    service.analyse(PENDING_TEXT, contract_id="c-attente")
    with locks.hold("c-attente"):
        _, expired = service.expire(timedelta(hours=24))
    assert expired == []
    assert "c-attente" in caplog.text and "en cours de traitement" in caplog.text
    assert [r["etat"] for r in service.contracts()] == ["en_attente"]
    _, expired = service.expire(timedelta(hours=24))  # verrou rendu : expiré
    assert [s["final_decision"] for s in expired] == ["NO_GO"]


# --- PostgreSQL : deux connexions, deux processus réels -----------------------------------------


@pytest.mark.pg
def test_verrou_postgres_exclusif_entre_connexions(pg, thread_id):
    first, second = (
        PostgresContractLocks(lambda: pg.app),
        PostgresContractLocks(lambda: pg.app),
    )
    with first.hold(thread_id), pytest.raises(ContractBusy), second.hold(thread_id):
        pass
    with second.hold(thread_id):
        pass


class Replica:
    """Un processus d'analyse, qui garde ses événements en vie jusqu'à la fin du test : en
    mode spawn, un objet partagé que le parent libère avant que l'enfant l'ait repris
    disparaît sous lui (sémaphore nommé supprimé, FileNotFoundError chez l'enfant)."""

    def __init__(self, pg, journal, thread_id, results, *, release_now=False):
        self.started, self.release = SPAWN.Event(), SPAWN.Event()
        if release_now:
            self.release.set()
        events = (self.started, self.release, results)
        self.process = SPAWN.Process(
            target=processus.analyse,
            args=(pg.app, journal, thread_id, CONTRACT_TEXT, *events),
        )
        self.process.start()


def sealed(pg, journal) -> list[str]:
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            sql.SQL("SELECT thread_id FROM {}").format(sql.Identifier(journal))
        ).fetchall()
    return [row[0] for row in rows]


@pytest.mark.pg
def test_deux_processus_creent_le_meme_contrat_un_seul_cree(pg, thread_id, journal):
    results = SPAWN.Queue()
    first = Replica(pg, journal, thread_id, results)
    assert first.started.wait(WAIT), "le premier réplica n'a pas atteint l'extraction"
    second = Replica(pg, journal, thread_id, results, release_now=True)
    second.process.join(WAIT)
    refused = results.get(timeout=WAIT)
    first.release.set()
    first.process.join(WAIT)
    done = results.get(timeout=WAIT)
    assert refused[:2] == ("erreur", "ContractBusy")
    assert f"le contrat {thread_id} est en cours de traitement" in refused[2]
    assert done == ("ok", "termine")
    third = Replica(pg, journal, thread_id, results, release_now=True)
    third.process.join(WAIT)
    late = results.get(timeout=WAIT)
    assert late[:2] == ("erreur", "ThreadError") and "existe déjà" in late[2]
    assert sealed(pg, journal) == [thread_id]  # scellé une seule fois


@pytest.mark.pg
def test_verrou_relache_quand_le_processus_meurt(pg, thread_id, journal):
    results = SPAWN.Queue()
    replica = Replica(pg, journal, thread_id, results)
    assert replica.started.wait(WAIT), "le réplica n'a pas atteint l'extraction"
    locks = PostgresContractLocks(lambda: pg.app)
    with pytest.raises(ContractBusy), locks.hold(thread_id):
        pass
    replica.process.kill()  # mort brutale, sans nettoyage : la session se ferme
    replica.process.join(WAIT)
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while True:  # le serveur voit la connexion fermée presque aussitôt
        try:
            with PostgresContractLocks(lambda: pg.app).hold(thread_id):
                break
        except ContractBusy:
            if datetime.now(UTC) > deadline:
                raise


@pytest.mark.pg
def test_analyse_d_un_processus_tue_reprise_par_un_autre_scellee_une_fois(
    pg, thread_id, journal
):
    from doubles import FakeCrag, FixedExtractor, clauses, make_deps

    from cdg.adapters.langgraph import orchestrator
    from cdg.adapters.postgres.audit_store import PostgresAuditStore
    from cdg.domain.config import load_config

    results = SPAWN.Queue()
    replica = Replica(pg, journal, thread_id, results)
    assert replica.started.wait(WAIT), "le réplica n'a pas atteint l'extraction"
    replica.process.kill()  # meurt au milieu de l'analyse, verrou relâché avec lui
    replica.process.join(WAIT)
    config = load_config()
    deps = make_deps(
        FixedExtractor(clauses()),
        FakeCrag(()),
        audit_store=PostgresAuditStore(pg.app, table=journal),
    )
    options = {
        "hold": PostgresContractLocks(lambda: pg.app).hold,
        "record": PostgresResumeCounter(lambda: pg.app).record,
        "limit": config.interrupted.max_resumes,
        "thread_ids": {thread_id},  # jamais les autres threads de la base
    }
    with orchestrator.open_graph(config, deps, pg.app) as graph:
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        resumed = orchestrator.resume_interrupted(graph, **options)
        while not resumed and datetime.now(UTC) < deadline:  # verrou vu relâché
            resumed = orchestrator.resume_interrupted(graph, **options)
        assert [(s["thread_id"], s["statut"]) for s in resumed] == [
            (thread_id, "termine")
        ]
        assert orchestrator.resume_interrupted(graph, **options) == []
    assert sealed(pg, journal) == [thread_id]  # scellé une seule fois

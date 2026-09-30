"""Reprises d'une analyse interrompue, comptées (phase Kubernetes, ajout à la PR A).

Une analyse qui fait planter le processus serait reprise à chaque redémarrage, avec un
appel au LLM à chaque fois. Chaque reprise est comptée par contrat, durablement (table
`contract_resumes`, migration 006) ; au-delà de `interrupted.max_resumes`
(config/decision.yaml), le contrat n'est plus repris : ESCALADE vers la revue humaine, avec
un rapport d'échec qui cite le nombre de reprises, et un avertissement au journal.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
import yaml
from doubles import ACTEUR_ANALYSTE, CONTRACT_TEXT, FakeCrag
from pydantic import ValidationError
from test_arret import Death, DyingOnce
from test_service import answer, make_service

from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.demo.resumes import LocalResumeCounter
from cdg.adapters.langgraph.engine import memory_opener
from cdg.adapters.postgres import connexions, migrations
from cdg.adapters.postgres.resumes import PostgresResumeCounter
from cdg.domain import resumption
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config

CONFIG = load_config()
LIMIT = CONFIG.interrupted.max_resumes


# --- domaine ------------------------------------------------------------------------------


def test_reprise_permise_jusqu_au_maximum_compris():
    assert [resumption.exhausted(n, 3) for n in (1, 2, 3, 4, 5)] == [
        False,
        False,
        False,
        True,
        True,
    ]
    with pytest.raises(ValueError, match="rang de reprise"):
        resumption.exhausted(0, 3)


def test_echec_cite_le_nombre_de_reprises_et_l_etape_en_cours():
    failure = resumption.failure(("analyst", "analyst"), 4, 3)
    assert (failure.node, failure.error) == ("analyst", "AnalyseInterrompue")
    assert failure.attempts == 4  # la première exécution et trois reprises
    assert "3 reprises" in failure.message and "maximum : 3" in failure.message
    assert "analyst" in failure.message


# --- configuration --------------------------------------------------------------------------


def test_maximum_de_reprises_dans_la_configuration():
    assert LIMIT == 3
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["interrupted"]["max_resumes"] = 0
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate(data)


# --- compteur en mémoire (démonstration, tests) ----------------------------------------------


def test_compteur_local_par_contrat():
    counter = LocalResumeCounter()
    assert [counter.record("c1"), counter.record("c1"), counter.record("c2")] == [
        1,
        2,
        1,
    ]


def test_compteur_local_sans_perte_sous_concurrence():
    counter = LocalResumeCounter()
    with ThreadPoolExecutor(8) as pool:
        seen = list(pool.map(lambda _: counter.record("c1"), range(200)))
    assert sorted(seen) == list(range(1, 201))


# --- un contrat qui plante à chaque reprise ---------------------------------------------------


class AlwaysDying:
    """Extraction qui fait « mourir » le processus à chaque appel : un appel au LLM
    payé à chaque fois, sans jamais aboutir."""

    def __init__(self):
        self.calls = 0

    def __call__(self, raw_text, feedback):
        self.calls += 1
        raise Death()


class Restarts:
    """Des redémarrages successifs : un nouveau service à chaque fois, sur le même
    checkpointer et le même compteur (la base, en réel)."""

    def __init__(self, extractor):
        self.extractor, self.opener = extractor, memory_opener(CONFIG)
        self.counter, self.locks = LocalResumeCounter(), LocalContractLocks()

    def service(self):
        return make_service(
            self.extractor, opener=self.opener, resumes=self.counter, locks=self.locks
        )


def test_contrat_qui_plante_a_chaque_reprise_part_en_escalade(caplog):
    extractor = AlwaysDying()
    replicas = Restarts(extractor)
    with pytest.raises(Death):
        replicas.service().analyse(
            CONTRACT_TEXT, contract_id="c-boucle", actor=ACTEUR_ANALYSTE
        )
    for _ in range(LIMIT):  # chaque redémarrage reprend, et meurt de nouveau
        with pytest.raises(Death):
            replicas.service().resume_interrupted()
    assert extractor.calls == 1 + LIMIT
    with caplog.at_level(logging.WARNING, logger="cdg"):
        [escalated] = replicas.service().resume_interrupted()
    assert extractor.calls == 1 + LIMIT  # plus aucun appel au LLM
    assert (escalated["thread_id"], escalated["statut"]) == ("c-boucle", "suspendu")
    service = replicas.service()
    values = service.engine.values("c-boucle")
    assert values["proposed_decision"] == "ESCALADE"
    [failure] = values["failure_report"]["failures"]
    assert failure["error"] == "AnalyseInterrompue"
    assert failure["attempts"] == LIMIT + 1
    assert f"{LIMIT} reprises" in failure["message"]
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "c-boucle" in warning.getMessage() and "ESCALADE" in warning.getMessage()
    # en attente d'un humain : plus jamais repris, rien de scellé avant sa décision
    assert service.resume_interrupted() == [] and extractor.calls == 1 + LIMIT
    assert service.journal() == []
    service.decide("c-boucle", answer())
    [sealed] = service.journal()
    assert sealed["thread_id"] == "c-boucle"


class CrashingAnalysts(FakeCrag):
    """CRAG qui fait mourir le processus pour deux domaines, à chaque fois, après avoir
    laissé les deux autres analystes (instantanés) rendre leur verdict."""

    def __call__(self, domain, clauses, analysis_date):
        if domain in {"conformite", "operationnel"}:
            self.calls.append(domain)
            threading.Event().wait(0.2)
            raise Death()
        return super().__call__(domain, clauses, analysis_date)


def test_plantage_pendant_les_analystes_verdicts_rendus_gardes():
    crag = CrashingAnalysts()
    opener, counter = memory_opener(CONFIG), LocalResumeCounter()

    def service():
        return make_service(opener=opener, resumes=counter, crag=crag)

    with pytest.raises(Death):
        service().analyse(
            CONTRACT_TEXT, contract_id="c-analystes", actor=ACTEUR_ANALYSTE
        )
    for _ in range(LIMIT):
        with pytest.raises(Death):
            service().resume_interrupted()
    [escalated] = service().resume_interrupted()
    assert escalated["statut"] == "suspendu"
    values = service().engine.values("c-analystes")
    # les analystes qui avaient rendu leur verdict ne sont ni perdus ni refaits
    assert sorted(v.domain for v in values["verdicts"]) == ["financier", "juridique"]
    assert crag.calls.count("juridique") == crag.calls.count("financier") == 1
    [failure] = values["failure_report"]["failures"]
    assert failure["node"] == "analyst"


def test_une_reprise_qui_aboutit_est_comptee_une_fois():
    counter = LocalResumeCounter()
    service = make_service(DyingOnce(), resumes=counter)
    with pytest.raises(Death):
        service.analyse(CONTRACT_TEXT, contract_id="c-une", actor=ACTEUR_ANALYSTE)
    [resumed] = service.resume_interrupted()
    assert resumed["statut"] == "termine"
    assert counter.record("c-une") == 2  # une seule reprise comptée avant celle-ci


class Spy(LocalResumeCounter):
    def __init__(self):
        super().__init__()
        self.seen: list[str] = []

    def record(self, thread_id: str) -> int:
        self.seen.append(thread_id)
        return super().record(thread_id)


def test_rien_n_est_compte_sans_reprise():
    from test_service import PENDING_TEXT

    spy, locks = Spy(), LocalContractLocks()
    service = make_service(DyingOnce(), resumes=spy, locks=locks)
    with pytest.raises(Death):
        service.analyse(CONTRACT_TEXT, contract_id="c-ailleurs", actor=ACTEUR_ANALYSTE)
    other = make_service(resumes=spy)
    other.analyse(CONTRACT_TEXT, contract_id="c-fini", actor=ACTEUR_ANALYSTE)
    other.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    assert other.resume_interrupted() == [] and spy.seen == []
    with locks.hold(
        "c-ailleurs"
    ):  # en cours dans un autre réplica : ni repris, ni compté
        assert service.resume_interrupted() == []
    assert spy.seen == []


# --- PostgreSQL : migration 006 et compteur durable -----------------------------------------


def grants(pg) -> set[str]:
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'app_role' AND table_name = 'contract_resumes'"
        ).fetchall()
    return {r[0] for r in rows}


@pytest.mark.pg
def test_migration_006_idempotente_droits_sans_suppression(pg, thread_id):
    assert "006_reprises.sql" in [m.name for m in migrations.IDEMPOTENT]
    migrations.apply(pg.admin)  # deux fois : idempotente
    migrations.apply(pg.admin)
    assert grants(pg) == {"SELECT", "INSERT", "UPDATE"}
    PostgresResumeCounter(lambda: pg.app).record(thread_id)
    with (
        psycopg.connect(pg.app) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("DELETE FROM contract_resumes WHERE thread_id = %s", (thread_id,))


def test_migration_006_appliquee_par_le_script_d_initialisation():
    # l'init Docker (et le job tests de la CI) applique toutes les migrations, dans
    # l'ordre ; seule la 001 attend la variable psql du mot de passe d'app_role
    script = migrations.MIGRATIONS.parent / "docker" / "initdb" / "00_migrate.sh"
    assert "for migration in /migrations/*.sql; do" in script.read_text("utf-8")
    sql_text = (migrations.MIGRATIONS / "006_reprises.sql").read_text("utf-8")
    assert ":app_password" not in sql_text and "IF NOT EXISTS" in sql_text


@pytest.mark.pg
def test_compteur_postgres_survit_aux_redemarrages(pg, thread_id):
    first = PostgresResumeCounter(lambda: pg.app)
    assert [first.record(thread_id), first.record(thread_id)] == [1, 2]
    restarted = PostgresResumeCounter(lambda: pg.app)  # un autre processus
    assert restarted.record(thread_id) == 3
    pool = connexions.open_pool(pg.app, max_size=2)
    try:
        assert PostgresResumeCounter(lambda: pool).record(thread_id) == 4
    finally:
        pool.close()


@pytest.mark.pg
def test_compteur_postgres_sans_perte_sous_concurrence(pg, thread_id):
    pool = connexions.open_pool(pg.app, max_size=4)
    counter = PostgresResumeCounter(lambda: pool)
    barrier = threading.Barrier(4)

    def five(_):
        barrier.wait(10)
        return [counter.record(thread_id) for _ in range(5)]

    try:
        with ThreadPoolExecutor(4) as executor:
            seen = [n for batch in executor.map(five, range(4)) for n in batch]
    finally:
        pool.close()
    assert sorted(seen) == list(range(1, 21))


# --- PostgreSQL : de vrais processus, tués à chaque reprise ----------------------------------


@pytest.mark.pg
def test_processus_tue_a_chaque_reprise_contrat_escalade_sans_nouvel_appel(
    pg, thread_id, journal
):
    import processus
    from doubles import make_deps
    from test_verrous import SPAWN, WAIT, Replica, sealed

    from cdg.adapters.langgraph import orchestrator
    from cdg.adapters.postgres.audit_store import PostgresAuditStore
    from cdg.adapters.postgres.locks import PostgresContractLocks

    results = SPAWN.Queue()
    first = Replica(pg, journal, thread_id, results)
    assert first.started.wait(WAIT), "le réplica n'a pas atteint l'extraction"
    first.process.kill()  # meurt au milieu de l'analyse
    first.process.join(WAIT)
    for n in range(1, LIMIT + 1):  # chaque redémarrage reprend l'analyse, qui le tue
        replica = SPAWN.Process(
            target=processus.resume_and_die, args=(pg.app, journal, thread_id)
        )
        replica.start()
        replica.join(WAIT)
        assert replica.exitcode == -9, f"reprise {n} : sortie {replica.exitcode}"
    extractor = AlwaysDying()  # ne doit plus jamais être appelé
    deps = make_deps(
        extractor, FakeCrag(()), audit_store=PostgresAuditStore(pg.app, table=journal)
    )
    options = {
        "hold": PostgresContractLocks(lambda: pg.app).hold,
        "record": PostgresResumeCounter(lambda: pg.app).record,
        "limit": LIMIT,
        "thread_ids": {thread_id},
    }
    with orchestrator.open_graph(CONFIG, deps, pg.app) as graph:
        resumed = []
        for _ in range(50):  # le verrou du dernier processus tué, vu relâché
            resumed = orchestrator.resume_interrupted(graph, **options)
            if resumed:
                break
            threading.Event().wait(0.2)
        assert [(s["thread_id"], s["statut"]) for s in resumed] == [
            (thread_id, "suspendu")
        ]
        values = graph.get_state({"configurable": {"thread_id": thread_id}}).values
    assert extractor.calls == 0 and sealed(pg, journal) == []
    assert values["proposed_decision"] == "ESCALADE"
    [failure] = values["failure_report"]["failures"]
    assert failure["attempts"] == LIMIT + 1 and failure["node"] == "extract_clauses"

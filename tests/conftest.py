"""Fixtures PostgreSQL : les tests marqués `pg` échouent si la base est arrêtée.

Aucun saut silencieux. Pour les exclure volontairement : `uv run pytest -m "not pg"`.
"""

import logging
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from doubles import CONTRACT_TEXT, TEMPLATE, FakeCrag, FixedExtractor, clauses
from psycopg import sql

from cdg import cli, settings
from cdg.adapters import journaux
from cdg.adapters.langgraph import checkpointer
from cdg.adapters.llm import API_KEY_VARS
from cdg.adapters.postgres import conninfo, migrations, rag_store
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.application.deps import Deps
from cdg.domain.config import load_config


def pytest_addoption(parser):
    parser.addoption(
        "--llm",
        action="store_true",
        help="exécute aussi les tests marqués llm (vrai modèle, payant)",
    )


def pytest_report_header(config):
    if config.getoption("--llm"):
        return "tests llm : activés (--llm), vrai modèle"
    return "tests llm : exclus, lancer avec --llm"


def pytest_collection_modifyitems(config, items):
    # exclusion par défaut, comptée comme « deselected » : jamais de saut silencieux,
    # et -m "not pg" ne peut pas activer les tests llm par accident
    if config.getoption("--llm"):
        return
    llm = [item for item in items if item.get_closest_marker("llm")]
    if llm:
        items[:] = [item for item in items if not item.get_closest_marker("llm")]
        config.hook.pytest_deselected(items=llm)


@dataclass(frozen=True)
class Pg:
    admin: str  # chaîne de connexion administrateur : setup-db et ménage des tests
    app: str  # chaîne de connexion app_role : tout le reste


@pytest.fixture(scope="session")
def pg() -> Pg:
    settings.load_env()
    try:
        admin, app = conninfo.admin_conninfo(), conninfo.app_conninfo()
    except settings.SettingsError as exc:
        pytest.fail(
            f'tests PostgreSQL : {exc}. Exclusion volontaire : -m "not pg"',
            pytrace=False,
        )
    try:
        psycopg.connect(admin, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(
            "PostgreSQL injoignable : lancer `docker compose up -d`, "
            'ou exclure volontairement ces tests avec -m "not pg".\n'
            f"{exc}",
            pytrace=False,
        )
    checkpointer.setup_database(admin)  # idempotent : tables du checkpointer et droits
    migrations.apply(admin)  # 002 et suivantes, idempotentes
    rag_store.check_dimension(admin, load_config().embedding.dimension)
    return Pg(admin=admin, app=app)


@pytest.fixture
def thread_id(pg) -> str:
    """Un thread par test, supprimé ensuite avec les droits administrateur."""
    tid = f"test-{uuid.uuid4()}"
    yield tid
    checkpointer.delete_thread(pg.admin, tid)


@pytest.fixture
def journal(pg) -> str:
    """Journal d'audit jetable : même structure (index uniques compris) et mêmes droits
    qu'`audit_decisions`. Les tests ne touchent jamais au vrai journal, qu'on ne peut pas
    purger sans casser la chaîne."""
    name = f"audit_test_{uuid.uuid4().hex[:12]}"
    table, role = sql.Identifier(name), sql.Identifier(settings.APP_ROLE)
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {} (LIKE audit_decisions INCLUDING ALL)").format(
                table
            )
        )
        conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(table, role))
    yield name
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP TABLE {}").format(table))


class ForbiddenAuditStore:
    """Journal réel de la CLI pendant les tests : toute écriture ou lecture échoue. Un
    test qui scelle par la CLI demande la fixture `audit_journal` (journal jetable)."""

    def append(self, seal):
        raise AssertionError(
            "un test ne scelle jamais dans le vrai journal : fixture audit_journal"
        )

    def entries(self):
        raise AssertionError(
            "un test ne lit jamais le vrai journal : fixture audit_journal"
        )


@pytest.fixture(autouse=True)
def _journal_reel_interdit(monkeypatch):
    monkeypatch.setattr(cli, "open_audit_store", ForbiddenAuditStore)


# chargement réel du modèle d'embedding, gardé pour le test qui le vérifie
PROCESS_EMBEDDER = cli.process_embedder


@pytest.fixture(autouse=True)
def _aucun_modele_charge_en_arriere_plan(monkeypatch):
    """`web` en mode réel charge le modèle d'embedding dès le lancement : jamais dans les
    tests, qui n'ont ni les poids (CI) ni besoin de 2,2 Go en mémoire."""
    monkeypatch.setattr(cli, "process_embedder", lambda config: None)


@pytest.fixture(autouse=True)
def _journaux_du_processus_restaures():
    """`cli.main` applique sa configuration de journaux à tout le processus ; elle est
    défaite après chaque test, pour que `caplog` voie encore les journaux des suivants
    (sinon « jamais dans les journaux » passerait à vide)."""
    names = ("cdg", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        n: (lg.level, lg.handlers[:], lg.propagate)
        for n in names
        if (lg := logging.getLogger(n))
    }
    root_level = logging.getLogger().level
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler.formatter, tuple(journaux.FORMATTERS.values())):
            root.removeHandler(handler)
    root.setLevel(root_level)
    for name, (level, handlers, propagate) in saved.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate


@pytest.fixture(autouse=True)
def _cles_d_api_vides(request, monkeypatch):
    """Clés d'API vides, comme en CI, quel que soit le .env du poste (load_env ne remplace
    pas une variable exportée) : aucun test n'appelle le vrai fournisseur, et `resume`
    explique par le gabarit, même en sous-processus. Les tests llm gardent leur clé."""
    if request.node.get_closest_marker("llm"):
        return
    for var in API_KEY_VARS.values():
        monkeypatch.setenv(var, "")


@pytest.fixture
def audit_journal(pg, journal, monkeypatch) -> PostgresAuditStore:
    """La CLI scelle dans un journal jetable (même structure, mêmes droits)."""
    store = PostgresAuditStore(pg.app, table=journal)
    monkeypatch.setattr(cli, "open_audit_store", lambda: store)
    return store


# --- CLI : contrat synthétique et doublures des dépendances réelles -------------------


@pytest.fixture
def contract(tmp_path):
    path = tmp_path / "contrat-synth.txt"
    path.write_text(CONTRACT_TEXT, encoding="utf-8")
    return str(path)


@pytest.fixture
def analysis(monkeypatch, request):
    """Remplace les dépendances réelles (LLM, embedding, corpus) par des doublures ; la
    CLI scelle dans un journal jetable (fixture `audit_journal`, demandée à l'appel)."""

    def use(extractor=None, empty=()):
        extractor = extractor or FixedExtractor(clauses())
        crag = FakeCrag(empty)

        def build(config):
            request.getfixturevalue("audit_journal")
            return Deps(
                extractor=extractor,
                crag=crag,
                audit_store=cli.open_audit_store(),
                clock=cli.now,
                explainer=TEMPLATE,
            )

        monkeypatch.setattr(cli, "build_deps", build)
        return extractor, crag

    return use

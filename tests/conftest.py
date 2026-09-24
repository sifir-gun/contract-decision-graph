"""Fixtures PostgreSQL : les tests marqués `pg` échouent si la base est arrêtée.

Aucun saut silencieux. Pour les exclure volontairement : `uv run pytest -m "not pg"`.
"""

import uuid
from dataclasses import dataclass

import psycopg
import pytest

from cdg import orchestrator, settings


@dataclass(frozen=True)
class Pg:
    admin: str  # chaîne de connexion administrateur : setup-db et ménage des tests
    app: str  # chaîne de connexion app_role : tout le reste


@pytest.fixture(scope="session")
def pg() -> Pg:
    settings.load_env()
    try:
        admin, app = settings.admin_conninfo(), settings.app_conninfo()
    except settings.SettingsError as exc:
        pytest.fail(f'tests PostgreSQL : {exc}. Exclusion volontaire : -m "not pg"', pytrace=False)
    try:
        psycopg.connect(admin, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(
            "PostgreSQL injoignable : lancer `docker compose up -d`, "
            'ou exclure volontairement ces tests avec -m "not pg".\n'
            f"{exc}",
            pytrace=False,
        )
    orchestrator.setup_database(admin)  # idempotent : tables du checkpointer et droits
    return Pg(admin=admin, app=app)


@pytest.fixture
def thread_id(pg) -> str:
    """Un thread par test, supprimé ensuite avec les droits administrateur."""
    tid = f"test-{uuid.uuid4()}"
    yield tid
    orchestrator.delete_thread(pg.admin, tid)

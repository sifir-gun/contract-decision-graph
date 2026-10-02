"""Rotation d'un mot de passe sans redémarrage (ADR 005, PR C3). CloudNativePG applique le
nouveau mot de passe à la base aussitôt ; l'application doit l'utiliser pour chaque
nouvelle connexion. La chaîne de connexion est donc une fonction, relue à chaque connexion
(le fichier de secret monté est relu), pour le pool comme pour les connexions hors pool ;
une chaîne figée au démarrage garderait l'ancien mot de passe (contrôle ci-dessous)."""

import secrets
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import PoolTimeout

from cdg import cli, settings
from cdg.adapters.langgraph import checkpointer
from cdg.adapters.postgres import connexions, conninfo, migrations, rag_store

# --- sans base ------------------------------------------------------------------------------


@pytest.fixture
def mounted(tmp_path, monkeypatch) -> Path:
    """Dossier des secrets montés, comme le kubelet les projette."""
    folder = tmp_path / "secrets"
    folder.mkdir()
    monkeypatch.setattr(settings, "SECRETS_DIR", folder)
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "cdg")
    return folder


def password_of(chain: str) -> str:
    return conninfo_to_dict(chain)["password"]


def test_chaines_de_connexion_relisent_le_secret_a_chaque_appel(mounted):
    (mounted / "APP_DB_PASSWORD").write_text("avant\n")
    (mounted / "POSTGRES_USER").write_text("administrateur\n")
    (mounted / "POSTGRES_PASSWORD").write_text("admin-avant\n")
    assert password_of(conninfo.app_conninfo()) == "avant"
    assert password_of(conninfo.admin_conninfo()) == "admin-avant"
    (mounted / "APP_DB_PASSWORD").write_text("apres\n")
    (mounted / "POSTGRES_PASSWORD").write_text("admin-apres\n")
    assert password_of(conninfo.app_conninfo()) == "apres"
    assert password_of(conninfo.admin_conninfo()) == "admin-apres"


def test_le_pool_du_processus_recoit_la_fonction_pas_une_chaine(mounted, monkeypatch):
    (mounted / "APP_DB_PASSWORD").write_text("avant\n")
    received = []

    class Pool:
        def close(self) -> None:
            pass

    def open_pool(chain, **kwargs):
        received.append(chain)
        return Pool()

    monkeypatch.setattr(cli.connexions, "open_pool", open_pool)
    cli.app_pool.cache_clear()
    try:
        cli.app_pool()
    finally:
        cli.app_pool.cache_clear()
    [chain] = received
    assert callable(chain) and not isinstance(chain, str)
    (mounted / "APP_DB_PASSWORD").write_text("apres\n")
    assert password_of(chain()) == "apres"


def test_commandes_d_administration_passent_la_fonction(monkeypatch):
    """setup-db et ingest : chaque connexion administrateur relit ses secrets."""
    received: dict[str, object] = {}

    def spy(name, result=None):
        def record(source, *args, **kwargs):
            received[name] = source
            return result

        return record

    monkeypatch.setattr(cli.migrations, "ensure_role_at", spy("role", False))
    monkeypatch.setattr(cli.migrations, "apply", spy("migrations", []))
    monkeypatch.setattr(cli.checkpointer, "setup_database", spy("checkpointer"))
    monkeypatch.setattr(cli.rag_store, "check_dimension", spy("dimension"))
    cli._setup_db(SimpleNamespace())
    assert set(received) == {"role", "migrations", "checkpointer", "dimension"}
    for name, source in received.items():
        assert source is conninfo.admin_conninfo, name
    monkeypatch.setattr(cli.rag_store, "sync", spy("sync", {}))
    monkeypatch.setattr(cli.ingestion, "rows", lambda embedder, words, prefix: [])
    monkeypatch.setattr(cli.fastembed, "FastembedEmbedder", _Embedder)
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", "/tmp")
    cli._ingest(SimpleNamespace())
    assert received["sync"] is conninfo.admin_conninfo


class _Embedder:
    model = "doublure"

    def __init__(self, *args, **kwargs) -> None:
        pass


# --- avec PostgreSQL ------------------------------------------------------------------------


@pytest.fixture
def rotating(pg, tmp_path):
    """Un rôle de test dont le mot de passe vit dans un fichier, comme un secret monté ;
    `chain` relit le fichier à chaque appel, `rotate` change le mot de passe en base puis
    dans le fichier (CloudNativePG, puis le kubelet)."""
    role = f"rotation_{uuid.uuid4().hex[:10]}"
    file = tmp_path / "APP_DB_PASSWORD"
    first = secrets.token_urlsafe(16)
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(first)
            )
        )
    file.write_text(first + "\n")
    calls = []

    def chain() -> str:
        calls.append(1)
        return make_conninfo(pg.admin, user=role, password=file.read_text().strip())

    def rotate() -> None:
        new = secrets.token_urlsafe(16)
        with psycopg.connect(pg.admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(new)
                )
            )
        file.write_text(new + "\n")

    yield SimpleNamespace(chain=chain, rotate=rotate, calls=calls, first=chain())
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s",
            (role,),
        )
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def ready(pool):
    """Pool prêt, une seule connexion ouverte : sollicité plus tôt, il en ouvrirait une
    seconde d'avance, avant la rotation."""
    pool.wait(timeout=5.0)
    assert pool.get_stats()["pool_size"] == 1
    return pool


def current_user(conn) -> str:
    row = conn.execute("SELECT current_user AS role").fetchone()
    return row["role"] if isinstance(row, dict) else row[0]


@pytest.mark.pg
def test_pool_ouvre_ses_nouvelles_connexions_avec_le_mot_de_passe_tourne(rotating):
    pool = ready(connexions.open_pool(rotating.chain, max_size=2, timeout=5.0))
    try:
        with pool.connection() as held:
            role = current_user(held)
            rotating.rotate()
            before = len(rotating.calls)
            # la seule connexion est occupée : le pool en ouvre une nouvelle
            with pool.connection() as fresh:
                assert current_user(fresh) == role
            assert len(rotating.calls) == before + 1
    finally:
        pool.close()


@pytest.mark.pg
def test_controle_une_chaine_figee_garde_l_ancien_mot_de_passe(rotating):
    """Ce que corrige la fonction : figée au démarrage, la chaîne ne peut plus ouvrir de
    nouvelle connexion après la rotation."""
    pool = ready(connexions.open_pool(rotating.first, max_size=2, timeout=2.0))
    try:
        with pool.connection():
            rotating.rotate()
            with pytest.raises(PoolTimeout), pool.connection(timeout=2.0):
                pass
    finally:
        pool.close()


@pytest.mark.pg
def test_connexions_hors_pool_relisent_la_chaine(rotating):
    with connexions.connection(rotating.chain) as conn:
        role = current_user(conn)
    rotating.rotate()
    before = len(rotating.calls)
    with connexions.connection(rotating.chain) as conn:
        assert current_user(conn) == role
    assert connexions.ping(rotating.chain) is True
    with checkpointer.open_saver(rotating.chain) as saver:
        assert current_user(saver.conn) == role
    assert len(rotating.calls) == before + 3  # relue à chaque connexion


@pytest.mark.pg
def test_administration_accepte_la_fonction(pg):
    calls = []

    def admin() -> str:
        calls.append(1)
        return pg.admin

    assert migrations.apply(admin)[0] == "002_rag.sql"
    assert migrations.ensure_role_at(admin, settings.APP_ROLE, _never) is False
    assert rag_store.column_dimension(admin) == 1024
    checkpointer.setup_database(admin)
    assert len(calls) >= 4


def _never() -> str:
    raise AssertionError("mot de passe demandé pour un rôle existant")

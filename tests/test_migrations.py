"""Amorçage d'une base par setup-db (ADR 005, PR C3) : sur une base vide, toutes les
migrations dans l'ordre (001 à 006) ; rien ne change sur une base existante ; relancé, il
ne fait rien. Le rôle applicatif, s'il faut le créer, reçoit un mot de passe déjà haché
côté client (SCRAM-SHA-256, bibliothèque standard) : le mot de passe n'atteint jamais le
serveur, ni aucun journal. Dans le cluster, CloudNativePG gère ce rôle (chart cdg-postgres).
"""

import base64
import logging
import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from cdg.adapters.postgres import migrations

ROOT = Path(__file__).resolve().parents[1]
CANARY = "canari-mot-de-passe-7f2c"


# --- sans base -------------------------------------------------------------------------------


def test_verificateur_scram_au_format_de_postgresql():
    verifier = migrations.scram_sha256(CANARY)
    scheme, rest = verifier.split("$", 1)
    params, keys = rest.split("$")
    iterations, salt = params.split(":")
    stored, server = keys.split(":")
    assert scheme == "SCRAM-SHA-256" and iterations == "4096"
    assert len(base64.b64decode(salt)) == 16
    assert len(base64.b64decode(stored)) == len(base64.b64decode(server)) == 32
    assert CANARY not in verifier
    assert migrations.scram_sha256(CANARY) != verifier  # sel aléatoire


def test_mot_de_passe_hors_ascii_refuse_explicitement():
    """SASLprep n'est pas appliqué : un mot de passe hors ASCII imprimable est refusé,
    jamais haché autrement que ne le ferait PostgreSQL."""
    with pytest.raises(migrations.MigrationError, match="ASCII"):
        migrations.scram_sha256("mot-de-passe-é")


def test_la_001_ne_cree_le_role_que_s_il_n_existe_pas():
    text = (ROOT / "migrations" / "001_audit.sql").read_text(encoding="utf-8")
    assert "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role')" in text
    assert ":'app_password'" not in text  # exécutable par psycopg (setup-db)
    assert "current_setting('cdg.app_password', true)" in text


def test_docker_compose_passe_le_mot_de_passe_a_la_session_de_chaque_migration():
    script = (ROOT / "docker" / "initdb" / "00_migrate.sh").read_text(encoding="utf-8")
    assert "SET cdg.app_password = :'app_password';" in script
    assert "\\i ${migration}" in script


# --- avec PostgreSQL ------------------------------------------------------------------------


@pytest.fixture
def empty_database(pg):
    name = f"cdg_vide_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo(pg.admin, dbname=name)
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def temporary_role(pg):
    name = f"role_test_{uuid.uuid4().hex[:12]}"
    yield name
    with psycopg.connect(pg.admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.pg
def test_meme_hachage_que_libpq_avec_le_meme_sel(pg):
    with psycopg.connect(pg.admin) as conn:
        reference = conn.pgconn.encrypt_password(
            CANARY.encode(), b"app_role", b"scram-sha-256"
        ).decode()
    salt = base64.b64decode(reference.split("$")[1].split(":")[1])
    assert migrations.scram_sha256(CANARY, salt=salt) == reference


class Spy:
    """Connexion espionnée : chaque requête envoyée, texte rendu et paramètres."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        self.sent: list[str] = []

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(self._conn)
        self.sent.append(f"{text} {params!r}")
        return self._conn.execute(query, params)

    def transaction(self):
        return self._conn.transaction()

    def commit(self):
        return self._conn.commit()


@pytest.mark.pg
def test_role_cree_avec_un_mot_de_passe_hache_jamais_en_clair(
    pg, temporary_role, caplog
):
    """Le serveur ne reçoit que le vérificateur SCRAM, et la journalisation des requêtes
    est suspendue le temps de la création : PostgreSQL ne peut journaliser que ce qu'il
    reçoit, et le mot de passe n'en fait jamais partie."""
    caplog.set_level(logging.DEBUG)
    with psycopg.connect(pg.admin) as conn:
        spy = Spy(conn)
        assert migrations.ensure_role(spy, temporary_role, lambda: CANARY) is True
    sent = "\n".join(spy.sent)
    assert "SCRAM-SHA-256$4096:" in sent
    assert "SET LOCAL log_statement = 'none'" in sent
    assert "SET LOCAL log_min_error_statement = 'panic'" in sent
    assert CANARY not in sent and CANARY not in caplog.text
    with psycopg.connect(pg.admin) as conn:
        [(stored,)] = conn.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", (temporary_role,)
        ).fetchall()
    assert stored.startswith("SCRAM-SHA-256$4096:")
    # le mot de passe d'origine ouvre bien une session : le vérificateur est juste
    login = make_conninfo(pg.admin, user=temporary_role, password=CANARY)
    with psycopg.connect(login) as conn:
        assert conn.execute("SELECT current_user").fetchone() == (temporary_role,)


@pytest.mark.pg
@pytest.mark.skipif(
    sys.platform != "linux", reason="trace du protocole de libpq : Linux seulement (CI)"
)
def test_protocole_ne_transporte_jamais_le_mot_de_passe(pg, temporary_role, tmp_path):
    trace = tmp_path / "protocole.trace"
    with psycopg.connect(pg.admin) as conn, trace.open("w") as out:
        conn.pgconn.trace(out.fileno())
        migrations.ensure_role(conn, temporary_role, lambda: CANARY)
        conn.pgconn.untrace()
    sent = trace.read_text(encoding="utf-8", errors="replace")
    assert "SCRAM-SHA-256$4096:" in sent and CANARY not in sent


@pytest.mark.pg
def test_role_existant_intact_mot_de_passe_jamais_demande(pg, temporary_role):
    with psycopg.connect(pg.admin) as conn:
        migrations.ensure_role(conn, temporary_role, lambda: CANARY)

    def never() -> str:
        raise AssertionError("mot de passe demandé pour un rôle existant")

    with psycopg.connect(pg.admin) as conn:
        assert migrations.ensure_role(conn, temporary_role, never) is False


def _catalog(conninfo: str) -> dict:
    with psycopg.connect(conninfo) as conn:
        return {
            "tables": conn.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'public' ORDER BY 1, 2"
            ).fetchall(),
            "droits": conn.execute(
                "SELECT grantee, table_name, privilege_type"
                " FROM information_schema.role_table_grants"
                " WHERE table_schema = 'public' ORDER BY 1, 2, 3"
            ).fetchall(),
            "extensions": conn.execute(
                "SELECT extname FROM pg_extension ORDER BY 1"
            ).fetchall(),
        }


@pytest.mark.pg
def test_base_vide_toutes_les_migrations_dans_l_ordre_puis_rien(empty_database):
    names = [p.name for p in sorted((ROOT / "migrations").glob("0*.sql"))]
    assert names[0] == "001_audit.sql" and names[-1] == "006_reprises.sql"
    assert migrations.apply(empty_database) == names
    before = _catalog(empty_database)
    assert ("app_role", "audit_decisions", "INSERT") in before["droits"]
    assert ("vector",) in before["extensions"]
    # relancé : la 001 n'est pas rejouée, rien ne change
    assert migrations.apply(empty_database) == names[1:]
    assert _catalog(empty_database) == before


@pytest.mark.pg
def test_base_existante_la_001_jamais_rejouee(pg):
    assert migrations.apply(pg.admin)[0] == "002_rag.sql"


@pytest.mark.pg
def test_setup_db_amorce_une_base_vide(empty_database, monkeypatch, capsys):
    from cdg import cli

    database = psycopg.conninfo.conninfo_to_dict(empty_database)["dbname"]
    monkeypatch.setenv("POSTGRES_DB", database)
    monkeypatch.setenv("APP_DB_PASSWORD", os.environ.get("APP_DB_PASSWORD", "x"))
    assert cli.main(["setup-db"]) == 0
    first = capsys.readouterr().out
    assert '"001_audit.sql"' in first
    assert cli.main(["setup-db"]) == 0
    second = capsys.readouterr().out
    assert '"001_audit.sql"' not in second

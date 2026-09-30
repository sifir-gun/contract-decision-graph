"""Archive des configurations (PR d'archivage, ADR 005).

Chaque configuration qui a produit une décision scellée est archivée au scellement, dans
la transaction de l'enregistrement, dans une table en ajout seul indexée par son
empreinte : le rejeu fidèle la retrouve même après un changement du modèle de
configuration (cause du non-rejeu du vrai journal, 30/09).

- Migration 007 : `audit_decisions_configurations`, empreinte en clé primaire ; app_role
  n'a que SELECT et INSERT, jamais UPDATE, DELETE ni TRUNCATE.
"""

import psycopg
import pytest
from psycopg import sql

from cdg.adapters.postgres import migrations

ARCHIVE = "audit_decisions_configurations"
HASH = "a" * 64


def grants(pg, table: str = ARCHIVE) -> set[str]:
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'app_role' AND table_name = %s",
            (table,),
        ).fetchall()
    return {r[0] for r in rows}


# --- migration 007 : table en ajout seul ---------------------------------------------------


@pytest.mark.pg
def test_migration_007_idempotente_droits_sans_modification_ni_suppression(pg):
    assert "007_archive_configurations.sql" in [m.name for m in migrations.IDEMPOTENT]
    migrations.apply(pg.admin)  # deux fois : idempotente
    migrations.apply(pg.admin)
    assert grants(pg) == {"SELECT", "INSERT"}


@pytest.mark.pg
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE {} SET config = '{{}}'::jsonb",
        "DELETE FROM {}",
        "TRUNCATE {}",
    ],
)
def test_app_role_ne_modifie_ni_ne_supprime_une_configuration_archivee(pg, statement):
    with (
        psycopg.connect(pg.app) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(sql.SQL(statement).format(sql.Identifier(ARCHIVE)))


@pytest.mark.pg
@pytest.mark.parametrize("key", ["", "A" * 64, "a" * 63, "sha256:" + "a" * 64])
def test_empreinte_mal_formee_refusee_par_la_table(pg, key):
    with (
        psycopg.connect(pg.admin) as conn,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        conn.execute(
            sql.SQL("INSERT INTO {} (config_hash, config) VALUES (%s, '{{}}')").format(
                sql.Identifier(ARCHIVE)
            ),
            (key,),
        )
        conn.rollback()


def test_migration_007_appliquee_par_le_script_d_initialisation():
    # l'init Docker (et le job tests de la CI) applique toutes les migrations, dans
    # l'ordre ; seule la 001 attend la variable psql du mot de passe d'app_role
    script = migrations.MIGRATIONS.parent / "docker" / "initdb" / "00_migrate.sh"
    assert "for migration in /migrations/*.sql; do" in script.read_text("utf-8")
    text = (migrations.MIGRATIONS / "007_archive_configurations.sql").read_text("utf-8")
    assert ":app_password" not in text and "IF NOT EXISTS" in text

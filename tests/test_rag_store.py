"""Table rag_chunks : migrations idempotentes, dimension, droits d'app_role."""

import psycopg
import pytest

from cdg.adapters.postgres import migrations, rag_store
from cdg.domain.config import load_config

pytestmark = pytest.mark.pg

CONFIG = load_config()


def grants(pg, table: str) -> set[str]:
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'app_role' AND table_name = %s",
            (table,),
        ).fetchall()
    return {r[0] for r in rows}


def test_app_role_en_lecture_seule_sur_le_corpus(pg):
    assert grants(pg, "rag_chunks") == {"SELECT"}


def test_dimension_de_la_colonne_conforme_a_la_configuration(pg):
    assert rag_store.column_dimension(pg.admin) == CONFIG.embedding.dimension == 1024


def test_migrations_idempotentes(pg):
    migrations.apply(pg.admin)
    migrations.apply(pg.admin)
    assert grants(pg, "rag_chunks") == {"SELECT"}
    rag_store.check_dimension(pg.admin, CONFIG.embedding.dimension)


def test_migrations_rejouables_sans_la_001():
    # 001 exige la variable psql app_password : seul l'init Docker l'applique
    names = [m.name for m in migrations.IDEMPOTENT]
    assert names[0].startswith("002_") and not any(n.startswith("001_") for n in names)
    assert names == sorted(names)


def test_dimension_divergente_erreur_explicite(pg):
    with pytest.raises(rag_store.RagStoreError, match="768"):
        rag_store.check_dimension(pg.admin, 768)


def test_app_role_ne_peut_pas_ecrire_dans_le_corpus(pg):
    with (
        psycopg.connect(pg.app) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("DELETE FROM rag_chunks WHERE false")


def test_migration_003_metadonnees_de_version(pg):
    with psycopg.connect(pg.admin) as conn:
        columns = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'rag_chunks'"
            )
        }
    assert {
        "article",
        "chunk_index",
        "valid_from",
        "valid_until",
        "amendment",
        "note",
        "retrieved_at",
    } <= columns

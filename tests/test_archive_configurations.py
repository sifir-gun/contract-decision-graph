"""Archive des configurations (PR d'archivage, ADR 005).

Chaque configuration qui a produit une décision scellée est archivée au scellement, dans
la transaction de l'enregistrement, dans une table en ajout seul indexée par son
empreinte : le rejeu fidèle la retrouve même après un changement du modèle de
configuration (cause du non-rejeu du vrai journal, 30/09).

- Migration 007 : `audit_decisions_configurations`, empreinte en clé primaire ; app_role
  n'a que SELECT et INSERT, jamais UPDATE, DELETE ni TRUNCATE.
- Configuration de l'analyse posée dans l'état par run_contract (sa forme validée),
  archivée au scellement avec celle du processus qui scelle, dans la transaction de
  l'enregistrement.
- JSONB ne garde ni l'ordre des clés ni la forme des nombres : l'empreinte se recalcule
  par la sérialisation canonique, jamais sur le texte relu en base.
"""

import html
import json

import psycopg
import pytest
from doubles import ACTEUR_ANALYSTE, CODE, CONTRACT_TEXT, context
from psycopg import sql
from test_audit import analysed, record
from test_audit_v2 import entry, reviewed, v1_record
from web_helpers import analyse as web_analyse
from web_helpers import client, memory_service

from cdg.adapters.postgres import migrations
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.domain import audit
from cdg.domain.config import load_config

CONFIG = load_config()
OTHER = CONFIG.model_copy(update={"min_margin": 0.06})

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


# --- domaine : empreinte canonique et configurations à archiver ------------------------------


def test_empreinte_d_une_configuration_independante_de_l_ordre_des_cles():
    data = CONFIG.model_dump(mode="json")
    reordered = {key: data[key] for key in sorted(data, reverse=True)}
    assert list(reordered) != list(data)
    assert audit.configuration_hash(reordered) == audit.config_hash(CONFIG)


def test_contexte_d_analyse_porte_la_configuration_validee():
    context_ = audit.analysis_context(CONFIG, CODE)
    assert context_["analysis_config"] == CONFIG.model_dump(mode="json")
    assert (
        audit.configuration_hash(context_["analysis_config"]) == context_["config_hash"]
    )


def test_configurations_a_archiver_analyse_et_scellement():
    state = analysed()  # analysée avec CONFIG
    assert audit.configurations_to_archive(state, CONFIG) == {
        audit.config_hash(CONFIG): CONFIG.model_dump(mode="json")
    }
    both = audit.configurations_to_archive(state, OTHER)  # expiration après changement
    assert set(both) == {audit.config_hash(CONFIG), audit.config_hash(OTHER)}


def test_analyse_anterieure_a_l_archive_seule_la_configuration_du_scellement():
    state = analysed()
    del state["analysis_config"]
    assert set(audit.configurations_to_archive(state, OTHER)) == {
        audit.config_hash(OTHER)
    }  # celle de l'analyse manque : verify le signalera


def test_configuration_de_l_analyse_incoherente_avec_son_empreinte_refusee():
    state = analysed() | {"analysis_config": OTHER.model_dump(mode="json")}
    with pytest.raises(ValueError, match="empreinte"):
        audit.configurations_to_archive(state, CONFIG)


# --- graphe : archivée au scellement -----------------------------------------------------------


def test_contrat_scelle_sa_configuration_archivee():
    service = memory_service()
    service.analyse(CONTRACT_TEXT, contract_id="c-go", actor=ACTEUR_ANALYSTE)
    [entry] = service.audit_store().entries()
    archived = service.audit_store().configurations()
    assert entry.config_hash in archived
    assert audit.configuration_hash(archived[entry.config_hash]) == entry.config_hash


# --- PostgreSQL : texte relu différent, empreinte recalculée -----------------------------------


@pytest.mark.pg
def test_empreinte_recalculee_par_la_forme_canonique_jamais_sur_le_texte_relu(
    pg, journal
):
    store = PostgresAuditStore(pg.app, table=journal)
    written = CONFIG.model_dump(mode="json")
    sealed = record({"contract_id": "c-1", **context(), "reject_reason": "vide"})
    store.append(
        lambda head: audit.seal(sealed, head), {audit.config_hash(CONFIG): written}
    )
    [(key, read)] = store.configurations().items()
    with psycopg.connect(pg.admin) as conn:
        [(text,)] = conn.execute(
            sql.SQL("SELECT config::text FROM {}").format(
                sql.Identifier(f"{journal}_configurations")
            )
        ).fetchall()
    assert list(read) != list(written)  # JSONB a réordonné les clés
    assert text != json.dumps(written)  # le texte relu n'est pas celui écrit
    assert key == audit.configuration_hash(read) == audit.config_hash(CONFIG)


@pytest.mark.pg
def test_journal_jetable_avec_son_archive_jetable(pg, journal):
    assert grants(pg, f"{journal}_configurations") == {"SELECT", "INSERT"}


# --- verify : chaque enregistrement v2 a la configuration de sa décision --------------------

ARCHIVED = {audit.config_hash(CONFIG): CONFIG.model_dump(mode="json")}


def chain_of(*records: dict) -> list[audit.StoredAuditEntry]:
    entries, head = [], audit.GENESIS
    for number, data in enumerate(records, start=1):
        stored = entry(data, head).model_copy(update={"id": number})
        entries.append(stored)
        head = stored.chain_hash
    return entries


def v2() -> dict:
    return record(reviewed()).model_dump(mode="json")


def test_journal_conforme_configurations_archivees_v1_exemptes():
    report = audit.verify_journal(chain_of(v1_record(), v2()), ARCHIVED)
    assert report.ok and not report.archive_fault
    assert (report.count, report.archived, report.v1_exempted) == (2, 1, 1)


def test_v2_sans_sa_configuration_archivee_journal_non_conforme():
    entries = chain_of(v1_record(), v2())
    report = audit.verify_journal(entries, {})
    assert (report.ok, report.archive_fault, report.broken_id) == (False, True, 2)
    assert "non archivée" in report.reason and entries[1].config_hash in report.reason


def test_configuration_archivee_alteree_journal_non_conforme():
    altered = {key: data | {"min_margin": 0.5} for key, data in ARCHIVED.items()}
    report = audit.verify_journal(chain_of(v2()), altered)
    assert (report.ok, report.archive_fault) == (False, True)
    assert "altérée" in report.reason


def test_configuration_archivee_relue_dans_un_autre_ordre_reste_conforme():
    [(key, data)] = ARCHIVED.items()
    reordered = {key: {k: data[k] for k in sorted(data, reverse=True)}}
    assert audit.verify_journal(chain_of(v2()), reordered).ok


def test_chaine_rompue_signalee_avant_l_archive():
    entries = chain_of(v2(), v2() | {"thread_id": "c-2", "contract_id": "c-2"})
    tampered = entries[0].model_copy(update={"prev_hash": "0" * 63 + "1"})
    report = audit.verify_journal([tampered, entries[1]], {})
    assert (report.ok, report.archive_fault, report.broken_id) == (False, False, 1)


def test_verification_de_l_interface_montre_l_archive():
    service = memory_service()
    web = client(service)
    web_analyse(web, CONTRACT_TEXT, identifiant="c-1")
    ok = html.unescape(web.get("/journal/verification").text)
    assert "Configurations archivées : 1" in ok
    assert "enregistrements v1, antérieurs à l'archive : 0" in ok
    service.audit_store().archive.clear()
    refused = html.unescape(web.get("/journal/verification").text)
    assert "Archive des configurations non conforme" in refused
    assert "non archivée" in refused

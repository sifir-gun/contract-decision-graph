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

import dataclasses
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

from cdg import cli
from cdg.adapters.postgres import migrations
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.domain import audit
from cdg.domain.config import load_config
from cdg.domain.version import UNKNOWN, CodeVersion

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


def test_version_d_enregistrement_inconnue_journal_non_conforme():
    report = audit.verify_journal(chain_of(v2() | {"version": 3}), ARCHIVED)
    assert (report.ok, report.archive_fault, report.broken_id) == (False, False, 1)
    assert "version" in report.reason


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


# --- rejeu : sur la configuration archivée, fidèle ou réévaluation ----------------------------

AUTRE_CODE = CodeVersion(commit="0" * 40, image="sha256:" + "1" * 64)
JSON = CONFIG.model_dump(mode="json")


def test_rejeu_sur_la_forme_validee_archivee():
    assert audit.replay(v2(), JSON).identical


def test_rejeu_empreinte_recalculee_par_la_forme_canonique():
    reordered = {key: JSON[key] for key in sorted(JSON, reverse=True)}
    assert audit.replay(v2(), reordered).identical


def test_rejeu_configuration_d_une_autre_empreinte_refuse():
    with pytest.raises(audit.ReplayError, match="configuration différente"):
        audit.replay(v2(), OTHER.model_dump(mode="json"))


def test_rejeu_configuration_illisible_par_le_code_courant_erreur_explicite():
    """Une configuration archivée que le modèle courant ne sait plus lire (champ
    retiré depuis) : le rejeu est impossible, et le dit, sans recalculer l'empreinte."""
    old = JSON | {"champ_retire": 1}
    state = reviewed() | {"config_hash": audit.configuration_hash(old)}
    del state["analysis_config"]
    data = record(state).model_dump(mode="json")
    with pytest.raises(audit.ReplayError, match="illisible par le code courant"):
        audit.replay(data, old)


def sealed_by(code: CodeVersion, *, analysed_by: CodeVersion | None = CODE) -> dict:
    state = reviewed()
    if analysed_by is None:
        del state["code_version"]
    else:
        state["code_version"] = analysed_by.model_dump(mode="json")
    return record(state, code=code).model_dump(mode="json")


def test_rejeu_fidele_meme_commit_meme_configuration():
    assert audit.replay_sense(sealed_by(CODE), CODE)[0] == "fidele"


@pytest.mark.parametrize(
    ("data", "current", "reason"),
    [
        (lambda: v1_record(), CODE, "v1"),
        (lambda: sealed_by(CODE, analysed_by=None), CODE, "antérieure"),
        (lambda: sealed_by(AUTRE_CODE), CODE, "entre l'analyse et le scellement"),
        (
            lambda: sealed_by(
                CodeVersion(commit=UNKNOWN, image=None),
                analysed_by=CodeVersion(commit=UNKNOWN, image=None),
            ),
            CodeVersion(commit=UNKNOWN, image=None),
            "inconnu",
        ),
        (lambda: sealed_by(CODE), CodeVersion(commit=UNKNOWN, image=None), "inconnu"),
        (lambda: sealed_by(CODE), AUTRE_CODE, "autre commit"),
        (
            lambda: sealed_by(AUTRE_CODE, analysed_by=AUTRE_CODE),
            AUTRE_CODE.model_copy(update={"image": None}),
            "autre image",
        ),
    ],
)
def test_reevaluation_des_que_le_code_n_est_pas_prouve_le_meme(data, current, reason):
    sense, why = audit.replay_sense(data(), current)
    assert sense == "reevaluation" and reason in why


def test_image_non_scellee_seul_le_commit_compte():
    """Scellé hors du cluster (aucune image) : rejoué dans le cluster, même commit."""
    assert (
        audit.replay_sense(
            sealed_by(CODE), CODE.model_copy(update={"image": "sha256:" + "2" * 64})
        )[0]
        == "fidele"
    )


# --- service, CLI et interface : sens du rejeu, anomalie ou différence signalée -------------


def pending_service(code=CODE):
    service = memory_service(code_version=code)
    service.analyse(CONTRACT_TEXT, contract_id="c-go", actor=ACTEUR_ANALYSTE)
    return service


def test_rejeu_fidele_identique_par_la_configuration_archivee():
    result = pending_service().replay("c-go")
    assert (result["sens"], result["identique"], result["anomalie"]) == (
        "fidele",
        True,
        False,
    )
    assert result["configuration"] == "archivee"


def falsify(service) -> None:
    """Journal altéré en mémoire : la marge scellée ne correspond plus au calcul."""
    service.audit_store().stored[0].record["decision"]["margin"] = 0.99


def test_rejeu_fidele_different_anomalie_code_1(monkeypatch, capsys):
    service = pending_service()
    falsify(service)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    assert cli.main(["replay", "c-go"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert (error["erreur"], error["sens"], error["identique"]) == (
        "RejeuAnomalie",
        "fidele",
        False,
    )


def test_reevaluation_differente_signalee_code_0(monkeypatch, capsys):
    service = pending_service()
    falsify(service)
    later = dataclasses.replace(service, code_version=AUTRE_CODE)  # code mis à jour
    monkeypatch.setattr(cli, "build_service", lambda config: later)
    assert cli.main(["replay", "c-go"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["sens"], out["identique"], out["anomalie"]) == (
        "reevaluation",
        False,
        False,
    )
    assert "autre commit" in out["motif_du_sens"]


def test_interface_anomalie_en_erreur_reevaluation_en_avertissement():
    service = pending_service()
    falsify(service)
    anomaly = html.unescape(client(service).get("/contrats/c-go/rejeu").text)
    assert "Anomalie" in anomaly and "rejeu fidèle" in anomaly
    later = dataclasses.replace(service, code_version=AUTRE_CODE)
    page = client(later).get("/contrats/c-go/rejeu").text
    assert 'class="warning"' in page and "Réévaluation différente" in html.unescape(
        page
    )


def v1_service():
    """Journal au format v1 (synthétique), sans configuration archivée."""
    service = memory_service()
    data = v1_record()
    service.audit_store().stored.append(
        entry(data, audit.GENESIS).model_copy(update={"id": 1})
    )
    return service, data["thread_id"]


def test_v1_rejoue_par_la_configuration_courante_de_meme_empreinte_en_reevaluation():
    service, thread = v1_service()
    result = service.replay(thread)
    assert result["configuration"] == "courante, même empreinte"
    assert result["sens"] == "reevaluation" and "v1" in result["motif_du_sens"]
    assert result["identique"] and not result["anomalie"]


def test_sans_configuration_archivee_ni_courante_de_meme_empreinte_non_rejouable():
    service, thread = v1_service()
    other = dataclasses.replace(service, config=OTHER)
    with pytest.raises(audit.ReplayError, match="non archivée"):
        other.replay(thread)

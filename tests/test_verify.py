"""Commande verify et critère 8 : modifier un enregistrement en base casse la vérification
de la chaîne ; `--expect-head` détecte une fin de journal tronquée. Journal jetable."""

import json
import uuid

import psycopg
import pytest
from doubles import CONTRACT_TEXT, TEMPLATE, FakeCrag, FixedExtractor, clauses
from psycopg import sql

from cdg import cli
from cdg.adapters.langgraph import checkpointer
from cdg.application.deps import Deps
from cdg.domain import audit

pytestmark = pytest.mark.pg


def verify(capsys, *options):
    code = cli.main(["verify", *options])
    out, err = capsys.readouterr()
    return code, json.loads(out if code == 0 else err)


@pytest.fixture
def sealed(pg, audit_journal, tmp_path, monkeypatch, capsys):
    """Deux contrats analysés et scellés par la CLI dans le journal jetable."""

    def build(config, code_version):
        return Deps(
            extractor=FixedExtractor(clauses()),
            crag=FakeCrag(),
            audit_store=cli.open_audit_store(),
            clock=cli.now,
            explainer=TEMPLATE,
            code_version=code_version,
        )

    monkeypatch.setattr(cli, "build_deps", build)
    contract = tmp_path / "contrat.txt"
    contract.write_text(CONTRACT_TEXT, encoding="utf-8")
    threads = [f"test-{uuid.uuid4()}" for _ in range(2)]
    for tid in threads:
        assert (
            cli.main(
                [
                    "run",
                    str(contract),
                    "--operateur",
                    "analyste-synth",
                    "--contract-id",
                    tid,
                ]
            )
            == 0
        )
    capsys.readouterr()
    yield audit_journal.entries()
    for tid in threads:
        checkpointer.delete_thread(pg.admin, tid)


def admin_execute(pg, journal, statement, *params):
    with psycopg.connect(pg.admin) as conn:
        conn.execute(sql.SQL(statement).format(sql.Identifier(journal)), params)


def test_verify_journal_vide(audit_journal, capsys):
    code, out = verify(capsys)
    assert (code, out) == (
        0,
        {
            "verify": "ok",
            "enregistrements": 0,
            "tete": audit.GENESIS,
            "configurations_archivees": 0,
            "v1_sans_archive": 0,
        },
    )


def test_verify_chaine_intacte(sealed, capsys):
    code, out = verify(capsys)
    assert (code, out["verify"], out["enregistrements"]) == (0, "ok", 2)
    assert out["tete"] == sealed[-1].chain_hash
    assert (out["configurations_archivees"], out["v1_sans_archive"]) == (1, 0)


def test_configuration_retiree_de_l_archive_journal_non_conforme(
    pg, sealed, journal, capsys
):
    admin_execute(pg, f"{journal}_configurations", "DELETE FROM {}")
    code, err = verify(capsys)
    assert (code, err["erreur"], err["maillon_fautif"]) == (
        1,
        "ArchiveNonConforme",
        sealed[0].id,
    )
    assert "non archivée" in err["raison"]


def test_configuration_archivee_alteree_journal_non_conforme(
    pg, sealed, journal, capsys
):
    admin_execute(
        pg,
        f"{journal}_configurations",
        "UPDATE {} SET config = jsonb_set(config, '{{min_margin}}', '0.5')",
    )
    code, err = verify(capsys)
    assert (code, err["erreur"]) == (1, "ArchiveNonConforme")
    assert "altérée" in err["raison"]


def test_8_modifier_la_decision_en_base_casse_la_chaine(pg, sealed, journal, capsys):
    first = sealed[0]
    admin_execute(
        pg,
        journal,
        # accolades doublées : psycopg.sql réserve {} aux identifiants
        "UPDATE {} SET record = jsonb_set(record, '{{decision,final_decision}}', "
        "'\"NO_GO\"') WHERE id = %s",
        first.id,
    )
    code, err = verify(capsys)
    assert (code, err["erreur"], err["maillon_fautif"]) == (1, "ChaineRompue", first.id)
    assert "decision_hash" in err["raison"] and err["enregistrements"] == 2


def test_8_modifier_hors_decision_casse_aussi_la_chaine(pg, sealed, journal, capsys):
    admin_execute(
        pg,
        journal,
        # consommation effacée : hors de la partie décision
        "UPDATE {} SET record = record || '{{\"usage\": []}}'::jsonb WHERE id = %s",
        sealed[1].id,
    )
    code, err = verify(capsys)
    assert (code, err["maillon_fautif"]) == (1, sealed[1].id)
    assert "chain_hash" in err["raison"]


@pytest.mark.parametrize("field", ["code_version", "sealing_code_version"])
def test_modifier_la_version_du_code_scellee_casse_la_chaine(
    pg, sealed, journal, capsys, field
):
    admin_execute(
        pg,
        journal,
        "UPDATE {} SET record = jsonb_set(record, %s::text[], %s::jsonb) WHERE id = %s",
        [field, "commit"],
        json.dumps("0" * 40),
        sealed[0].id,
    )
    code, err = verify(capsys)
    assert (code, err["erreur"], err["maillon_fautif"]) == (
        1,
        "ChaineRompue",
        sealed[0].id,
    )
    assert "chain_hash" in err["raison"]


def test_8_supprimer_un_maillon_du_milieu_casse_la_chaine(pg, sealed, journal, capsys):
    admin_execute(pg, journal, "DELETE FROM {} WHERE id = %s", sealed[0].id)
    code, err = verify(capsys)
    assert (code, err["maillon_fautif"]) == (1, sealed[1].id)


def test_expect_head_conforme(sealed, capsys):
    code, out = verify(capsys, "--expect-head", sealed[-1].chain_hash)
    assert (code, out["verify"]) == (0, "ok")


def test_troncature_detectee_seulement_par_expect_head(pg, sealed, journal, capsys):
    head = sealed[-1].chain_hash  # conservée hors de la base
    admin_execute(pg, journal, "DELETE FROM {} WHERE id = %s", sealed[-1].id)
    code, out = verify(capsys)
    assert (code, out["enregistrements"]) == (0, 1)  # limite : invisible sans tête
    code, err = verify(capsys, "--expect-head", head)
    assert (code, err["erreur"], err["maillon_fautif"]) == (1, "ChaineRompue", None)
    assert "tête de chaîne" in err["raison"]


@pytest.mark.parametrize("value", ["abc", "G" * 64, "0" * 63])
def test_expect_head_invalide_refusee(value, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["verify", "--expect-head", value])
    assert exc.value.code == 2
    assert "64 caractères hexadécimaux" in capsys.readouterr().err

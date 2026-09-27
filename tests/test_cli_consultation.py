"""CLI : consultation par le service des contrats, comme l'interface web. `list` (liste des
contrats), `show` (dossier), `journal` (enregistrements scellés), `replay` (rejeu d'une
décision scellée). Base PostgreSQL de test, journal jetable, doublures du LLM."""

import pytest
from cli_helpers import INSUFFICIENT, run_cli
from doubles import CONTRACT_TEXT

from cdg.domain.audit import GENESIS


@pytest.mark.pg
def test_list_et_filtre_en_attente(pg, thread_id, contract, analysis, capsys):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(capsys, "list")
    assert code == 0
    rows = {r["thread_id"]: r for r in out["contrats"]}
    assert rows[thread_id]["etat"] == "en_attente"
    assert rows[thread_id]["proposed_decision"] == "ESCALADE"
    code, out = run_cli(capsys, "list", "--en-attente")
    assert code == 0
    assert thread_id in [r["thread_id"] for r in out["contrats"]]
    assert {r["etat"] for r in out["contrats"]} == {"en_attente"}


@pytest.mark.pg
def test_show_dossier_d_un_contrat(pg, thread_id, contract, analysis, capsys):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(capsys, "show", thread_id)
    assert code == 0
    assert (out["thread_id"], out["etat"]) == (thread_id, "en_attente")
    assert out["texte_masque"] == CONTRACT_TEXT
    assert {c["kind"] for c in out["clauses"]} >= {"penalites_execution"}
    assert [p["step"] for p in out["parcours"]] == sorted(
        p["step"] for p in out["parcours"]
    )


@pytest.mark.pg
def test_show_thread_inconnu(pg, thread_id, capsys):
    code, err = run_cli(capsys, "show", thread_id)
    assert code == 1
    assert (err["erreur"], err["detail"]) == (
        "ThreadError",
        f"thread inconnu : {thread_id}",
    )


@pytest.mark.pg
def test_journal_puis_replay(
    pg,
    thread_id,
    contract,
    analysis,
    audit_journal,
    capsys,
):
    analysis()
    _, run = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(capsys, "journal")
    assert code == 0
    [entry] = out["enregistrements"]
    assert (entry["thread_id"], entry["final_decision"]) == (thread_id, "GO")
    assert (entry["prev_hash"], entry["chain_hash"]) == (GENESIS, run["chain_hash"])
    code, out = run_cli(capsys, "replay", thread_id)
    assert code == 0
    assert out == {
        "thread_id": thread_id,
        "empreinte_scellee": run["decision_hash"],
        "empreinte_rejouee": run["decision_hash"],
        "identique": True,
        "recalcule": True,
    }


@pytest.mark.pg
def test_replay_d_un_thread_non_scelle(pg, thread_id, audit_journal, capsys):
    code, err = run_cli(capsys, "replay", thread_id)
    assert code == 1
    assert err["erreur"] == "ReplayError"
    assert "aucun enregistrement scellé" in err["detail"]

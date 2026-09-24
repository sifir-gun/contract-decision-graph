"""CLI : sorties JSON, erreurs structurées."""

import json

import pytest

from cdg import cli


def run_cli(capsys, *argv) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, json.loads(out if code == 0 else err)


@pytest.mark.pg
def test_setup_db(pg, capsys):
    code, out = run_cli(capsys, "setup-db")
    assert code == 0
    assert out == {
        "setup_db": "ok",
        "role": "app_role",
        "tables": ["checkpoints", "checkpoint_blobs", "checkpoint_writes"],
        "droits": ["SELECT", "INSERT", "UPDATE"],
    }


def test_commande_obligatoire(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


# --- run, resume, history (mode stub-j2) ---------------------------------------------

from doubles import CONTRACT_TEXT, clauses


@pytest.fixture
def contract(tmp_path):
    """Contrat synthétique et ses clauses déjà extraites (mode stub-j2)."""

    def make(**overrides):
        text = tmp_path / "contrat-synth.txt"
        text.write_text(CONTRACT_TEXT, encoding="utf-8")
        cl = tmp_path / "contrat-synth.clauses.json"
        cl.write_text(
            json.dumps([c.model_dump() for c in clauses(**overrides)], ensure_ascii=False),
            encoding="utf-8",
        )
        return str(text), str(cl)

    return make


def test_aide_annonce_le_mode_stub(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    help_text = capsys.readouterr().out
    assert "stub-j2" in help_text and "INSUFFISANT" in help_text


@pytest.mark.pg
def test_run_suspend_en_escalade_mode_stub(pg, thread_id, contract, capsys):
    text, cl = contract()
    code, out = run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    assert code == 0 and out["mode"] == "stub-j2"
    # CRAG sans corpus : INSUFFISANT partout, donc ESCALADE et revue humaine
    assert (out["thread_id"], out["statut"], out["proposed_decision"]) == (
        thread_id,
        "suspendu",
        "ESCALADE",
    )
    assert out["final_decision"] is None
    assert {v["retrieval_status"] for v in out["verdicts"]} == {"INSUFFISANT"}
    assert out["demande"]["proposed_decision"] == "ESCALADE"


@pytest.mark.pg
def test_run_blocage_dur_termine_en_no_go(pg, thread_id, contract, capsys):
    text, cl = contract(responsabilite_acheteur=None)
    code, out = run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    assert (code, out["statut"], out["final_decision"], out["demande"]) == (
        0,
        "termine",
        "NO_GO",
        None,
    )


@pytest.mark.pg
def test_resume_finalise_puis_history(pg, thread_id, contract, capsys):
    text, cl = contract()
    run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "référentiel insuffisant",
    )
    assert (code, out["mode"], out["statut"], out["final_decision"]) == (
        0,
        "stub-j2",
        "termine",
        "NO_GO",
    )
    assert out["human"]["reviewer"] == "relecteur-synth"

    code, out = run_cli(capsys, "history", thread_id)
    steps = out["checkpoints"]
    assert code == 0 and out["mode"] == "stub-j2"
    assert steps[0]["source"] == "input" and steps[-1]["next"] == []
    assert [s["step"] for s in steps] == sorted(s["step"] for s in steps)  # chronologique
    assert steps[-1]["final_decision"] == "NO_GO"


@pytest.mark.pg
def test_resume_refuse_reste_suspendu_avec_le_motif(pg, thread_id, contract, capsys):
    text, cl = contract()
    run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "ESCALADE",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "je ne sais pas",
    )
    assert (code, out["statut"]) == (0, "suspendu")
    assert "ESCALADE" in out["demande"]["error"]
    # la reprise suivante doit rester possible après un refus
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "référentiel insuffisant",
    )
    assert (code, out["statut"], out["final_decision"]) == (0, "termine", "NO_GO")


@pytest.mark.pg
def test_resume_thread_inconnu(pg, thread_id, capsys):
    code, err = run_cli(
        capsys, "resume", thread_id, "--decision", "NO_GO", "--reviewer", "r", "--reason", "m"
    )
    assert code == 1 and "inconnu" in err["detail"]


@pytest.mark.pg
def test_resume_thread_termine_refuse(pg, thread_id, contract, capsys):
    text, cl = contract(responsabilite_acheteur=None)
    run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    code, err = run_cli(
        capsys, "resume", thread_id, "--decision", "GO", "--reviewer", "r", "--reason", "m"
    )
    assert code == 1 and "attente" in err["detail"]


@pytest.mark.pg
def test_run_refuse_un_thread_existant(pg, thread_id, contract, capsys):
    text, cl = contract()
    run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    code, err = run_cli(capsys, "run", text, "--clauses", cl, "--contract-id", thread_id)
    assert code == 1 and "existe déjà" in err["detail"]


def test_run_fichier_de_clauses_absent(tmp_path, capsys):
    text = tmp_path / "c.txt"
    text.write_text(CONTRACT_TEXT, encoding="utf-8")
    code, err = run_cli(capsys, "run", str(text), "--clauses", str(tmp_path / "absent.json"))
    assert code == 1 and err["erreur"] == "FileNotFoundError"


# --- Échec de nœud (option c au J2) : erreur JSON, code non nul, état lisible -----------


@pytest.mark.pg
def test_echec_de_noeud_erreur_json_et_etat_lisible_par_history(pg, thread_id, tmp_path, capsys):
    text = tmp_path / "contrat.txt"
    text.write_text(CONTRACT_TEXT, encoding="utf-8")
    # clause présente sans citation : le nœud d'extraction échoue (ValidationError)
    broken = [c.model_dump() for c in clauses()]
    broken[0].update(present=True, quote="")
    cl = tmp_path / "contrat.clauses.json"
    cl.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    code, err = run_cli(capsys, "run", str(text), "--clauses", str(cl), "--contract-id", thread_id)
    assert code == 1
    assert err["erreur"] == "ValidationError" and "citation" in err["detail"]

    # l'état reste dans le dernier checkpoint, lisible par history
    code, out = run_cli(capsys, "history", thread_id)
    assert code == 0 and out["checkpoints"]
    assert out["checkpoints"][-1]["next"] == ["extract_clauses"]
    assert out["checkpoints"][-1]["route"] == "extract_clauses"

    # pas en attente d'un humain : resume refuse explicitement
    code, err = run_cli(
        capsys, "resume", thread_id, "--decision", "NO_GO", "--reviewer", "r", "--reason", "m"
    )
    assert code == 1 and "attente" in err["detail"]

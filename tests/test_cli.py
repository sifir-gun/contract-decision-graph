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
    assert out == {"setup_db": "ok", "role": "app_role",
                   "tables": ["checkpoints", "checkpoint_blobs", "checkpoint_writes"],
                   "droits": ["SELECT", "INSERT", "UPDATE"]}


def test_commande_obligatoire(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2

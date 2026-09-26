"""Critère d'acceptation n° 5 : processus tué pendant l'interrupt, reprise par un autre."""

import json
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from doubles import ABSENT, CONTRACT_TEXT, clauses

from cdg.adapters.postgres.audit_store import PostgresAuditStore

pytestmark = pytest.mark.pg
TESTS = Path(__file__).resolve().parent

# lance le graphe puis se tue en pleine suspension, connexion PostgreSQL ouverte
KILLED_RUN = textwrap.dedent("""
    import json, os, signal, sys
    from datetime import date
    text, clauses_path, thread_id, tests_dir = sys.argv[1:5]
    sys.path.insert(0, tests_dir)
    from doubles import FakeCrag, FixedExtractor, make_deps
    from cdg import settings
    from cdg.adapters.langgraph import orchestrator
    from cdg.adapters.postgres import conninfo
    from cdg.domain.config import load_config
    from cdg.domain.models import Clause

    settings.load_env()
    found = [Clause.model_validate(c) for c in json.load(open(clauses_path))]
    # tué pendant la suspension : audit_seal jamais atteint, journal en mémoire
    deps = make_deps(FixedExtractor(found), FakeCrag(empty={"financier"}))
    with orchestrator.open_graph(load_config(), deps, conninfo.app_conninfo()) as graph:
        status = orchestrator.run_contract(
            graph,
            thread_id,
            open(text).read(),
            analysis_date=date(2026, 9, 25),
            config=load_config(),
        )
        print(json.dumps(status["statut"]), flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
    print("jamais atteint", flush=True)
""")

# reprise par la CLI dans un nouveau processus ; le journal jetable du test est fourni par
# ce code de test, jamais par une option de la CLI
RESUME_CLI = textwrap.dedent("""
    import sys
    tests_dir, table, *argv = sys.argv[1:]
    sys.path.insert(0, tests_dir)
    from cdg import cli
    from cdg.adapters.postgres import conninfo
    from cdg.adapters.postgres.audit_store import PostgresAuditStore

    cli.open_audit_store = lambda: PostgresAuditStore(
        conninfo.app_conninfo(), table=table
    )
    sys.exit(cli.main(argv))
""")


def test_5_processus_tue_pendant_l_interrupt_puis_reprise(
    pg, thread_id, journal, tmp_path
):
    text = tmp_path / "contrat.txt"
    text.write_text(CONTRACT_TEXT, encoding="utf-8")
    cl = tmp_path / "contrat.clauses.json"
    # financier INSUFFISANT : un constat que le corpus de la doublure ne justifie pas
    found = clauses(penalites_execution=ABSENT)
    cl.write_text(json.dumps([c.model_dump() for c in found]), encoding="utf-8")

    killed = subprocess.run(
        [sys.executable, "-c", KILLED_RUN, str(text), str(cl), thread_id, str(TESTS)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # code de retour vérifié ci-dessous : -9 attendu
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    assert killed.stdout.splitlines() == ['"suspendu"']  # tué pendant l'interrupt

    resumed = subprocess.run(
        [
            sys.executable,
            "-c",
            RESUME_CLI,
            str(TESTS),
            journal,
            "resume",
            thread_id,
            "--decision",
            "NO_GO",
            "--reviewer",
            "relecteur-synth",
            "--reason",
            "référentiel insuffisant",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # code de retour vérifié ci-dessous
    )
    assert resumed.returncode == 0, resumed.stderr
    out = json.loads(resumed.stdout)
    assert (out["statut"], out["final_decision"]) == ("termine", "NO_GO")
    assert out["human"]["reviewer"] == "relecteur-synth"
    # scellé une fois, par le processus de reprise, dans le journal jetable
    [entry] = PostgresAuditStore(pg.app, table=journal).entries()
    decision = entry.record["decision"]
    assert (entry.thread_id, decision["final_decision"]) == (thread_id, "NO_GO")
    assert decision["human"]["reviewer"] == "relecteur-synth"
    assert out["chain_hash"] == entry.chain_hash

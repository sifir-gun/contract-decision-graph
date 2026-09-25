"""Critère d'acceptation n° 5 : processus tué pendant l'interrupt, reprise par un autre."""

import json
import signal
import subprocess
import sys
import textwrap

import pytest
from doubles import CONTRACT_TEXT, clauses

pytestmark = pytest.mark.pg

# lance le graphe puis se tue en pleine suspension, connexion PostgreSQL ouverte
KILLED_RUN = textwrap.dedent("""
    import json, os, signal, sys
    from datetime import date
    from cdg import settings, stub_j2
    from cdg.adapters.langgraph import orchestrator
    from cdg.adapters.postgres import conninfo
    from cdg.domain.config import load_config

    text, clauses_path, thread_id = sys.argv[1:4]
    settings.load_env()
    with orchestrator.open_graph(load_config(), stub_j2.deps(clauses_path),
                                 conninfo.app_conninfo()) as graph:
        status = orchestrator.run_contract(
            graph, thread_id, open(text).read(), analysis_date=date(2026, 9, 25)
        )
        print(json.dumps(status["statut"]), flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
    print("jamais atteint", flush=True)
""")


def test_5_processus_tue_pendant_l_interrupt_puis_reprise(pg, thread_id, tmp_path):
    text = tmp_path / "contrat.txt"
    text.write_text(CONTRACT_TEXT, encoding="utf-8")
    cl = tmp_path / "contrat.clauses.json"
    cl.write_text(json.dumps([c.model_dump() for c in clauses()]), encoding="utf-8")

    killed = subprocess.run(
        [sys.executable, "-c", KILLED_RUN, str(text), str(cl), thread_id],
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
            "-m",
            "cdg.cli",
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

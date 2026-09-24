"""Critère d'acceptation n° 11 : thread en attente au-delà du délai, NO_GO système."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from doubles import CONTRACT_TEXT, FakeCrag, FixedExtractor, clauses

from cdg import cli, orchestrator
from cdg.config import load_config
from cdg.deps import Deps

pytestmark = pytest.mark.pg

CONFIG = load_config()
DAY = timedelta(hours=24)
# juridique 0,5 et opérationnel 0,7 : marge 0,04, donc revue humaine
LOW_MARGIN = {"responsabilite_fournisseur": 50, "duree_engagement": 48}


def suspend(graph, thread_id: str) -> datetime:
    status = orchestrator.run_contract(graph, thread_id, CONTRACT_TEXT)
    assert status["statut"] == "suspendu"
    return datetime.fromisoformat(
        graph.get_state({"configurable": {"thread_id": thread_id}}).created_at
    )


@pytest.fixture
def graph(pg):
    deps = Deps(extractor=FixedExtractor(clauses(**LOW_MARGIN)), crag=FakeCrag())
    with orchestrator.open_graph(CONFIG, deps, pg.app) as g:
        yield g


def test_11_thread_expire_no_go_systeme_motif_timeout(graph, thread_id):
    since = suspend(graph, thread_id)
    [status] = orchestrator.expire_threads(
        graph, DAY, now=since + DAY + timedelta(hours=1), thread_ids={thread_id}
    )
    assert (status["thread_id"], status["statut"], status["final_decision"]) == (
        thread_id,
        "termine",
        "NO_GO",
    )
    human = status["human"]
    assert (human["source"], human["reviewer"], human["decision"]) == (
        "systeme",
        "systeme:expire",
        "NO_GO",
    )
    assert human["reason"].startswith("timeout : en attente depuis 25 h")
    assert orchestrator.thread_status(graph, thread_id)["statut"] == "termine"


def test_thread_recent_non_expire(graph, thread_id):
    since = suspend(graph, thread_id)
    assert (
        orchestrator.expire_threads(
            graph,
            DAY,
            now=since + DAY,  # pile au délai
            thread_ids={thread_id},
        )
        == []
    )
    assert orchestrator.thread_status(graph, thread_id)["statut"] == "suspendu"


def test_thread_termine_jamais_repris(pg, thread_id):
    deps = Deps(extractor=FixedExtractor(clauses()), crag=FakeCrag())  # GO direct
    with orchestrator.open_graph(CONFIG, deps, pg.app) as g:
        assert orchestrator.run_contract(g, thread_id, CONTRACT_TEXT)["statut"] == "termine"
        far = datetime.now(UTC) + timedelta(days=365)
        assert orchestrator.expire_threads(g, DAY, now=far, thread_ids={thread_id}) == []


def test_thread_ayant_recu_une_reponse_refusee_expire_aussi(graph, thread_id):
    since = suspend(graph, thread_id)
    refused = orchestrator.resume_thread(
        graph, thread_id, {"decision": "ESCALADE", "reviewer": "r", "reason": "m"}
    )
    assert refused["statut"] == "suspendu"
    [status] = orchestrator.expire_threads(graph, DAY, now=since + 2 * DAY, thread_ids={thread_id})
    assert status["final_decision"] == "NO_GO"


def test_cli_expire_sans_effet_sous_le_delai(pg, capsys):
    # délai de 1 000 jours : aucun thread réel ne peut être touché
    assert cli.main(["expire", "--older-than", "1000d"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["mode"], out["older_than"], out["expired"]) == ("stub-j2", "1000d", [])


def test_cli_expire_duree_invalide(capsys):
    assert cli.main(["expire", "--older-than", "vingt-quatre heures"]) == 1
    assert "durée" in json.loads(capsys.readouterr().err)["detail"]

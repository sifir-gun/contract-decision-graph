"""Checkpointer PostgreSQL : droits d'app_role, cycle complet, reprise depuis la base."""

import psycopg
import pytest
from langgraph.types import Command

from cdg import orchestrator
from cdg.config import load_config
from cdg.deps import Deps
from cdg.state import AgentVerdict, HumanDecision
from doubles import FakeCrag, FixedExtractor, clauses

pytestmark = pytest.mark.pg

CONFIG = load_config()
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
# juridique 0,5 et opérationnel 0,7 : score 0,79, marge 0,04 < 0,05
LOW_MARGIN = {"responsabilite_fournisseur": 50, "duree_engagement": 48}
VALID = {"decision": "NO_GO", "reviewer": "relecteur-synth", "reason": "marge trop faible"}


def grants(pg, table: str) -> set[str]:
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'app_role' AND table_name = %s", (table,)).fetchall()
    return {r[0] for r in rows}


def deps() -> Deps:
    return Deps(extractor=FixedExtractor(clauses(**LOW_MARGIN)), crag=FakeCrag())


def thread(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}}


# --- Droits ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", CHECKPOINT_TABLES)
def test_app_role_lit_insere_met_a_jour_sans_supprimer(pg, table):
    assert grants(pg, table) == {"SELECT", "INSERT", "UPDATE"}


def test_app_role_sans_droit_sur_les_migrations_du_checkpointer(pg):
    assert grants(pg, "checkpoint_migrations") == set()


def test_journal_d_audit_toujours_en_ajout_seul(pg):
    assert grants(pg, "audit_decisions") == {"SELECT", "INSERT"}


def test_setup_database_idempotent(pg):
    orchestrator.setup_database(pg.admin)
    assert grants(pg, "checkpoints") == {"SELECT", "INSERT", "UPDATE"}


# --- Cycle complet avec les seuls droits d'app_role ---------------------------------------

def test_4_cycle_complet_run_interrupt_resume_avec_app_role(pg, thread_id):
    with orchestrator.open_graph(CONFIG, deps(), pg.app) as graph:
        out = graph.invoke({"contract_id": thread_id, "raw_text": "Contrat synthétique."},
                           thread(thread_id))
        [pending] = out["__interrupt__"]
        assert (pending.value["proposed_decision"], pending.value["margin"]) == ("GO", 0.04)

    # nouvelle connexion : l'état suspendu est relu depuis PostgreSQL
    with orchestrator.open_graph(CONFIG, deps(), pg.app) as graph:
        state = graph.get_state(thread(thread_id))
        assert state.next == ("human_review",)
        assert all(isinstance(v, AgentVerdict) for v in state.values["verdicts"])
        out = graph.invoke(Command(resume=VALID), thread(thread_id))
        assert "__interrupt__" not in out and out["final_decision"] == "NO_GO"
        assert out["human"] == HumanDecision(**VALID)
        assert len(list(graph.get_state_history(thread(thread_id)))) > 3


def test_app_role_ne_peut_pas_supprimer_un_thread(pg, thread_id):
    with orchestrator.open_graph(CONFIG, deps(), pg.app) as graph:
        graph.invoke({"contract_id": thread_id, "raw_text": "Contrat synthétique."},
                     thread(thread_id))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        orchestrator.delete_thread(pg.app, thread_id)

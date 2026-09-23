"""Graphe compilé, doublures comprises : preuve que Send et le réducteur marchent dans LangGraph."""

from langgraph.types import Send

from cdg.config import load_config
from cdg.deps import Deps
from cdg.orchestrator import build_graph, route_after_verify
from cdg.state import DOMAINS
from doubles import FakeCrag, FixedExtractor, clauses

CONFIG = load_config()
BUDGET = CONFIG.budget.max_tokens_per_contract


def make(clause_overrides=None, statuses=None, crag_tokens=0):
    extractor = FixedExtractor(clauses(**(clause_overrides or {})), tokens_in=500, tokens_out=100)
    crag = FakeCrag(statuses, tokens_in=crag_tokens)
    graph = build_graph(CONFIG, Deps(extractor=extractor, crag=crag)).compile()
    return graph, extractor, crag


def run(raw_text="Contrat synthétique de prestation.", **kwargs):
    graph, extractor, crag = make(**kwargs)
    out = graph.invoke({"contract_id": "c-synth-001", "raw_text": raw_text})
    return out, extractor, crag


def updates(raw_text="Contrat synthétique de prestation.", **kwargs) -> list[tuple[str, dict]]:
    graph, _, _ = make(**kwargs)
    steps = graph.stream({"contract_id": "c-synth-001", "raw_text": raw_text},
                         stream_mode="updates")
    return [(node, update) for step in steps for node, update in step.items()]


# --- Critère d'acceptation n° 1 : fan-out ------------------------------------------

def test_1_fan_out_quatre_verdicts_distincts_aucun_ecrase():
    out, extractor, crag = run(crag_tokens=10)
    assert len(out["verdicts"]) == 4
    assert sorted(v.domain for v in out["verdicts"]) == sorted(DOMAINS)
    # chaque verdict porte la référence de son propre domaine : aucun n'a écrasé l'autre
    assert {v.domain: v.evidence_ids for v in out["verdicts"]} == {
        d: [f"{d}-ref-1"] for d in DOMAINS}
    assert sorted(crag.calls) == sorted(DOMAINS)
    # usage cumulé par le réducteur : 1 extraction + 4 CRAG
    assert sorted(u.node for u in out["usage"]) == sorted(
        ["extract_clauses"] + [f"crag:{d}" for d in DOMAINS])
    assert (out["route"], out["proposed_decision"], out["final_decision"]) == (
        "explain", "GO", "GO")
    assert out["extraction_attempts"] == 1 and len(extractor.calls) == 1


def test_1_quatre_analystes_puis_un_seul_decision_gate():
    ups = updates()
    names = [node for node, _ in ups]
    assert names.count("analyst") == 4 and names.count("decision_gate") == 1
    assert max(i for i, n in enumerate(names) if n == "analyst") < names.index("decision_gate")
    # un analyste ne renvoie que verdicts et usage
    assert all(set(u) == {"verdicts", "usage"} for n, u in ups if n == "analyst")


def test_route_apres_verification_construit_les_send():
    sends = route_after_verify({"route": "analysts", "clauses": clauses()})
    assert all(isinstance(s, Send) and s.node == "analyst" for s in sends)
    assert [s.arg["domain"] for s in sends] == list(DOMAINS)
    assert all(set(s.arg) == {"domain", "clauses"} for s in sends)
    assert route_after_verify({"route": "human_review"}) == "human_review"


# --- Critère d'acceptation n° 2, de bout en bout -----------------------------------

def test_2_blocage_dur_no_go_dans_le_graphe():
    out, _, _ = run(clause_overrides={"responsabilite_acheteur": None})
    assert (out["route"], out["final_decision"]) == ("explain", "NO_GO")
    assert [v.domain for v in out["verdicts"] if v.hard_block] == ["juridique"]


# --- Routes vers human_review (passe-plat au J1) --------------------------------------

def test_marge_faible_route_vers_human_review():
    # juridique 0,5 et opérationnel 0,7 : score 0,79, marge 0,04 < 0,05
    ov = {"responsabilite_fournisseur": 50, "duree_engagement": 48}
    out, _, _ = run(clause_overrides=ov)
    assert (out["route"], out["proposed_decision"], out["margin"]) == ("human_review", "GO", 0.04)
    assert "final_decision" not in out            # écrite par human_review au J2
    assert [n for n, _ in updates(clause_overrides=ov)][-3:] == [
        "human_review", "explain", "audit_seal"]


def test_insuffisant_route_vers_human_review_en_escalade():
    out, _, _ = run(statuses={"conformite": "INSUFFISANT"})
    assert (out["route"], out["proposed_decision"]) == ("human_review", "ESCALADE")


def test_budget_depasse_dans_le_graphe():
    out, _, _ = run(crag_tokens=15_000)           # 4 × 15 000 + 600 d'extraction
    assert (out["route"], out["proposed_decision"]) == ("human_review", "ESCALADE")
    assert out["failure_report"] == {"stage": "budget", "tokens": 60_600, "limit": BUDGET}


# --- Rejet --------------------------------------------------------------------------

def test_rejet_scelle_sans_decision_finale():
    out, extractor, crag = run(raw_text="   ")
    assert (out["route"], out["reject_reason"]) == ("reject", "texte du contrat vide")
    # les canaux à réducteur valent [] même sans écriture
    assert out.get("final_decision") is None and out["verdicts"] == []
    assert extractor.calls == [] and crag.calls == []
    assert [n for n, _ in updates(raw_text="   ")] == ["validate_input", "reject", "audit_seal"]


# --- Structure : arêtes conformes à la spec -------------------------------------------

def test_aretes_du_graphe_conformes_a_la_spec():
    graph, _, _ = make()
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert edges == {
        ("__start__", "validate_input"),
        ("validate_input", "extract_clauses"), ("validate_input", "reject"),
        ("extract_clauses", "verify_extraction"),
        ("verify_extraction", "extract_clauses"), ("verify_extraction", "analyst"),
        ("verify_extraction", "human_review"),
        ("analyst", "decision_gate"),
        ("decision_gate", "explain"), ("decision_gate", "human_review"),
        ("human_review", "explain"),
        ("explain", "audit_seal"), ("reject", "audit_seal"),
        ("audit_seal", "__end__"),
    }

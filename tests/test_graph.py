"""Graphe compilé, doublures comprises : preuve que Send et le réducteur marchent dans LangGraph."""

import pytest
import yaml
from doubles import ABSENT, CONTRACT_TEXT, FakeCrag, FixedExtractor, clauses
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Send

from cdg import orchestrator
from cdg.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.deps import Deps
from cdg.orchestrator import build_graph, route_after_verify, strict_serializer
from cdg.state import DOMAINS, HumanDecision

CONFIG = load_config()
BUDGET = CONFIG.budget.max_tokens_per_contract


def make(clause_overrides=None, statuses=None, crag_tokens=0):
    extractor = FixedExtractor(clauses(**(clause_overrides or {})), tokens_in=500, tokens_out=100)
    crag = FakeCrag(statuses, tokens_in=crag_tokens)
    graph = build_graph(CONFIG, Deps(extractor=extractor, crag=crag)).compile()
    return graph, extractor, crag


def run(raw_text=CONTRACT_TEXT, **kwargs):
    graph, extractor, crag = make(**kwargs)
    out = graph.invoke({"contract_id": "c-synth-001", "raw_text": raw_text})
    return out, extractor, crag


def updates(raw_text=CONTRACT_TEXT, **kwargs) -> list[tuple[str, dict]]:
    graph, _, _ = make(**kwargs)
    steps = graph.stream(
        {"contract_id": "c-synth-001", "raw_text": raw_text}, stream_mode="updates"
    )
    return [(node, update) for step in steps for node, update in step.items()]


# --- Critère d'acceptation n° 1 : fan-out ------------------------------------------


def test_1_fan_out_quatre_verdicts_distincts_aucun_ecrase():
    out, extractor, crag = run(crag_tokens=10)
    assert len(out["verdicts"]) == 4
    assert sorted(v.domain for v in out["verdicts"]) == sorted(DOMAINS)
    # chaque verdict porte la référence de son propre domaine : aucun n'a écrasé l'autre
    assert {v.domain: v.evidence_ids for v in out["verdicts"]} == {
        d: [f"{d}-ref-1"] for d in DOMAINS
    }
    assert sorted(crag.calls) == sorted(DOMAINS)
    # usage cumulé par le réducteur : 1 extraction + 4 CRAG
    assert sorted(u.node for u in out["usage"]) == sorted(
        ["extract_clauses"] + [f"crag:{d}" for d in DOMAINS]
    )
    assert (out["route"], out["proposed_decision"], out["final_decision"]) == (
        "explain",
        "GO",
        "GO",
    )
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


# --- human_review : interrupt() puis reprise par Command(resume=...) --------------------

# juridique 0,5 et opérationnel 0,7 : score 0,79, marge 0,04 < 0,05
LOW_MARGIN = {"responsabilite_fournisseur": 50, "duree_engagement": 48}
THREAD = {"configurable": {"thread_id": "c-synth-001"}}
VALID = {"decision": "NO_GO", "reviewer": "relecteur-synth", "reason": "marge trop faible"}


def start(clause_overrides=None, statuses=None, crag_tokens=0, config=CONFIG):
    """Graphe avec checkpointer mémoire, lancé jusqu'à sa première suspension."""
    extractor = FixedExtractor(clauses(**(clause_overrides or {})), tokens_in=500, tokens_out=100)
    crag = FakeCrag(statuses, tokens_in=crag_tokens)
    graph = build_graph(config, Deps(extractor=extractor, crag=crag)).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    out = graph.invoke({"contract_id": "c-synth-001", "raw_text": CONTRACT_TEXT}, THREAD)
    return graph, out


def request_of(out) -> dict:
    [pending] = out["__interrupt__"]
    return pending.value


def test_4_marge_faible_suspend_et_expose_la_charge_utile():
    graph, out = start(clause_overrides=LOW_MARGIN)
    assert (out["route"], out["proposed_decision"], out["margin"]) == ("human_review", "GO", 0.04)
    assert "final_decision" not in out
    assert graph.get_state(THREAD).next == ("human_review",)
    request = request_of(out)
    assert (request["contract_id"], request["proposed_decision"], request["margin"]) == (
        "c-synth-001",
        "GO",
        0.04,
    )
    assert len(request["verdicts"]) == 4 and "error" not in request


def test_reprise_avec_decision_valide_finalise():
    graph, _ = start(clause_overrides=LOW_MARGIN)
    steps = graph.stream(Command(resume=VALID), THREAD, stream_mode="updates")
    ups = [(node, update) for step in steps for node, update in step.items()]
    assert [n for n, _ in ups] == ["human_review", "explain", "audit_seal"]
    assert set(ups[0][1]) == {"human", "final_decision"}  # seules clés modifiées
    state = graph.get_state(THREAD)
    assert state.next == ()
    assert state.values["final_decision"] == "NO_GO"
    assert state.values["human"] == HumanDecision(**VALID)


def test_reponse_refusee_redemandee_avec_erreur_puis_acceptee():
    graph, _ = start(clause_overrides=LOW_MARGIN)
    out = graph.invoke(Command(resume={**VALID, "decision": "ESCALADE"}), THREAD)
    assert "ESCALADE" in request_of(out)["error"] and "final_decision" not in out
    out = graph.invoke(Command(resume={"decision": "GO"}), THREAD)  # mal formée
    assert request_of(out)["error"].startswith("réponse invalide")
    # plusieurs interrupt() dans le même nœud : appariés par ordre d'appel
    out = graph.invoke(Command(resume=VALID), THREAD)
    assert "__interrupt__" not in out
    assert (out["final_decision"], out["human"].decision) == ("NO_GO", "NO_GO")


def test_insuffisant_suspend_en_escalade():
    _, out = start(statuses={"conformite": "INSUFFISANT"})
    assert (out["route"], out["proposed_decision"]) == ("human_review", "ESCALADE")
    assert request_of(out)["proposed_decision"] == "ESCALADE"


def test_budget_depasse_suspend_avec_rapport_d_echec():
    _, out = start(crag_tokens=15_000)  # 4 × 15 000 + 600 d'extraction
    report = {"stage": "budget", "tokens": 60_600, "limit": BUDGET}
    assert (out["route"], out["proposed_decision"]) == ("human_review", "ESCALADE")
    assert out["failure_report"] == report
    assert request_of(out)["failure_report"] == report


# --- Blocage dur et revue humaine : human_policy.hard_block_review ---------------------

# un blocage dur par domaine qui en a (l'opérationnel n'a que des pénalités)
BLOCKS = {
    "juridique": {"responsabilite_acheteur": None},
    "financier": {"revision_prix": None},
    "conformite": {"accord_traitement_donnees": ABSENT},
}
BLOCKED = BLOCKS["juridique"]


def hard_block_review_config() -> DecisionConfig:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["human_policy"]["hard_block_review"] = True
    return DecisionConfig.model_validate(data)


@pytest.mark.parametrize("domain", BLOCKS)
def test_par_defaut_un_blocage_dur_n_atteint_jamais_human_review(domain):
    assert CONFIG.human_policy.hard_block_review is False
    graph, out = start(clause_overrides=BLOCKS[domain])  # avec checkpointer
    assert "__interrupt__" not in out and graph.get_state(THREAD).next == ()
    assert (out["route"], out["final_decision"]) == ("explain", "NO_GO")
    assert "human_review" not in [n for n, _ in updates(clause_overrides=BLOCKS[domain])]


def test_12_blocage_dur_en_revue_suspend_avec_no_go_propose():
    graph, out = start(clause_overrides=BLOCKED, config=hard_block_review_config())
    assert (out["route"], out["proposed_decision"]) == ("human_review", "NO_GO")
    assert "final_decision" not in out and graph.get_state(THREAD).next == ("human_review",)
    request = request_of(out)
    assert [v["domain"] for v in request["verdicts"] if v["hard_block"]] == ["juridique"]


def test_12_levee_sans_overrides_block_refusee_puis_acceptee_avec_motif():
    graph, _ = start(clause_overrides=BLOCKED, config=hard_block_review_config())
    lift = {
        "decision": "GO",
        "reviewer": "relecteur-synth",
        "reason": "responsabilité plafonnée par avenant synthétique n° 2",
    }
    out = graph.invoke(Command(resume=lift), THREAD)  # sans overrides_block
    assert "overrides_block" in request_of(out)["error"] and "final_decision" not in out
    out = graph.invoke(Command(resume={**lift, "overrides_block": True}), THREAD)
    assert "__interrupt__" not in out
    assert out["final_decision"] == "GO" and out["proposed_decision"] == "NO_GO"
    # la levée est tracée comme telle dans l'état, que audit_seal scellera (J4)
    assert out["human"] == HumanDecision(**lift, overrides_block=True)


def test_12_humain_confirme_le_no_go_sans_levee():
    graph, _ = start(clause_overrides=BLOCKED, config=hard_block_review_config())
    out = graph.invoke(Command(resume=VALID), THREAD)
    assert (out["final_decision"], out["human"].overrides_block) == ("NO_GO", False)


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
        ("validate_input", "extract_clauses"),
        ("validate_input", "reject"),
        ("extract_clauses", "verify_extraction"),
        ("verify_extraction", "extract_clauses"),
        ("verify_extraction", "analyst"),
        ("verify_extraction", "human_review"),
        ("analyst", "decision_gate"),
        ("decision_gate", "explain"),
        ("decision_gate", "human_review"),
        ("human_review", "explain"),
        ("explain", "audit_seal"),
        ("reject", "audit_seal"),
        ("audit_seal", "__end__"),
    }


# --- Masquage avant le graphe : le texte original n'entre jamais dans l'état -------------

PII = "Contact : jeanne.martin@exemple.fr, 01 23 45 67 89, société Acme Industrie.\n"


def test_run_contract_masque_avant_le_graphe():
    extractor = FixedExtractor(clauses())
    graph = build_graph(CONFIG, Deps(extractor=extractor, crag=FakeCrag())).compile(
        checkpointer=InMemorySaver(serde=strict_serializer())
    )
    status = orchestrator.run_contract(
        graph, "c-pii", CONTRACT_TEXT + PII, parties=["Acme Industrie"]
    )
    assert status["masquage"] == {"EMAIL": 1, "TELEPHONE": 1, "PARTIE": 1}
    raw = graph.get_state({"configurable": {"thread_id": "c-pii"}}).values["raw_text"]
    assert "[EMAIL]" in raw and "[TELEPHONE]" in raw and "[PARTIE_1]" in raw
    for original in ("jeanne.martin@exemple.fr", "01 23 45 67 89", "Acme Industrie"):
        assert original not in raw
    # l'extracteur ne reçoit que le texte masqué
    [(received, _)] = extractor.calls
    assert received == raw

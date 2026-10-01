"""audit_seal dans le graphe : chaque fin de parcours scelle exactement un enregistrement,
rien n'est scellé pendant une suspension ; critères 6 (rejeu), 11 (expiration) et 12
(levée de blocage) scellés. Journal en mémoire, même contrat que PostgreSQL."""

from datetime import datetime, timedelta

import pytest
import yaml
from doubles import (
    ABSENT,
    ACTEUR_ANALYSTE,
    ACTEUR_RELECTEUR,
    ANALYSIS_DATE,
    CODE,
    CONTRACT_TEXT,
    FIXED_NOW,
    FakeCrag,
    FixedExtractor,
    MemoryAuditStore,
    clauses,
    context,
    make_deps,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.domain import audit
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config

CONFIG = load_config()
HUMAN = {
    "decision": "NO_GO",
    "acteur": ACTEUR_RELECTEUR.model_dump(mode="json"),
    "reason": "motif",
}


def thread(cid):
    return {"configurable": {"thread_id": cid}}


def compiled(
    store, extractor=None, crag=None, config=CONFIG, checkpointer=True, saver=None
):
    graph = orchestrator.build_graph(config, make_deps(extractor, crag, store))
    if not checkpointer:
        return graph.compile()
    return graph.compile(checkpointer=saver or InMemorySaver(serde=strict_serializer()))


def invoke(graph, cid, text=CONTRACT_TEXT, config=CONFIG):
    graph.invoke(
        {
            "contract_id": cid,
            "raw_text": text,
            "analysis_date": ANALYSIS_DATE,
            **context(config),
        },
        thread(cid),
    )
    return graph.get_state(thread(cid)).values


def decision_of(entry):
    return entry.record["decision"]


# --- Une fin de parcours, un enregistrement ------------------------------------------------


def test_go_scelle_un_enregistrement_et_expose_ses_empreintes():
    store = MemoryAuditStore()
    graph = compiled(store)
    values = invoke(graph, "c-go")
    [entry] = store.entries()
    assert (entry.contract_id, entry.thread_id) == ("c-go", "c-go")
    assert decision_of(entry)["final_decision"] == values["final_decision"] == "GO"
    assert (values["decision_hash"], values["chain_hash"], values["config_hash"]) == (
        entry.decision_hash,
        entry.chain_hash,
        audit.config_hash(CONFIG),
    )
    assert entry.record["sealed_at"] == FIXED_NOW.isoformat().replace("+00:00", "Z")
    assert entry.record["models"] == audit.models_of(CONFIG)
    assert audit.verify_chain(store.entries()).ok
    status = orchestrator.thread_status(graph, "c-go")
    assert (status["decision_hash"], status["chain_hash"]) == (
        entry.decision_hash,
        entry.chain_hash,
    )


def test_blocage_dur_no_go_scelle():
    store = MemoryAuditStore()
    invoke(
        compiled(store, FixedExtractor(clauses(responsabilite_acheteur=None))), "c-nogo"
    )
    [entry] = store.entries()
    decision = decision_of(entry)
    assert (decision["final_decision"], decision["human"]) == ("NO_GO", None)
    assert any(v["hard_block"] for v in decision["verdicts"])


def test_rejet_scelle_sans_decision():
    store = MemoryAuditStore()
    values = invoke(compiled(store), "c-rejet", text="Too short.")
    [entry] = store.entries()
    decision = decision_of(entry)
    assert decision["reject_reason"] == values["reject_reason"] is not None
    assert (decision["final_decision"], decision["verdicts"]) == (None, [])


def test_escalade_rien_scelle_pendant_la_suspension_puis_decision_humaine():
    store = MemoryAuditStore()
    graph = compiled(
        store,
        FixedExtractor(clauses(penalites_execution=ABSENT)),
        FakeCrag(empty={"financier"}),
    )
    assert invoke(graph, "c-esc")["proposed_decision"] == "ESCALADE"
    assert store.entries() == []  # aucun effet de bord avant interrupt()
    graph.invoke(Command(resume=HUMAN), thread("c-esc"))
    [entry] = store.entries()
    decision = decision_of(entry)
    assert (decision["proposed_decision"], decision["final_decision"]) == (
        "ESCALADE",
        "NO_GO",
    )
    assert decision["human"]["acteur"] == ACTEUR_RELECTEUR.model_dump(mode="json")


def test_extraction_en_echec_puis_humain_scelle():
    store = MemoryAuditStore()
    invented = [
        c.model_copy(update={"quote": "citation inventée"})
        if c.kind == "revision_prix"
        else c
        for c in clauses()
    ]
    graph = compiled(store, FixedExtractor(invented))
    assert invoke(graph, "c-extr")["failure_report"]["stage"] == "extraction"
    graph.invoke(Command(resume=HUMAN), thread("c-extr"))
    [entry] = store.entries()
    decision = decision_of(entry)
    assert decision["failure_report"]["stage"] == "extraction"
    assert (decision["verdicts"], decision["final_decision"]) == ([], "NO_GO")


def test_sans_checkpointer_le_contrat_tient_lieu_de_thread():
    store = MemoryAuditStore()
    compiled(store, checkpointer=False).invoke(
        {
            "contract_id": "c-sans",
            "raw_text": CONTRACT_TEXT,
            "analysis_date": ANALYSIS_DATE,
            **context(),
        }
    )
    [entry] = store.entries()
    assert entry.thread_id == "c-sans"


def test_thread_distinct_du_contrat_scelle_avec_son_thread():
    store = MemoryAuditStore()
    graph = compiled(store)
    graph.invoke(
        {
            "contract_id": "c-x",
            "raw_text": CONTRACT_TEXT,
            "analysis_date": ANALYSIS_DATE,
            **context(),
        },
        thread("fil-x"),
    )
    [entry] = store.entries()
    assert (entry.contract_id, entry.thread_id) == ("c-x", "fil-x")


# --- Critère 6 : rejeu -----------------------------------------------------------------------


def test_6_memes_clauses_meme_decision_hash_puis_rejeu_identique():
    store = MemoryAuditStore()
    found = clauses(responsabilite_fournisseur=50, duree_engagement=48)  # humain
    graph = compiled(store, FixedExtractor(found))
    for cid in ("c-a", "c-b"):
        invoke(graph, cid)
        graph.invoke(Command(resume=HUMAN), thread(cid))
    first, second = store.entries()
    assert first.decision_hash == second.decision_hash
    assert first.chain_hash != second.chain_hash
    assert second.prev_hash == first.chain_hash
    for entry in (first, second):
        report = audit.replay(entry.record, CONFIG.model_dump(mode="json"))
        assert report.recomputed and report.identical


def test_6_autres_clauses_autre_decision_hash():
    store = MemoryAuditStore()
    invoke(compiled(store), "c-1")
    invoke(compiled(store, FixedExtractor(clauses(penalites_execution=ABSENT))), "c-2")
    first, second = store.entries()
    assert first.decision_hash != second.decision_hash


# --- Critère 11 : expiration scellée -------------------------------------------------------


def test_11_expiration_no_go_systeme_scellee():
    store = MemoryAuditStore()
    found = clauses(responsabilite_fournisseur=50, duree_engagement=48)
    graph = compiled(store, FixedExtractor(found))
    invoke(graph, "c-exp")
    snapshot = graph.get_state(thread("c-exp"))
    since = datetime.fromisoformat(snapshot.created_at)
    [status] = orchestrator.expire_threads(
        graph,
        timedelta(hours=24),
        now=since + timedelta(hours=25),
        thread_ids={"c-exp"},
        hold=LocalContractLocks().hold,
        actor=ACTEUR_RELECTEUR,
    )
    [entry] = store.entries()
    human = decision_of(entry)["human"]
    assert (human["source"], human["acteur"], human["decision"]) == (
        "systeme",
        ACTEUR_RELECTEUR.model_dump(mode="json"),
        "NO_GO",
    )
    assert human["reason"].startswith("timeout")
    assert status["chain_hash"] == entry.chain_hash


# --- Critère 12 : levée de blocage scellée -------------------------------------------------


def test_12_levee_de_blocage_scellee_avec_son_motif():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["human_policy"]["hard_block_review"] = True
    config = DecisionConfig.model_validate(data)
    store = MemoryAuditStore()
    graph = compiled(
        store, FixedExtractor(clauses(responsabilite_acheteur=None)), config=config
    )
    assert invoke(graph, "c-levee", config=config)["proposed_decision"] == "NO_GO"
    override = {
        "decision": "GO",
        "acteur": ACTEUR_RELECTEUR.model_dump(mode="json"),
        "reason": "plafond négocié hors contrat",
        "overrides_block": True,
    }
    graph.invoke(Command(resume=override), thread("c-levee"))
    [entry] = store.entries()
    decision = decision_of(entry)
    assert decision["final_decision"] == "GO"
    assert decision["human"]["overrides_block"] is True
    assert decision["human"]["reason"] == "plafond négocié hors contrat"
    assert entry.config_hash == audit.config_hash(config) != audit.config_hash(CONFIG)


def test_journal_verifie_apres_plusieurs_parcours():
    store = MemoryAuditStore()
    invoke(compiled(store), "c-1")
    invoke(compiled(store), "c-rejet", text="Too short.")
    invoke(
        compiled(store, FixedExtractor(clauses(responsabilite_acheteur=None))), "c-nogo"
    )
    assert audit.verify_chain(store.entries()).ok and len(store.entries()) == 3


# --- Empreinte scellée : celle de la configuration qui a produit la décision --------------

LOW_MARGIN = {"responsabilite_fournisseur": 50, "duree_engagement": 48}


def changed_config():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["budget"]["max_tokens_per_contract"] += 1
    return DecisionConfig.model_validate(data)


def test_run_contract_pose_l_empreinte_d_analyse_scellee():
    store = MemoryAuditStore()
    graph = compiled(store)
    status = orchestrator.run_contract(
        graph,
        "c-run",
        CONTRACT_TEXT,
        analysis_date=ANALYSIS_DATE,
        config=CONFIG,
        actor=ACTEUR_ANALYSTE,
        code=CODE,
    )
    [entry] = store.entries()
    assert status["config_hash"] == entry.config_hash == audit.config_hash(CONFIG)
    assert entry.record["sealing_config_hash"] == audit.config_hash(CONFIG)
    assert entry.record["sealing_findings"] == []
    assert entry.record["models"] == audit.models_of(CONFIG)


def test_resume_refuse_si_la_configuration_a_change_avant_toute_reprise():
    store = MemoryAuditStore()
    graph = compiled(store, FixedExtractor(clauses(**LOW_MARGIN)))
    orchestrator.run_contract(
        graph,
        "c-conf",
        CONTRACT_TEXT,
        analysis_date=ANALYSIS_DATE,
        config=CONFIG,
        actor=ACTEUR_ANALYSTE,
        code=CODE,
    )
    with pytest.raises(orchestrator.ThreadError) as refused:
        orchestrator.resume_thread(graph, "c-conf", HUMAN, config=changed_config())
    message = str(refused.value)
    assert "configuration modifiée" in message
    assert "relancer l'analyse" in message.lower()
    assert "restaurer la configuration" in message
    assert orchestrator.thread_status(graph, "c-conf")["statut"] == "suspendu"
    assert store.entries() == []  # rien de repris, rien de scellé
    # avec la configuration de l'analyse, la reprise passe
    orchestrator.resume_thread(graph, "c-conf", HUMAN, config=CONFIG)
    assert len(store.entries()) == 1


def test_expire_continue_et_scelle_les_deux_empreintes_avec_le_constat():
    store, saver = MemoryAuditStore(), InMemorySaver(serde=strict_serializer())
    analysed_by = compiled(store, FixedExtractor(clauses(**LOW_MARGIN)), saver=saver)
    orchestrator.run_contract(
        analysed_by,
        "c-exp2",
        CONTRACT_TEXT,
        analysis_date=ANALYSIS_DATE,
        config=CONFIG,
        actor=ACTEUR_ANALYSTE,
        code=CODE,
    )
    # expire lancé par un processus dont la configuration a changé depuis l'analyse
    other = changed_config()
    expired_by = compiled(store, saver=saver, config=other)
    since = datetime.fromisoformat(expired_by.get_state(thread("c-exp2")).created_at)
    [status] = orchestrator.expire_threads(
        expired_by,
        timedelta(hours=24),
        now=since + timedelta(hours=25),
        thread_ids={"c-exp2"},
        hold=LocalContractLocks().hold,
        actor=ACTEUR_RELECTEUR,
    )
    assert status["final_decision"] == "NO_GO"
    [entry] = store.entries()
    assert entry.config_hash == audit.config_hash(CONFIG)  # celle de l'analyse
    assert entry.record["sealing_config_hash"] == audit.config_hash(other)
    assert entry.record["sealing_findings"] == [audit.CONFIG_CHANGED]
    # le rejeu utilise l'empreinte d'analyse
    assert audit.replay(entry.record, CONFIG.model_dump(mode="json")).identical
    with pytest.raises(audit.ReplayError):
        audit.replay(entry.record, other.model_dump(mode="json"))

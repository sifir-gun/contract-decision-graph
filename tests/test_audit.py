"""Piste d'audit (domaine) : forme canonique, empreintes, chaînage, vérification, rejeu.

Python pur, sans base ni LLM. Critères 6 (rejeu : même decision_hash) et 8 (chaîne
d'audit : une modification casse la vérification), côté domaine.
"""

import hashlib
import json
from datetime import UTC, date, datetime

import pytest
import yaml
from doubles import ABSENT, ANALYSIS_DATE, clauses, usage
from pydantic import BaseModel, ValidationError

from cdg.domain import audit
from cdg.domain.config import DEFAULT_CONFIG_PATH, DecisionConfig, load_config
from cdg.domain.decision import decide
from cdg.domain.justification import justify
from cdg.domain.models import (
    DOMAINS,
    ClauseRetrieval,
    HumanDecision,
    NodeFailure,
    RetrievalTrace,
)
from cdg.domain.rules import RULES

CONFIG = load_config()
SEALED_AT = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


# --- Forme canonique ---------------------------------------------------------------


def test_canonique_cles_triees_sans_espaces_et_accents_conserves():
    assert audit.canonical({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()
    assert audit.canonical({"a": 1, "b": 2}) == audit.canonical({"b": 2, "a": 1})


def test_canonique_flottants_par_l_arrondi_unique():
    assert audit.canonical(0.1 + 0.2) == audit.canonical(0.3)
    assert audit.canonical(-1e-9) == b"0.0"  # -0.0 normalisé


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_canonique_flottant_non_fini_refuse(value):
    with pytest.raises(ValueError, match="non fini"):
        audit.canonical({"x": value})


def test_canonique_dates_et_modeles():
    class M(BaseModel):
        d: date
        x: float

    assert (
        audit.canonical(M(d=date(2026, 9, 26), x=0.5)) == b'{"d":"2026-09-26","x":0.5}'
    )
    assert audit.canonical(SEALED_AT) == b'"2026-09-26T08:00:00+00:00"'


def test_canonique_type_inconnu_refuse_sans_conversion_silencieuse():
    with pytest.raises(TypeError, match="object"):
        audit.canonical({"x": object()})
    with pytest.raises(TypeError, match="clé"):
        audit.canonical({1: "x"})


# --- Enregistrement, empreintes -------------------------------------------------------


def traced(domain, found, refs=("réf",), findings=()):
    """Verdict d'un domaine : règles, puis références figées pour chaque constat."""
    assessment = RULES[domain](found, CONFIG)
    trace = RetrievalTrace(
        clauses=[
            ClauseRetrieval(
                kind=k, queries=["q"], passes=1, retained=list(refs), expired=[]
            )
            for k in assessment.kinds_to_justify()
        ],
        findings=list(findings),
    )
    return justify(assessment, trace)


def analysed(contract_id="c-1", refs=("réf",), used=None, failures=(), **overrides):
    """État d'un contrat passé par les analystes et le gate, sans revue humaine."""
    found = clauses(**overrides)
    verdicts = [traced(d, found, refs) for d in DOMAINS if d not in _failed(failures)]
    used = used or [
        usage(1000, 200, node="extract_clauses"),
        usage(300, 20, node="crag_grade:financier:penalites_execution"),
    ]
    outcome = decide(verdicts, list(failures), used, CONFIG)
    return {
        "contract_id": contract_id,
        "analysis_date": ANALYSIS_DATE,
        "clauses": found,
        "verdicts": verdicts,
        "usage": used,
        "failures": list(failures),
        "proposed_decision": outcome.proposed,
        "margin": outcome.margin,
        "failure_report": outcome.failure_report,
        "final_decision": outcome.final,
        "human": None,
        "reject_reason": None,
    }


def _failed(failures):
    return {f.domain for f in failures}


def record(state, thread_id=None, sealed_at=SEALED_AT, explanation=None, config=CONFIG):
    return audit.build_record(
        state,
        thread_id=thread_id or state["contract_id"],
        config_hash=audit.config_hash(config),
        models=audit.models_of(config),
        sealed_at=sealed_at,
        explanation=explanation,
    )


def dumped(state, **kwargs):
    return record(state, **kwargs).model_dump(mode="json")


def test_verdicts_ranges_dans_l_ordre_des_domaines():
    state = analysed()
    shuffled = {**state, "verdicts": list(reversed(state["verdicts"]))}
    assert [v.domain for v in record(shuffled).decision.verdicts] == list(DOMAINS)
    assert audit.decision_hash(dumped(shuffled)) == audit.decision_hash(dumped(state))


def test_decision_hash_ignore_identifiants_horodatage_consommation_et_explication():
    base = dumped(analysed())
    other = dumped(
        analysed(contract_id="c-2", used=[usage(5, 5, node="extract_clauses")]),
        sealed_at=datetime(2027, 1, 1, tzinfo=UTC),
        explanation={"source": "gabarit", "texte": "autre"},
    )
    assert other["contract_id"] != base["contract_id"] and other != base
    assert audit.decision_hash(other) == audit.decision_hash(base)


@pytest.mark.parametrize(
    "change",
    [
        {"duree_engagement": 48},  # une clause : autres constats, autre décision
        {"transfert_hors_ue": ABSENT},
    ],
)
def test_decision_hash_suit_les_clauses(change):
    assert audit.decision_hash(dumped(analysed(**change))) != audit.decision_hash(
        dumped(analysed())
    )


def test_decision_hash_suit_les_references_figees_et_la_configuration():
    base = audit.decision_hash(dumped(analysed(penalites_execution=ABSENT)))
    other_refs = dumped(analysed(refs=("autre réf",), penalites_execution=ABSENT))
    assert audit.decision_hash(other_refs) != base
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["min_margin"] = 0.06
    other_config = DecisionConfig.model_validate(data)
    state = analysed(penalites_execution=ABSENT)
    assert audit.decision_hash(dumped(state, config=other_config)) != base


def test_horodatage_sans_fuseau_refuse():
    with pytest.raises(ValidationError, match="fuseau"):
        record(analysed(), sealed_at=SEALED_AT.replace(tzinfo=None))


def test_modeles_et_config_hash_dans_l_enregistrement():
    r = record(analysed())
    assert r.models == {
        "llm_provider": CONFIG.llm.provider,
        "llm_main": CONFIG.llm.model("main"),
        "llm_light": CONFIG.llm.model("light"),
        "embedding": CONFIG.embedding.model,
    }
    assert r.decision.config_hash == audit.config_hash(CONFIG)


# --- config_hash : configuration validée, sous forme canonique -------------------------


def test_config_hash_stable_et_insensible_aux_commentaires(tmp_path):
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    commented = tmp_path / "decision.yaml"
    commented.write_text("# commentaire ajouté\n" + text, encoding="utf-8")
    assert audit.config_hash(load_config(commented)) == audit.config_hash(CONFIG)
    assert len(audit.config_hash(CONFIG)) == 64


def test_config_hash_suit_un_reglage():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["budget"]["max_tokens_per_contract"] += 1
    assert audit.config_hash(DecisionConfig.model_validate(data)) != audit.config_hash(
        CONFIG
    )


# --- Scellement et chaînage -----------------------------------------------------------


def test_premier_maillon_part_de_64_zeros():
    assert audit.GENESIS == "0" * 64
    entry = audit.seal(record(analysed()), None)
    assert entry.prev_hash == audit.GENESIS


def test_empreintes_selon_la_spec():
    r = record(analysed())
    data = r.model_dump(mode="json")
    entry = audit.seal(r, "a" * 64)
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert entry.chain_hash == hashlib.sha256(b"a" * 64 + canonical).hexdigest()
    assert entry.decision_hash == audit.decision_hash(data)
    assert (entry.record, entry.contract_id, entry.thread_id) == (data, "c-1", "c-1")
    assert entry.config_hash == audit.config_hash(CONFIG)


@pytest.mark.parametrize("prev", ["", "abc", "g" * 64])
def test_prev_hash_invalide_refuse(prev):
    with pytest.raises(ValueError, match="prev_hash"):
        audit.seal(record(analysed()), prev)


def chain(n=3):
    """Chaîne de n enregistrements scellés, comme le magasin les rendrait."""
    entries, prev = [], None
    for i in range(1, n + 1):
        entry = audit.seal(record(analysed(contract_id=f"c-{i}")), prev)
        entries.append(
            audit.StoredAuditEntry(**entry.model_dump(), id=i, created_at=SEALED_AT)
        )
        prev = entry.chain_hash
    return entries


def test_chaine_intacte_verifiee():
    report = audit.verify_chain(chain())
    assert (report.ok, report.count, report.broken_id, report.reason) == (
        True,
        3,
        None,
        None,
    )


def test_chaine_vide_verifiee():
    assert audit.verify_chain([]).ok


def tampered(entries, index, **changes):
    out = list(entries)
    out[index] = out[index].model_copy(update=changes)
    return out


def test_modifier_la_decision_casse_la_chaine():
    entries = chain()
    record_ = json.loads(json.dumps(entries[1].record))
    record_["decision"]["final_decision"] = "NO_GO"
    report = audit.verify_chain(tampered(entries, 1, record=record_))
    assert (report.ok, report.broken_id) == (False, 2)
    assert "decision_hash" in report.reason


def test_modifier_hors_decision_casse_la_chaine():
    entries = chain()
    record_ = json.loads(json.dumps(entries[1].record))
    record_["usage"][0]["tokens_in"] += 1
    report = audit.verify_chain(tampered(entries, 1, record=record_))
    assert (report.ok, report.broken_id) == (False, 2)
    assert "chain_hash" in report.reason


def test_supprimer_un_maillon_casse_la_chaine():
    entries = chain()
    report = audit.verify_chain([entries[0], entries[2]])
    assert (report.ok, report.broken_id) == (False, 3)
    assert "prev_hash" in report.reason


def test_premier_maillon_hors_genese_refuse():
    report = audit.verify_chain(chain()[1:])
    assert (report.ok, report.broken_id) == (False, 2)


@pytest.mark.parametrize(
    "column,value",
    [("config_hash", "0" * 64), ("contract_id", "autre"), ("thread_id", "autre")],
)
def test_colonne_incoherente_avec_l_enregistrement_refusee(column, value):
    report = audit.verify_chain(tampered(chain(), 0, **{column: value}))
    assert (report.ok, report.broken_id) == (False, 1)
    assert column in report.reason


def test_enregistrement_mal_forme_signale_pas_ignore():
    report = audit.verify_chain(tampered(chain(), 0, record={"sans": "decision"}))
    assert (report.ok, report.broken_id) == (False, 1)
    assert "mal formé" in report.reason


# --- Rejeu : règles, justification et décision recalculées sur les références figées ------


def replay(state, **kwargs):
    return audit.replay(dumped(state, **kwargs), CONFIG)


@pytest.mark.parametrize(
    "overrides",
    [
        {},  # GO, sans constat
        {"penalites_execution": ABSENT, "duree_engagement": 48},  # pénalités
        {"responsabilite_acheteur": None},  # blocage dur
        {"duree_engagement": 48, "preavis_resiliation": 12},  # conflit
    ],
)
def test_6_rejeu_meme_decision_hash(overrides):
    report = replay(analysed(**overrides))
    assert report.recomputed and report.identical
    assert report.replayed_hash == report.sealed_hash


def test_6_rejeu_insuffisant_sur_references_figees():
    state = analysed(refs=(), penalites_execution=ABSENT)  # constat sans référence
    assert state["proposed_decision"] == "ESCALADE"
    assert replay(state).identical


def test_rejeu_constats_du_crag_repris_du_resume():
    found = clauses(penalites_execution=ABSENT)
    verdicts = [
        traced(d, found, findings=["référence expirée : L441-10"]) for d in DOMAINS
    ]
    outcome = decide(verdicts, [], [], CONFIG)
    state = {
        **analysed(penalites_execution=ABSENT),
        "verdicts": verdicts,
        "usage": [],
        "proposed_decision": outcome.proposed,
        "margin": outcome.margin,
        "final_decision": outcome.final,
    }
    assert "référence expirée : L441-10" in state["verdicts"][1].findings
    assert replay(state).identical


def test_rejeu_detecte_un_verdict_falsifie():
    data = dumped(analysed())
    data["decision"]["verdicts"][0]["score"] = 0.1
    report = audit.replay(data, CONFIG)
    assert report.recomputed and not report.identical


def test_rejeu_avec_une_autre_configuration_refuse():
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["min_margin"] = 0.06
    with pytest.raises(audit.ReplayError, match="configuration"):
        audit.replay(dumped(analysed()), DecisionConfig.model_validate(data))


def test_rejeu_decision_humaine_reprise_telle_quelle():
    state = analysed(responsabilite_fournisseur=50, duree_engagement=48)  # marge faible
    assert state["final_decision"] is None
    human = HumanDecision(decision="GO_RESERVES", reviewer="r", reason="m")
    state = {**state, "human": human, "final_decision": "GO_RESERVES"}
    assert replay(state).identical


def test_rejeu_ignore_la_consommation_et_les_echecs_d_apres_le_gate():
    state = analysed()
    after = {
        **state,
        "usage": [*state["usage"], usage(99_000, 1_000, node="explain")],
        "failures": [NodeFailure(node="explain", error="E", message="m", attempts=1)],
    }
    assert replay(after).identical  # le budget n'a pas été dépassé au gate


def test_rejeu_budget_depasse_au_gate():
    state = analysed(used=[usage(59_000, 2_000, node="extract_clauses")])
    assert state["failure_report"]["stage"] == "budget"
    assert replay(state).identical


def test_rejeu_analyste_en_echec():
    failure = NodeFailure(
        node="analyst", error="E", message="m", attempts=3, domain="financier"
    )
    state = analysed(failures=[failure])
    assert state["proposed_decision"] == "ESCALADE"
    assert replay(state).identical


def test_rejeu_rien_a_recalculer_pour_un_rejet():
    state = {
        "contract_id": "c-r",
        "analysis_date": ANALYSIS_DATE,
        "reject_reason": "texte trop court",
        "verdicts": [],
        "usage": [],
    }
    report = replay(state)
    assert not report.recomputed and report.identical
    assert record(state).decision.final_decision is None


def test_rejeu_rien_a_recalculer_si_le_gate_a_echoue():
    state = analysed()
    failure = NodeFailure(node="decision_gate", error="E", message="m", attempts=1)
    state = {**state, "failures": [failure], "proposed_decision": "ESCALADE"}
    report = replay(state)
    assert not report.recomputed and report.identical


def test_verdicts_hors_ordre_ou_repetes_refuses():
    decision = dumped(analysed())["decision"]
    decision["verdicts"] = list(reversed(decision["verdicts"]))
    with pytest.raises(ValidationError, match="ordre des domaines"):
        audit.DecisionRecord.model_validate(decision)


def test_rejeu_sans_resume_du_crag_refuse():
    data = dumped(analysed())
    data["decision"]["verdicts"][0]["retrieval"] = None
    with pytest.raises(audit.ReplayError, match="sans résumé du CRAG"):
        audit.replay(data, CONFIG)


# --- Partie décision : le fait, pas la mesure ; échecs rangés par domaine ------------------


def failure(domain):
    return NodeFailure(
        node="analyst", error="ValueError", message="m", attempts=1, domain=domain
    )


def test_budget_depasse_meme_decision_hash_quelle_que_soit_la_consommation():
    small = analysed(used=[usage(59_000, 2_000, node="extract_clauses")])
    large = analysed(used=[usage(70_000, 5_000, node="extract_clauses")])
    assert small["failure_report"]["tokens"] != large["failure_report"]["tokens"]
    assert audit.decision_hash(dumped(small)) == audit.decision_hash(dumped(large))
    r = record(large)
    # le fait dans la partie décision, la mesure dans l'enregistrement scellé
    assert r.decision.failure_report == {"stage": "budget", "limit": 60_000}
    assert r.failure_report == {"stage": "budget", "tokens": 75_000, "limit": 60_000}


def test_budget_depasse_avec_analyste_en_echec_sans_la_mesure():
    state = analysed(
        used=[usage(59_000, 2_000, node="extract_clauses")],
        failures=[failure("financier")],
    )
    assert state["failure_report"]["budget"] == {"tokens": 61_000, "limit": 60_000}
    assert record(state).decision.failure_report["budget"] == {"limit": 60_000}
    assert replay(state).identical


def test_echecs_d_analystes_ranges_par_domaine():
    one = analysed(failures=[failure("conformite"), failure("financier")])
    other = analysed(failures=[failure("financier"), failure("conformite")])
    assert audit.decision_hash(dumped(one)) == audit.decision_hash(dumped(other))
    report = record(one).decision.failure_report
    assert [f["domain"] for f in report["failures"]] == ["financier", "conformite"]
    # l'ordre de l'état reste celui de l'enregistrement scellé, hors decision_hash
    assert [f.domain for f in record(one).failures] == ["conformite", "financier"]
    assert replay(one).identical and replay(other).identical

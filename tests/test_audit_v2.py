"""Journal d'audit v2 (PR D2, ADR 005).

- L'enregistrement porte sa version (2) ; l'acteur de l'analyse (`analyse_par`) et celui
  de la décision humaine (`human.acteur`) remplacent le relecteur nommé : canal, iss et
  sub, ou opérateur non nominatif ; jamais de nom ni d'e-mail.
- La version du code de l'analyse et celle du processus qui scelle (commit, et empreinte
  de l'image dans le cluster) sont scellées hors de la partie décision : decision_hash ne
  dépend que des entrées de la décision et de la configuration (critère 6), la chaîne
  couvre tout. Inconnue, elle le dit ; différentes, un constat.
- Les enregistrements v1 (sans version, relecteur nommé) restent vérifiables et
  rejouables : la vérification recalcule tout sur le JSON stocké, et le rejeu les relit
  par leurs propres modèles, figés. Jeu v1 synthétique : aucune donnée du vrai journal.
"""

import json

import pytest
from doubles import CODE
from test_audit import CONFIG, analysed, other_config, record

from cdg.domain import audit
from cdg.domain.authorization import Actor
from cdg.domain.models import HumanDecision, HumanReview
from cdg.domain.version import UNKNOWN, CodeVersion

ISS = "https://idp.example.org"
ANALYSTE = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-analyste")
RELECTEUR = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-relecteur")


def reviewed(**changes):
    state = analysed()
    state["analyse_par"] = ANALYSTE
    state["human"] = HumanReview(
        decision=state["proposed_decision"], acteur=RELECTEUR, reason="revu"
    )
    state["final_decision"] = state["proposed_decision"]
    return state | changes


def entry(data: dict, prev: str) -> audit.StoredAuditEntry:
    """Maillon stocké, calculé sur le JSON tel quel (comme le magasin le relit)."""
    return audit.StoredAuditEntry(
        id=0,
        created_at=data["sealed_at"],
        contract_id=data["contract_id"],
        thread_id=data["thread_id"],
        record=data,
        config_hash=data["decision"]["config_hash"],
        decision_hash=audit.decision_hash(data),
        prev_hash=prev,
        chain_hash=audit.chain_hash(data, prev),
    )


def v1_record() -> dict:
    """Enregistrement au format d'avant la PR D2 : sans version, sans acteur de
    l'analyse, relecteur nommé ; synthétique."""
    data = record(reviewed()).model_dump(mode="json")
    for v2_only in ("version", "analyse_par", "code_version", "sealing_code_version"):
        data.pop(v2_only)
    human = data["decision"]["human"]
    data["decision"]["human"] = HumanDecision(
        decision=human["decision"], reviewer="Relecteur de test", reason="revu"
    ).model_dump(mode="json")
    return audit.AuditRecordV1.model_validate(data).model_dump(mode="json")


# --- format v2 -----------------------------------------------------------------------------


def test_enregistrement_v2_versionne_avec_les_acteurs_sans_nom():
    data = record(reviewed()).model_dump(mode="json")
    assert data["version"] == 2
    assert data["analyse_par"] == ANALYSTE.model_dump(mode="json")
    assert data["decision"]["human"]["acteur"] == RELECTEUR.model_dump(mode="json")
    text = json.dumps(data)
    assert "reviewer" not in text and "nom" not in data["decision"]["human"]


def test_acteur_de_la_decision_dans_la_partie_decision():
    """La décision humaine, acteur compris, entre dans decision_hash."""
    first = record(reviewed()).model_dump(mode="json")
    other = Actor(canal="interface", authentifie=True, iss=ISS, sub="sub-autre")
    state = reviewed()
    state["human"] = state["human"].model_copy(update={"acteur": other})
    second = record(state).model_dump(mode="json")
    assert audit.decision_hash(first) != audit.decision_hash(second)


def test_ancienne_decision_humaine_dans_l_etat_refusee_au_scellement():
    state = reviewed(
        human=HumanDecision(decision="GO", reviewer="Relecteur", reason="revu")
    )
    with pytest.raises(TypeError, match="v1"):
        record(state)


def test_analyse_sans_acteur_scellee_sans_acteur():
    """Analyse antérieure aux rôles, tranchée en accès d'urgence : scellée telle quelle."""
    state = reviewed()
    del state["analyse_par"]
    assert record(state).model_dump(mode="json")["analyse_par"] is None


# --- version du code ----------------------------------------------------------------------

AUTRE_CODE = CodeVersion(commit="0" * 40, image="sha256:" + "1" * 64)


def test_versions_du_code_de_l_analyse_et_du_scellement_scellees():
    data = record(reviewed()).model_dump(mode="json")
    expected = CODE.model_dump(mode="json")
    assert data["code_version"] == data["sealing_code_version"] == expected
    assert data["sealing_findings"] == []


def test_contexte_d_analyse_porte_la_version_du_code():
    context = audit.analysis_context(CONFIG, AUTRE_CODE)
    assert context["code_version"] == AUTRE_CODE.model_dump(mode="json")


def test_code_modifie_entre_l_analyse_et_le_scellement_constat():
    data = record(reviewed(), code=AUTRE_CODE).model_dump(mode="json")
    assert data["code_version"] == CODE.model_dump(mode="json")  # celle de l'analyse
    assert data["sealing_code_version"] == AUTRE_CODE.model_dump(mode="json")
    assert data["sealing_findings"] == [audit.CODE_CHANGED]
    assert audit.CODE_CHANGED == "code modifié entre l'analyse et le scellement"


def test_configuration_et_code_modifies_deux_constats():
    data = record(reviewed(), config=other_config(), code=AUTRE_CODE).model_dump(
        mode="json"
    )
    assert data["sealing_findings"] == [audit.CONFIG_CHANGED, audit.CODE_CHANGED]


def test_version_inconnue_scellee_telle_quelle():
    unknown = CodeVersion(commit=UNKNOWN, image=None)
    state = reviewed(**audit.analysis_context(CONFIG, unknown))
    data = record(state, code=unknown).model_dump(mode="json")
    assert data["code_version"] == data["sealing_code_version"]
    assert data["code_version"] == {"commit": "inconnu", "image": None}


def test_version_du_code_hors_de_la_partie_decision_mais_chainee():
    first = record(reviewed()).model_dump(mode="json")
    state = reviewed(**audit.analysis_context(CONFIG, AUTRE_CODE))
    other = record(state, code=AUTRE_CODE).model_dump(mode="json")
    assert "code_version" not in first["decision"]
    assert audit.decision_hash(first) == audit.decision_hash(other)
    assert audit.chain_hash(first, audit.GENESIS) != audit.chain_hash(
        other, audit.GENESIS
    )


@pytest.mark.parametrize("field", ["code_version", "sealing_code_version"])
def test_version_du_code_alteree_apres_scellement_chaine_rompue(field):
    """Hors de decision_hash, mais couverte par chain_hash : un commit réécrit dans un
    enregistrement scellé est vu par la vérification de la chaîne."""
    first = entry(record(reviewed()).model_dump(mode="json"), audit.GENESIS)
    second = entry(record(reviewed()).model_dump(mode="json"), first.chain_hash)
    tampered = json.loads(json.dumps(first.record))
    tampered[field]["commit"] = "0" * 40
    altered = first.model_copy(update={"record": tampered, "id": 1})
    report = audit.verify_chain([altered, second])
    assert (report.ok, report.broken_id) == (False, 1)
    assert "chain_hash" in report.reason  # decision_hash, lui, reste juste


def test_analyse_sans_version_du_code_scellee_nulle():
    """Analyse lancée avant le scellement de la version du code : null, comme l'acteur
    d'une analyse antérieure aux rôles ; celle du scellement est toujours connue."""
    state = reviewed()
    del state["code_version"]
    data = record(state).model_dump(mode="json")
    assert data["code_version"] is None
    assert data["sealing_code_version"] == CODE.model_dump(mode="json")


def test_version_du_code_du_scellement_exigee():
    with pytest.raises(TypeError, match="sealing_code_version"):
        audit.build_record(
            reviewed(),
            thread_id="c-1",
            sealing_config_hash=audit.config_hash(CONFIG),
            sealed_at=record(reviewed()).sealed_at,
        )


# --- v1 vérifiable et rejouable --------------------------------------------------------------


def test_chaine_melee_v1_puis_v2_verifiable():
    first = entry(v1_record(), audit.GENESIS)
    second = entry(record(reviewed()).model_dump(mode="json"), first.chain_hash)
    report = audit.verify_chain([first, second])
    assert report.ok and report.count == 2


def test_v1_relu_par_ses_modeles_a_l_identique():
    data = v1_record()
    assert audit.record_version(data) == 1
    decision = audit.DecisionRecordV1.model_validate(data["decision"])
    assert audit.canonical(decision.model_dump(mode="json")) == audit.canonical(
        data["decision"]
    )


def test_v1_et_v2_rejoues_a_l_identique():
    for data in (v1_record(), record(reviewed()).model_dump(mode="json")):
        report = audit.replay(data, CONFIG.model_dump(mode="json"))
        assert report.identical and report.recomputed


def test_version_inconnue_refusee():
    data = record(reviewed()).model_dump(mode="json") | {"version": 3}
    with pytest.raises(audit.ReplayError, match="version"):
        audit.replay(data, CONFIG.model_dump(mode="json"))
    with pytest.raises(audit.ReplayError, match="version"):
        audit.record_version(data)

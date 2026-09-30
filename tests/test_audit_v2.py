"""Journal d'audit v2 (PR D2, ADR 005).

- L'enregistrement porte sa version (2) ; l'acteur de l'analyse (`analyse_par`) et celui
  de la décision humaine (`human.acteur`) remplacent le relecteur nommé : canal, iss et
  sub, ou opérateur non nominatif ; jamais de nom ni d'e-mail.
- Les enregistrements v1 (sans version, relecteur nommé) restent vérifiables et
  rejouables : la vérification recalcule tout sur le JSON stocké, et le rejeu les relit
  par leurs propres modèles, figés. Jeu v1 synthétique : aucune donnée du vrai journal.
"""

import json

import pytest
from test_audit import CONFIG, analysed, record

from cdg.domain import audit
from cdg.domain.authorization import Actor
from cdg.domain.models import HumanDecision, HumanReview

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
    data.pop("version")
    data.pop("analyse_par")
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
        report = audit.replay(data, CONFIG)
        assert report.identical and report.recomputed


def test_version_inconnue_refusee():
    data = record(reviewed()).model_dump(mode="json") | {"version": 3}
    with pytest.raises(audit.ReplayError, match="version"):
        audit.replay(data, CONFIG)
    with pytest.raises(audit.ReplayError, match="version"):
        audit.record_version(data)

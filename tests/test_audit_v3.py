"""Journal d'audit v3 (profils de contrat, ADR 009).

- Le profil de contrat entre dans la partie décision de l'enregistrement, donc dans
  decision_hash : une même décision sous deux profils n'a pas la même empreinte.
- Un état sans profil (analyse d'avant les profils, reprise ou relancée) est scellé
  `fournisseur`, le seul profil qui existait alors (`profiles.of_state`).
- Les enregistrements v1 et v2 restent vérifiables et se rejouent à l'identique : relus
  par leurs modèles, figés, comme profil `fournisseur`. Un v3, comme un v2, exige la
  configuration archivée de sa décision ; un v1 en est exempté. Jeux v1 et v2
  synthétiques : aucune donnée du vrai journal.
"""

import json

import pytest
from test_archive_configurations import ARCHIVED, chain_of
from test_audit import CONFIG, analysed, record
from test_audit_v2 import reviewed, v1_record, v2_record

from cdg.domain import audit
from cdg.domain.audit import AuditRecordV2, DecisionRecordV2
from cdg.domain.resumption import INTERRUPTED
from cdg.domain.rules import RULES


def v3_record() -> dict:
    """Enregistrement au format courant, scellé sous le profil `fournisseur`."""
    return record(reviewed()).model_dump(mode="json")


# --- format v3 : le profil dans la partie décision ------------------------------------------


def test_enregistrement_v3_porte_le_profil():
    data = record(reviewed()).model_dump(mode="json")
    assert data["version"] == 3
    assert data["decision"]["profile"] == "fournisseur"


def test_profil_compte_dans_l_empreinte_de_la_decision():
    data = record(reviewed()).model_dump(mode="json")
    changed = {**data, "decision": {**data["decision"], "profile": "autre"}}
    assert audit.decision_hash(changed) != audit.decision_hash(data)


def test_etat_sans_profil_scelle_en_fournisseur():
    state = {k: v for k, v in analysed().items() if k != "profile"}
    assert record(state).decision.profile == "fournisseur"


def test_etat_au_profil_inconnu_refuse_au_scellement():
    with pytest.raises(ValueError, match="profil inconnu"):
        record(analysed() | {"profile": "autre"})


def test_formats_par_version():
    assert audit.FORMATS == {
        1: audit.DecisionRecordV1,
        2: DecisionRecordV2,
        3: audit.DecisionRecord,
    }
    assert audit.AuditRecord.model_fields["decision"].annotation is audit.DecisionRecord
    assert AuditRecordV2.model_fields["decision"].annotation is DecisionRecordV2


# --- profil de chaque version -----------------------------------------------------------------


def test_profil_de_chaque_version():
    assert audit.record_profile(v1_record()) == "fournisseur"
    assert audit.record_profile(v2_record()) == "fournisseur"
    assert audit.record_profile(v3_record()) == "fournisseur"


@pytest.mark.parametrize(
    "profile", [None, "", "autre"], ids=["absent", "vide", "autre"]
)
def test_profil_d_un_v3_absent_ou_inconnu_refuse(profile):
    data = v3_record()
    if profile is None:
        del data["decision"]["profile"]
    else:
        data["decision"]["profile"] = profile
    with pytest.raises(ValueError, match="profil inconnu"):
        audit.record_profile(data)


def test_profil_d_une_version_inconnue_refuse():
    with pytest.raises(audit.ReplayError, match="version"):
        audit.record_profile(v3_record() | {"version": 4})


# --- v2 figé ----------------------------------------------------------------------------------

# forme du v2 scellé avant les profils, champ par champ : elle ne change plus
V2_RECORD_FIELDS = {
    "contract_id",
    "thread_id",
    "failure_report",
    "sealing_config_hash",
    "sealing_findings",
    "explanation",
    "failures",
    "usage",
    "models",
    "sealed_at",
    "version",
    "analyse_par",
    "code_version",
    "sealing_code_version",
    "resume_escalation",
    "decision",
}
V2_DECISION_FIELDS = {
    "analysis_date",
    "clauses",
    "verdicts",
    "proposed_decision",
    "margin",
    "final_decision",
    "failure_report",
    "reject_reason",
    "config_hash",
    "input_findings",
    "human",
}


def test_v2_relu_par_ses_modeles_a_l_identique():
    """Les modèles v2 sont des copies figées du v2 d'avant les profils : sans profil,
    ils relisent un enregistrement v2 et le reproduisent à l'identique, sans champ gagné
    ni perdu. L'enregistrement attendu est dérivé du v3 sans passer par eux."""
    v3 = v3_record()
    decision = {k: v for k, v in v3["decision"].items() if k != "profile"}
    expected = {**v3, "version": 2, "decision": decision}
    data = v2_record()
    assert data == expected
    assert audit.record_version(data) == 2
    assert AuditRecordV2.model_validate(data).model_dump(mode="json") == data
    relu = DecisionRecordV2.model_validate(data["decision"]).model_dump(mode="json")
    assert audit.canonical(relu) == audit.canonical(data["decision"])
    assert set(AuditRecordV2.model_fields) == V2_RECORD_FIELDS
    assert set(DecisionRecordV2.model_fields) == V2_DECISION_FIELDS
    assert AuditRecordV2.model_fields["version"].default == 2


def test_v2_avec_un_profil_refuse_par_ses_modeles():
    """Un v2 n'a jamais porté de profil : ses modèles figés le refusent."""
    data = v2_record()
    data["decision"]["profile"] = "fournisseur"
    with pytest.raises(ValueError, match="profile"):
        AuditRecordV2.model_validate(data)


def test_v3_sans_profil_refuse_par_ses_modeles():
    data = v3_record()
    del data["decision"]["profile"]
    with pytest.raises(ValueError, match="profile"):
        audit.AuditRecord.model_validate(data)


# --- journal mêlé : vérification et rejeu -----------------------------------------------------


def test_chaine_melee_v1_v2_v3_verifiable():
    report = audit.verify_journal(
        chain_of(v1_record(), v2_record(), v3_record()), ARCHIVED
    )
    assert report.ok and not report.archive_fault
    assert (report.count, report.archived, report.v1_exempted) == (3, 1, 1)


def test_v1_v2_v3_rejoues_a_l_identique():
    for data in (v1_record(), v2_record(), v3_record()):
        report = audit.replay(data, CONFIG.model_dump(mode="json"))
        assert report.identical and report.recomputed


def test_v3_sans_configuration_archivee_journal_non_conforme():
    """Un v3, comme un v2, exige la configuration archivée de sa décision."""
    for later in (v3_record(), v2_record()):
        entries = chain_of(v1_record(), later)
        report = audit.verify_journal(entries, {})
        assert (report.ok, report.archive_fault, report.broken_id) == (False, True, 2)
        assert "non archivée" in report.reason
        assert entries[1].config_hash in report.reason


def interrupted(data: dict) -> dict:
    """Le même enregistrement, escaladé par la reprise : le rejeu ne recalcule pas le
    gate, mais rejustifie les verdicts gardés."""
    data = json.loads(json.dumps(data))
    data["decision"]["failure_report"] = {"failures": [{"error": INTERRUPTED}]}
    return data


@pytest.mark.parametrize("branch", ["gate", "reprise"])
def test_rejeu_rejustifie_sous_le_profil_de_l_enregistrement(monkeypatch, branch):
    """Les règles du rejeu sont celles du profil que rend `record_profile` : sous un
    profil sans règles `fournisseur`, le rejeu les trouve quand même."""
    data = v3_record() if branch == "gate" else interrupted(v3_record())
    seen = []

    def profile_of(record):
        seen.append(record)
        return "autre"

    monkeypatch.setattr(audit, "record_profile", profile_of)
    monkeypatch.setattr(audit, "RULES", {"autre": RULES["fournisseur"]})
    report = audit.replay(data, CONFIG.model_dump(mode="json"))
    assert seen == [data] and report.recomputed
    if branch == "gate":
        assert report.identical

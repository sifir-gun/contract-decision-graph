"""Enregistrement v2 de référence, figé dans le dépôt (ADR 010).

Le vrai journal du poste ne contient aucun v2, et les aides des autres tests dérivent leur
v2 du code courant. Celui-ci a été produit par le code du commit 5e5a8f6, d'avant les
profils (`tests/fixtures/journal-v2-reference.json`, clé `_provenance`) : ses empreintes,
recalculées par le code courant, sont celles du scellement d'alors, il se relit à
l'identique par ses modèles figés, se vérifie avec sa configuration archivée, se rejoue à
l'identique et fidèlement, comme profil `fournisseur`. Données synthétiques ; à ne jamais
régénérer.
"""

import json
from pathlib import Path

import pytest

from cdg.domain import audit
from cdg.domain.audit import AuditRecordV2, DecisionRecordV2
from cdg.domain.version import CodeVersion

REFERENCE = Path(__file__).parent / "fixtures" / "journal-v2-reference.json"
# empreintes du scellement par 5e5a8f6, recopiées ici : un fichier régénéré se verrait
DECISION_HASH = "e1ed18e449e3af77af9da86d651f824b6da5a1b68f44b29e403b9a450a8cc136"
CHAIN_HASH = "ae34c94fbb15cf2c3790bd40ee67afcd625c1f4ec951db1588ff4f5787f8e9e1"


@pytest.fixture(scope="module")
def reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entry(reference) -> dict:
    return reference["entry"]


@pytest.fixture(scope="module")
def data(entry) -> dict:
    return entry["record"]


def stored(entry: dict) -> audit.StoredAuditEntry:
    return audit.StoredAuditEntry(
        id=1, created_at=entry["record"]["sealed_at"], **entry
    )


def test_reference_produite_avant_les_profils(reference, entry, data):
    assert "5e5a8f6" in reference["_provenance"]
    assert data["version"] == 2
    assert "profile" not in data["decision"]
    assert entry["decision_hash"] == DECISION_HASH
    assert entry["chain_hash"] == CHAIN_HASH
    assert entry["prev_hash"] == audit.GENESIS


def test_empreintes_recalculees_par_le_code_courant(entry, data):
    assert audit.decision_hash(data) == DECISION_HASH
    assert audit.chain_hash(data, entry["prev_hash"]) == CHAIN_HASH
    assert data["decision"]["config_hash"] == entry["config_hash"]


def test_relu_par_ses_modeles_figes_a_l_identique(data):
    assert AuditRecordV2.model_validate(data).model_dump(mode="json") == data
    decision = DecisionRecordV2.model_validate(data["decision"])
    assert decision.model_dump(mode="json") == data["decision"]


def test_journal_conforme_avec_sa_configuration_archivee(reference, entry):
    report = audit.verify_journal([stored(entry)], reference["configurations"])
    assert report.ok, report.reason
    assert (report.count, report.archived, report.v1_exempted) == (1, 1, 0)
    assert report.head == CHAIN_HASH


def test_sans_sa_configuration_archivee_journal_non_conforme(entry):
    report = audit.verify_journal([stored(entry)], {})
    assert not report.ok and report.archive_fault and report.broken_id == 1


def test_rejoue_a_l_identique_sur_sa_configuration_archivee(reference, entry, data):
    configuration = reference["configurations"][entry["config_hash"]]
    replayed = audit.replay(data, configuration)
    assert replayed.identical and replayed.recomputed
    assert replayed.sealed_hash == replayed.replayed_hash == DECISION_HASH


def test_rejeu_fidele_sous_le_meme_code(data):
    same = CodeVersion.model_validate(data["code_version"])
    assert audit.replay_sense(data, same)[0] == audit.FAITHFUL


def test_relu_comme_profil_fournisseur(data):
    assert audit.record_version(data) == 2
    assert audit.record_profile(data) == "fournisseur"

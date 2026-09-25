"""Mode stub-j2 : dépendances provisoires de la CLI, sans analyse réelle."""

import json

import pytest
from doubles import ANALYSIS_DATE, clauses
from pydantic import ValidationError

from cdg import stub_j2


def write_clauses(tmp_path, items) -> str:
    path = tmp_path / "clauses.json"
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return path


def test_mode_affiche():
    assert stub_j2.MODE == "stub-j2"


def test_extracteur_lit_des_clauses_synthetiques_sans_llm(tmp_path):
    path = write_clauses(tmp_path, [c.model_dump() for c in clauses()])
    result = stub_j2.JsonClausesExtractor(path)("texte ignoré", [])
    assert result.clauses == clauses() and result.usage == []


def test_extracteur_valide_les_clauses(tmp_path):
    path = write_clauses(tmp_path, [{"kind": "revision_prix", "present": True, "quote": ""}])
    with pytest.raises(ValidationError):
        stub_j2.JsonClausesExtractor(path)("texte", [])


def test_extracteur_exige_une_liste(tmp_path):
    path = write_clauses(tmp_path, {"clauses": []})
    with pytest.raises(ValueError, match="liste"):
        stub_j2.JsonClausesExtractor(path)("texte", [])


def test_crag_sans_corpus_toujours_insuffisant_sans_reference():
    result = stub_j2.no_corpus_crag("juridique", clauses(), ANALYSIS_DATE)
    assert (result.status, result.evidence_ids, result.usage) == ("INSUFFISANT", [], [])


def test_sans_fichier_de_clauses_l_extraction_echoue_explicitement():
    deps = stub_j2.deps(None)
    with pytest.raises(RuntimeError, match="--clauses"):
        deps.extractor("texte", [])

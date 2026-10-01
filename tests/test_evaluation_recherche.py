"""Évaluation de la recherche seule (chantier « qualité de la recherche », PR 1).

Jeu d'évaluation (`data/evaluation/recherche.yaml`) : pour chaque requête du CRAG que
donnent les constats du jeu de démonstration, les références qui justifient vraiment le
constat. Elles sont choisies à la lecture du texte de chaque article ou fiche, jamais
d'après le rattachement déclaré (manifeste, en-tête des fiches, SOURCES.md), que la
recherche filtrée utilise déjà : chaque choix est justifié par une citation mot pour mot,
vérifiée ici dans le texte de la référence.
"""

import pytest

from cdg.application import evaluation, ingestion
from cdg.application.crag import clause_query
from cdg.domain.config import load_config

CONFIG = load_config()
QUERIES = evaluation.load_queries()
TEXTS = ingestion.reference_texts()
FICHES = {ingestion.fiche_reference(f) for f in ingestion.load_fiches()}


def _key(item) -> tuple[str, str, str]:
    return item.domain, item.kind, item.query


def test_le_jeu_couvre_exactement_les_requetes_des_constats_du_jeu_de_demonstration():
    """Une entrée par requête distincte, avec les contrats dont un constat la donne :
    un constat ajouté au jeu de démonstration sans référence attendue fait échouer."""
    demo = {_key(q): q.contracts for q in evaluation.demo_queries(CONFIG)}
    jeu = {_key(q): q.contracts for q in QUERIES}
    assert jeu == demo
    assert len(QUERIES) == len(demo) == 21


def test_chaque_requete_est_celle_du_crag_pour_la_clause():
    for q in QUERIES:
        clause = evaluation.demo_clause(q.contracts[0], q.kind)
        assert q.query == clause_query(q.domain, clause)


def test_references_attendues_du_corpus_typees_et_justifiees_mot_pour_mot():
    for q in QUERIES:
        assert q.expected, q.query
        names = [r.reference for r in q.expected]
        assert len(names) == len(set(names)), q.query
        for ref in q.expected:
            assert ref.reference in TEXTS, ref.reference
            assert ref.type == ("fiche" if ref.reference in FICHES else "article")
            assert ref.quote in TEXTS[ref.reference]["texte"], (
                ref.reference,
                ref.quote,
            )


def test_references_ecartees_du_corpus_et_jamais_attendues():
    for q in QUERIES:
        expected = {r.reference for r in q.expected}
        for ref in q.rejected:
            assert ref.reference in TEXTS, ref.reference
            assert ref.reference not in expected, (q.query, ref.reference)


def test_jeu_fige_le_2026_10_01():
    """Jeu validé puis figé avant toute mesure : le modifier change la mesure. Toute
    modification est motivée au journal, chiffres avant et après, et ce test suivi."""
    expected = [r for q in QUERIES for r in q.expected]
    assert len(expected) == 55
    assert sum(r.type == "article" for r in expected) == 34
    assert all(sum(r.type == "fiche" for r in q.expected) == 1 for q in QUERIES)


@pytest.mark.parametrize("field", ["justification", "quote"])
def test_justification_et_citation_jamais_vides(field):
    assert all(getattr(r, field).strip() for q in QUERIES for r in q.expected)

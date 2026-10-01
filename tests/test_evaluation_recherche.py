"""Évaluation de la recherche seule (chantier « qualité de la recherche », PR 1).

Jeu d'évaluation (`data/evaluation/recherche.yaml`) : pour chaque requête du CRAG que
donnent les constats du jeu de démonstration, les références qui justifient vraiment le
constat. Elles sont choisies à la lecture du texte de chaque article ou fiche, jamais
d'après le rattachement déclaré (manifeste, en-tête des fiches, SOURCES.md), que la
recherche filtrée utilise déjà : chaque choix est justifié par une citation mot pour mot,
vérifiée ici dans le texte de la référence.

Mesure (`mesure-recherche`) : rappel et précision au rang k, au niveau de la référence,
avec et sans le filtre de rattachement, pour toutes les références et pour les articles
seuls, requête par requête et en moyenne ; sans LLM.
"""

import re

import psycopg
import pytest
from cli_helpers import run_cli
from doubles import FakeRetriever, HashEmbedder

from cdg import cli
from cdg.adapters.postgres import rag_store
from cdg.application import evaluation, ingestion
from cdg.application.crag import clause_query
from cdg.domain.config import load_config
from cdg.domain.corpus import by_domain
from cdg.domain.evaluation import Scores, first_ranks, mean, scores, top_references
from cdg.domain.numeric import rounded
from cdg.ports.retriever import Passage

CONFIG = load_config()
MAX_WORDS = CONFIG.corpus.chunk_max_words
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


# --- métriques (domaine) ---------------------------------------------------------------


def test_references_distinctes_des_k_premiers_extraits_dans_l_ordre():
    refs = ["A", "A", "B", "C"]
    assert top_references(refs, 1) == ["A"]
    assert top_references(refs, 2) == ["A"]
    assert top_references(refs, 3) == ["A", "B"]
    assert top_references(refs, 10) == ["A", "B", "C"]
    with pytest.raises(ValueError, match="rang"):
        top_references(refs, 0)


def test_rappel_et_precision_au_niveau_de_la_reference():
    assert scores({"A", "B"}, ["A", "C"]) == Scores(recall=0.5, precision=0.5)
    assert scores({"A", "B", "C"}, ["B"]) == Scores(recall=0.333333, precision=1.0)
    # aucun extrait rendu : rien de pertinent rendu, précision nulle
    assert scores({"A"}, []) == Scores(recall=0.0, precision=0.0)
    with pytest.raises(ValueError, match="aucune référence attendue"):
        scores(set(), ["A"])


def test_moyenne_arrondie_et_jamais_vide():
    assert mean([1.0, 0.0, 0.5]) == 0.5
    assert mean([1.0, 1.0, 0.0]) == rounded(2 / 3)
    with pytest.raises(ValueError):
        mean([])


def test_rang_du_premier_extrait_de_chaque_reference_attendue():
    ranks = first_ranks(["C", "A", "D"], ["A", "B", "A", "C"])
    assert ranks == {"C": 4, "A": 1, "D": None}


# --- mesure (application), sur une doublure du corpus ---------------------------------


def corpus_double(drop: int | None = None, extra: bool = False) -> FakeRetriever:
    """Corpus réel (fichiers) dans la doublure, à distance nulle : l'ordre des extraits
    est celui de l'ingestion. `drop` retire un extrait, `extra` en ajoute un inconnu."""
    passages: dict[str, list[Passage]] = {}
    for index, (meta, _, declared) in enumerate(ingestion.pending_chunks(MAX_WORDS)):
        if index == drop:
            continue
        for domain, kinds in by_domain(declared):
            passages.setdefault(domain, []).append(
                Passage(
                    id=index,
                    domain=domain,
                    source_id=meta["source_id"],
                    reference=meta["reference"],
                    text=meta["text"],
                    distance=0.0,
                    kinds=kinds,
                )
            )
    if extra:
        passages["financier"].append(
            Passage(
                id=-1,
                domain="financier",
                source_id="inconnue",
                reference="Source inconnue",
                text="texte absent des fichiers",
                distance=0.0,
                kinds=["delai_paiement"],
            )
        )
    return FakeRetriever(passages)


# les deux écarts du jeu avec le rattachement déclaré (ADR 006) : requête, référence
# attendue que le filtre écarte
HORS_RATTACHEMENT = {
    "durée et fin du contrat : engagement, préavis de résiliation ; "
    "durée d'engagement : non chiffrée": "C. civ., art. 1211",
    "protection des données personnelles : sous-traitance, transferts hors de l'Union "
    "européenne ; transfert de données personnelles hors de l'Union européenne : "
    "clause absente": "RGPD, art. 28",
}


def by_query(report) -> dict[str, dict]:
    return {q["requete"]: q for q in report["par_requete"]}


def test_mesure_avec_filtre_rappel_plein_sauf_les_ecarts_au_rattachement():
    """Avec le filtre et k au-delà de chaque groupe d'extraits rattachés, toute référence
    attendue est rendue, sauf les deux que le rattachement déclaré ne lie pas à la
    clause : l'article 1211 (durée non chiffrée), déclaré pour le préavis seul, et
    l'article 28 du RGPD (localisation des données), déclaré pour l'accord seul."""
    double = corpus_double()
    report = evaluation.measure(QUERIES, double, double, ks=(1, 500))
    rows = by_query(report)
    assert len(rows) == 21
    for query, row in rows.items():
        expected = 0.666667 if query in HORS_RATTACHEMENT else 1.0
        assert row["filtre"]["toutes"][500]["rappel"] == expected, query
    for query, reference in HORS_RATTACHEMENT.items():
        assert rows[query]["filtre"]["rangs"][reference] is None
        assert rows[query]["sans_filtre"]["rangs"][reference] is not None
    assert report["moyennes"]["filtre"]["toutes"][500]["rappel"] == rounded(
        (19 + 2 * 0.666667) / 21
    )


def test_mesure_sans_filtre_tout_le_corpus_et_moyennes_par_k():
    double = corpus_double()
    report = evaluation.measure(QUERIES, double, double, ks=(4, 500))
    rows = by_query(report)
    for row in rows.values():
        assert row["sans_filtre"]["toutes"][500]["rappel"] == 1.0
        assert row["sans_filtre"]["articles"][500]["rappel"] == 1.0
    assert set(report["moyennes"]) == {"filtre", "sans_filtre"}
    for mode in report["moyennes"].values():
        assert set(mode) == {"toutes", "articles"}
        for scope in mode.values():
            assert set(scope) == {4, 500}
            for k in (4, 500):
                assert scope[k]["echec"] == rounded(1 - scope[k]["rappel"])
    # une recherche par requête et par mode, au rang le plus grand
    assert len(double.calls) == len(double.unfiltered_calls) == 21
    assert (
        {k for _, _, k in double.calls}
        == {k for _, k in double.unfiltered_calls}
        == {500}
    )


def test_articles_seuls_les_fiches_occupent_leur_rang_sans_compter():
    """Articles seuls : attendues réduites aux articles de loi, et parmi les k premiers
    extraits rendus, les fiches occupent leur rang mais ne comptent ni pour ni contre."""
    q = next(q for q in QUERIES if q.kind == "revision_prix")
    fiche = next(r.reference for r in q.expected if r.type == "fiche")
    article = next(r.reference for r in q.expected if r.type == "article")

    def passage(index: int, reference: str) -> Passage:
        return Passage(
            id=index,
            domain="financier",
            source_id="s",
            reference=reference,
            text=f"extrait {index}",
            distance=0.0,
            kinds=["revision_prix"],
        )

    double = FakeRetriever(
        {"financier": [passage(1, fiche), passage(2, "C. com., art. L441-10")]}
        | {"juridique": [passage(3, article)]}
    )
    row = evaluation.measure([q], double, double, ks=(1, 2, 3))["par_requete"][0]
    toutes, articles = row["sans_filtre"]["toutes"], row["sans_filtre"]["articles"]
    assert toutes[1] == {"rappel": 0.5, "precision": 1.0}
    # au rang 1, la fiche seule : elle ne compte ni pour ni contre
    assert articles[1] == {"rappel": 0.0, "precision": 0.0}
    assert articles[2] == {"rappel": 0.0, "precision": 0.0}
    assert articles[3] == {"rappel": 1.0, "precision": 0.5}
    assert row["sans_filtre"]["rendues"] == [fiche, "C. com., art. L441-10", article]
    assert row["sans_filtre"]["rangs"] == {article: 3, fiche: 1}


def test_corpus_indexe_conforme_aux_fichiers():
    assert evaluation.check_index(corpus_double(), MAX_WORDS) == len(
        ingestion.pending_chunks(MAX_WORDS)
    )


@pytest.mark.parametrize(
    ("double", "detail"),
    [
        (corpus_double(drop=0), "1 extrait(s) manquant(s), 0 en trop"),
        (corpus_double(extra=True), "0 extrait(s) manquant(s), 1 en trop"),
    ],
)
def test_corpus_indexe_different_des_fichiers_erreur_explicite(double, detail):
    with pytest.raises(
        evaluation.IndexMismatch, match=re.escape(detail) + ".*relancer ingest"
    ):
        evaluation.check_index(double, MAX_WORDS)


def test_jeu_en_retard_sur_le_jeu_de_demonstration_erreur_explicite(monkeypatch):
    real = evaluation.demo_queries(CONFIG)
    added = evaluation.DemoQuery("financier", "delai_paiement", "requête neuve", ("x",))
    monkeypatch.setattr(evaluation, "demo_queries", lambda config: [*real[1:], added])
    double = corpus_double()
    with pytest.raises(evaluation.EvaluationSetOutdated) as exc:
        evaluation.run(CONFIG, double, double, ks=(4,))
    assert "requête neuve" in str(exc.value) and real[0].query in str(exc.value)


def test_run_verifie_puis_mesure():
    double = corpus_double()
    report = evaluation.run(CONFIG, double, double, ks=(1, 4))
    assert report["requetes"] == 21 and report["k"] == [1, 4]
    # réglages de la recherche mesurée, ceux de la configuration
    assert report["recherche"] == CONFIG.crag.search.model_dump()
    assert report["extraits"] == len(ingestion.pending_chunks(MAX_WORDS))
    assert len(report["par_requete"]) == 21


# --- commande de la CLI ----------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "-1", "a", "4,,8", ""])
def test_mesure_rangs_invalides_refuses(value, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["mesure-recherche", "--k", value])
    assert exc.value.code == 2


@pytest.mark.pg
def test_commande_mesure_recherche_sur_le_corpus_indexe(
    pg, capsys, monkeypatch, tmp_path
):
    """Corpus indexé par une doublure d'embedding (sans poids), puis mesure par la CLI :
    avec le filtre, au-delà des groupes rattachés, le rappel ne dépend pas de l'embedding
    et vaut 1, sauf pour les deux écarts au rattachement."""
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path))
    embedder = HashEmbedder()
    monkeypatch.setattr(
        cli.fastembed, "FastembedEmbedder", lambda config, cache_dir, **kw: embedder
    )
    rag_store.sync(pg.admin, ingestion.rows(embedder, MAX_WORDS), embedder.model)
    try:
        code, out = run_cli(capsys, "mesure-recherche", "--k", "60,1,4,4")
        assert code == 0 and out["mesure_recherche"] == "ok"
        assert out["modele"] == "hash-test" and out["k"] == [1, 4, 60]
        assert out["recherche"] == CONFIG.crag.search.model_dump()
        assert out["requetes"] == 21
        assert out["extraits"] == len(ingestion.pending_chunks(MAX_WORDS))
        rows = by_query(out)
        for query, row in rows.items():
            expected = 0.666667 if query in HORS_RATTACHEMENT else 1.0
            assert row["filtre"]["toutes"]["60"]["rappel"] == expected, query
    finally:
        with psycopg.connect(pg.admin) as conn:
            conn.execute(
                "DELETE FROM rag_chunks WHERE embedding_model = %s", (embedder.model,)
            )


@pytest.mark.pg
def test_commande_mesure_recherche_corpus_absent_erreur_explicite(
    pg, capsys, monkeypatch, tmp_path
):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path))

    class Absent(HashEmbedder):
        model = "modele-jamais-indexe"

    monkeypatch.setattr(
        cli.fastembed, "FastembedEmbedder", lambda config, cache_dir, **kw: Absent()
    )
    code, err = run_cli(capsys, "mesure-recherche")
    assert code == 1 and err["erreur"] == "IndexMismatch"
    assert "relancer ingest" in err["detail"]

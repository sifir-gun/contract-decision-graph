"""Recherche dans le corpus : filtre par domaine AVANT la distance, recherche exacte."""

from datetime import date

import psycopg
import pytest
from doubles import HashEmbedder

from cdg.adapters.postgres import rag_store
from cdg.domain.corpus import ChunkRow

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
SOURCE = "test-retrieval"
CHUNKS = [
    (
        "conformite",
        "RGPD, art. 28",
        "Le sous-traitant agit sur instruction documentée.",
    ),
    ("conformite", "RGPD, art. 32", "Sécurité du traitement et mesures techniques."),
    (
        "financier",
        "C. com., art. L441-10",
        "Pénalités de retard et indemnité forfaitaire.",
    ),
    # très proche de la requête, mais d'un autre domaine : ne doit jamais sortir
    (
        "financier",
        "piège",
        "Le sous-traitant agit sur instruction documentée du responsable.",
    ),
]

VALID_UNTIL = date(2027, 1, 1)
NOTE = "Conformément à l'article 3 de la loi de test."


@pytest.fixture
def corpus(pg):
    rows = [
        ChunkRow(
            domain=d,
            source_id=SOURCE,
            reference=r,
            text=t,
            embedding_model=EMBEDDER.model,
            embedding=EMBEDDER.embed_passages([t])[0],
        )
        for d, r, t in CHUNKS
    ]
    rows[2] = rows[2].model_copy(update={"valid_until": VALID_UNTIL, "note": NOTE})
    inserted = rag_store.insert(pg.admin, rows)
    yield inserted
    with psycopg.connect(pg.admin) as conn:
        conn.execute("DELETE FROM rag_chunks WHERE source_id = %s", (SOURCE,))


def test_insertion_idempotente(pg, corpus):
    rows = [
        ChunkRow(
            domain=d,
            source_id=SOURCE,
            reference=r,
            text=t,
            embedding_model=EMBEDDER.model,
            embedding=EMBEDDER.embed_passages([t])[0],
        )
        for d, r, t in CHUNKS
    ]
    assert corpus == 4 and rag_store.insert(pg.admin, rows) == 0


def test_filtre_par_domaine_avant_la_recherche(pg, corpus):
    query = EMBEDDER.embed_query("sous-traitant instruction documentée")
    found = rag_store.search(pg.app, "conformite", query, k=5, model=EMBEDDER.model)
    assert [c.reference for c in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    assert all(c.domain == "conformite" for c in found)
    assert found[0].distance <= found[1].distance


def test_k_resultats_au_plus(pg, corpus):
    query = EMBEDDER.embed_query("sécurité")
    assert (
        len(rag_store.search(pg.app, "conformite", query, k=1, model=EMBEDDER.model))
        == 1
    )


def test_seul_le_modele_d_embedding_courant_est_interroge(pg, corpus):
    query = EMBEDDER.embed_query("sous-traitant")
    assert (
        rag_store.search(pg.app, "conformite", query, k=5, model="autre-modele") == []
    )


def test_recherche_rend_fin_de_validite_et_note(pg, corpus):
    query = EMBEDDER.embed_query("pénalités de retard indemnité forfaitaire")
    found = rag_store.search(pg.app, "financier", query, k=5, model=EMBEDDER.model)
    by_reference = {c.reference: c for c in found}
    l441 = by_reference["C. com., art. L441-10"]
    assert (l441.valid_until, l441.note) == (VALID_UNTIL, NOTE)
    assert (by_reference["piège"].valid_until, by_reference["piège"].note) == (
        None,
        None,
    )


def test_adaptateur_du_port_retriever_requete_en_texte(pg, corpus):
    embedder = HashEmbedder()
    retriever = rag_store.PgvectorRetriever(pg.app, embedder)
    found = retriever.search("conformite", "sous-traitant instruction documentée", k=5)
    assert [p.reference for p in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    assert embedder.calls == [
        "sous-traitant instruction documentée"
    ]  # vecteur par l'Embedder


def test_adaptateur_du_port_filtre_sur_son_modele(pg, corpus):
    class OtherModel(HashEmbedder):
        model = "autre-modele"

    retriever = rag_store.PgvectorRetriever(pg.app, OtherModel())
    assert retriever.search("conformite", "sous-traitant", k=5) == []

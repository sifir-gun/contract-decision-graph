"""Recherche dans le corpus : filtre par domaine AVANT la distance, recherche exacte."""

import psycopg
import pytest
from doubles import HashEmbedder

from cdg.adapters.postgres import rag_store
from cdg.domain.corpus import ChunkRow

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
SOURCE = "test-retrieval"
CHUNKS = [
    ("conformite", "RGPD, art. 28", "Le sous-traitant agit sur instruction documentée."),
    ("conformite", "RGPD, art. 32", "Sécurité du traitement et mesures techniques."),
    ("financier", "C. com., art. L441-10", "Pénalités de retard et indemnité forfaitaire."),
    # très proche de la requête, mais d'un autre domaine : ne doit jamais sortir
    ("financier", "piège", "Le sous-traitant agit sur instruction documentée du responsable."),
]


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
    assert len(rag_store.search(pg.app, "conformite", query, k=1, model=EMBEDDER.model)) == 1


def test_seul_le_modele_d_embedding_courant_est_interroge(pg, corpus):
    query = EMBEDDER.embed_query("sous-traitant")
    assert rag_store.search(pg.app, "conformite", query, k=5, model="autre-modele") == []

"""Ingestion du corpus réel dans rag_chunks (embedder de test) : versions, rejouable."""

from datetime import date

import psycopg
import pytest
from doubles import HashEmbedder

from cdg import corpus, rag_store
from cdg.config import load_config

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
MAX_WORDS = load_config().corpus.chunk_max_words


@pytest.fixture
def ingested(pg):
    rows = corpus.rows(EMBEDDER, MAX_WORDS)
    summary = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    yield rows, summary
    with psycopg.connect(pg.admin) as conn:
        conn.execute("DELETE FROM rag_chunks WHERE embedding_model = %s", (EMBEDDER.model,))


def query(pg, sql, *params):
    with psycopg.connect(pg.admin) as conn:
        return conn.execute(sql, params).fetchall()


def test_ingestion_rejouable(pg, ingested):
    rows, first = ingested
    assert first["inserted"] == len(rows) > 0
    again = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    assert (again["inserted"], again["deleted"], again["unchanged"]) == (0, 0, len(rows))


def test_version_bornee_stockee(pg, ingested):
    [(valid_until, amendment)] = set(
        query(
            pg,
            "SELECT valid_until, amendment FROM rag_chunks WHERE embedding_model = %s "
            "AND reference = 'C. com., art. L441-10'",
            EMBEDDER.model,
        )
    )
    assert valid_until == date(2027, 1, 1)
    assert amendment == "modification : Ordonnance n°2019-359 du 24 avril 2019 - art. 1"


def test_note_stockee_hors_du_texte(pg, ingested):
    rows = query(
        pg,
        "SELECT text, note FROM rag_chunks WHERE embedding_model = %s "
        "AND reference = 'C. civ., art. 1171'",
        EMBEDDER.model,
    )
    assert rows and all(
        "Conformément" not in text and note.startswith("Conformément") for text, note in rows
    )


def test_hors_perimetre_et_domaines(pg, ingested):
    refs = {
        r
        for (r,) in query(
            pg,
            "SELECT DISTINCT reference FROM rag_chunks WHERE embedding_model = %s",
            EMBEDDER.model,
        )
    }
    assert "RGPD, art. 79" not in refs and "RGPD, art. 44" in refs
    domains = {
        d
        for (d,) in query(
            pg,
            "SELECT domain FROM rag_chunks WHERE embedding_model = %s "
            "AND reference = 'C. com., art. L442-1'",
            EMBEDDER.model,
        )
    }
    assert domains == {"operationnel", "juridique"}
    fiches = query(
        pg,
        "SELECT DISTINCT source_id FROM rag_chunks WHERE embedding_model = %s "
        "AND source_id LIKE 'fiche-%%'",
        EMBEDDER.model,
    )
    assert len(fiches) == 6


def test_extrait_disparu_supprime(pg, ingested):
    rows, _ = ingested
    obsolete = rows[0].model_copy(update={"text": "ancienne rédaction nettoyée autrement"})
    rag_store.insert(pg.admin, [obsolete])
    summary = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    assert (summary["inserted"], summary["deleted"]) == (0, 1)

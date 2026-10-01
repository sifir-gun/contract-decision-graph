"""Ingestion du corpus réel dans rag_chunks (embedder de test) : versions, rejouable."""

from datetime import date

import psycopg
import pytest
from doubles import HashEmbedder

from cdg.adapters.postgres import rag_store
from cdg.application import ingestion
from cdg.domain import corpus
from cdg.domain.config import load_config
from cdg.domain.models import DOMAIN_KINDS

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
CONFIG = load_config()
MAX_WORDS = CONFIG.corpus.chunk_max_words
PREFIX = CONFIG.embedding.passage_prefix


@pytest.fixture
def ingested(pg):
    rows = ingestion.rows(EMBEDDER, MAX_WORDS, PREFIX)
    summary = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    yield rows, summary
    with psycopg.connect(pg.admin) as conn:
        conn.execute(
            "DELETE FROM rag_chunks WHERE embedding_model = %s", (EMBEDDER.model,)
        )


def query(pg, sql, *params):
    with psycopg.connect(pg.admin) as conn:
        return conn.execute(sql, params).fetchall()


def test_ingestion_rejouable(pg, ingested):
    rows, first = ingested
    assert first["inserted"] == len(rows) > 0
    again = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    assert (again["inserted"], again["deleted"], again["unchanged"]) == (
        0,
        0,
        len(rows),
    )


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
        "Conformément" not in text and note.startswith("Conformément")
        for text, note in rows
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
    assert len(fiches) == 7


def test_extrait_disparu_supprime(pg, ingested):
    rows, _ = ingested
    obsolete = rows[0].model_copy(
        update={"text": "ancienne rédaction nettoyée autrement"}
    )
    rag_store.insert(pg.admin, [obsolete])
    summary = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    assert (summary["inserted"], summary["deleted"]) == (0, 1)


def test_sync_remplace_un_extrait_dont_la_validite_change(pg, ingested):
    rows, _ = ingested
    target = next(r for r in rows if r.reference == "C. com., art. L441-10")
    changed = [
        r.model_copy(update={"valid_until": date(2028, 1, 1)}) if r is target else r
        for r in rows
    ]
    summary = rag_store.sync(pg.admin, changed, EMBEDDER.model)
    assert (summary["inserted"], summary["deleted"]) == (1, 1)
    [(valid_until,)] = query(
        pg,
        "SELECT valid_until FROM rag_chunks WHERE embedding_model = %s AND reference = %s"
        " AND chunk_index = %s AND domain = %s",
        EMBEDDER.model,
        target.reference,
        target.chunk_index,
        target.domain,
    )
    assert valid_until == date(2028, 1, 1)


def test_lignes_rattachees_aux_clauses_de_leur_domaine(pg, ingested):
    rows, _ = ingested
    for row in rows:
        assert row.kinds and set(row.kinds) <= set(DOMAIN_KINDS[row.domain]), row
    stored = dict(
        query(
            pg,
            "SELECT DISTINCT domain, kinds FROM rag_chunks WHERE embedding_model = %s "
            "AND reference = 'C. com., art. L442-1'",
            EMBEDDER.model,
        )
    )
    assert stored == {
        "juridique": ["responsabilite_acheteur", "responsabilite_fournisseur"],
        "operationnel": ["preavis_resiliation"],
    }


def test_rattachement_modifie_extrait_remplace(pg, ingested):
    rows, _ = ingested
    changed = [
        r.model_copy(update={"kinds": r.kinds[:1]})
        if r.reference == "C. com., art. L442-1" and r.domain == "juridique"
        else r
        for r in rows
    ]
    summary = rag_store.sync(pg.admin, changed, EMBEDDER.model)
    assert summary["deleted"] == summary["inserted"] > 0


# --- réindexation : empreinte du texte embarqué (défaut relevé le 01/10, ADR 006) ---------


def stored_hashes(pg) -> dict[tuple, str]:
    return {
        (reference, domain, index): embedded
        for reference, domain, index, embedded in query(
            pg,
            "SELECT reference, domain, chunk_index, embedded_hash FROM rag_chunks "
            "WHERE embedding_model = %s",
            EMBEDDER.model,
        )
    }


def test_en_tete_et_empreinte_du_texte_embarque_stockes(pg, ingested):
    rows, _ = ingested
    stored = stored_hashes(pg)
    for row in rows:
        assert row.embedded_hash == corpus.embedded_hash(
            PREFIX, corpus.embedded_text(row.header, row.text)
        )
        assert stored[(row.reference, row.domain, row.chunk_index)] == row.embedded_hash
    [(header,)] = set(
        query(
            pg,
            "SELECT header FROM rag_chunks WHERE embedding_model = %s "
            "AND reference = 'RGPD, art. 28'",
            EMBEDDER.model,
        )
    )
    assert header.startswith("RGPD, art. 28")


def test_en_tete_change_texte_stocke_identique_extrait_reindexe(pg, ingested):
    """Le texte stocké, donc son empreinte, ne change pas ; le texte embarqué si : le
    vecteur doit être recalculé. Avant la correction, sync n'y voyait rien."""
    rows, _ = ingested
    target = next(r for r in rows if r.reference == "C. civ., art. 1170")
    header = target.header + " — Sous-section 3 : Le contenu du contrat"
    changed = [
        r.model_copy(
            update={
                "header": header,
                "embedded_hash": corpus.embedded_hash(
                    PREFIX, corpus.embedded_text(header, r.text)
                ),
            }
        )
        if r is target
        else r
        for r in rows
    ]
    summary = rag_store.sync(pg.admin, changed, EMBEDDER.model)
    assert (summary["inserted"], summary["deleted"]) == (1, 1)


def test_prefixe_de_passage_change_tout_le_corpus_reindexe(pg, ingested):
    rows, _ = ingested
    other = ingestion.rows(EMBEDDER, MAX_WORDS, "autre préfixe : ")
    summary = rag_store.sync(pg.admin, other, EMBEDDER.model)
    assert summary["inserted"] == summary["deleted"] == len(rows)
    assert set(stored_hashes(pg).values()) == {r.embedded_hash for r in other}


def test_extraits_indexes_avant_la_migration_008_reindexes(pg, ingested):
    """Sans empreinte du texte embarqué (colonne vide) : vecteur d'origine inconnue,
    l'extrait est remplacé au prochain ingest."""
    rows, _ = ingested
    with psycopg.connect(pg.admin) as conn:
        conn.execute(
            "UPDATE rag_chunks SET embedded_hash = NULL, header = NULL "
            "WHERE embedding_model = %s",
            (EMBEDDER.model,),
        )
    summary = rag_store.sync(pg.admin, rows, EMBEDDER.model)
    assert summary["inserted"] == summary["deleted"] == len(rows)

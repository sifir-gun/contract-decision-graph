"""Recherche dans le corpus : filtre par domaine, clause et modèle AVANT la distance,
recherche exacte."""

from datetime import date

import psycopg
import pytest
from doubles import HashEmbedder
from pydantic import ValidationError

from cdg.adapters.postgres import rag_store
from cdg.domain.corpus import ChunkRow

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
SOURCE = "test-retrieval"
ACCORD, DELAI = "accord_traitement_donnees", "delai_paiement"
CHUNKS = [
    (
        "conformite",
        "RGPD, art. 28",
        "Le sous-traitant agit sur instruction documentée.",
        [ACCORD],
    ),
    (
        "conformite",
        "RGPD, art. 32",
        "Sécurité du traitement et mesures techniques.",
        [ACCORD],
    ),
    # même domaine, très proche de la requête, mais rattaché à une autre clause
    (
        "conformite",
        "RGPD, art. 46",
        "Le sous-traitant agit sur instruction documentée pour un transfert.",
        ["transfert_hors_ue"],
    ),
    (
        "financier",
        "C. com., art. L441-10",
        "Pénalités de retard et indemnité forfaitaire.",
        [DELAI],
    ),
    # très proche de la requête, mais d'un autre domaine : ne doit jamais sortir
    (
        "financier",
        "piège",
        "Le sous-traitant agit sur instruction documentée du responsable.",
        [DELAI],
    ),
]

VALID_UNTIL = date(2027, 1, 1)
NOTE = "Conformément à l'article 3 de la loi de test."


def rows():
    return [
        ChunkRow(
            domain=d,
            source_id=SOURCE,
            reference=r,
            text=t,
            kinds=k,
            embedding_model=EMBEDDER.model,
            embedding=EMBEDDER.embed_passages([t])[0],
        )
        for d, r, t, k in CHUNKS
    ]


@pytest.fixture
def corpus(pg):
    found = rows()
    found[3] = found[3].model_copy(update={"valid_until": VALID_UNTIL, "note": NOTE})
    inserted = rag_store.insert(pg.admin, found)
    yield inserted
    with psycopg.connect(pg.admin) as conn:
        conn.execute("DELETE FROM rag_chunks WHERE source_id = %s", (SOURCE,))


def search(pg, domain, text, kind, k=5, model=EMBEDDER.model):
    query = EMBEDDER.embed_query(text)
    return rag_store.search(pg.app, domain, query, kind=kind, k=k, model=model)


def test_insertion_idempotente(pg, corpus):
    assert corpus == len(CHUNKS) and rag_store.insert(pg.admin, rows()) == 0


def test_filtre_par_domaine_et_clause_avant_la_recherche(pg, corpus):
    found = search(pg, "conformite", "sous-traitant instruction documentée", ACCORD)
    assert [c.reference for c in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    assert all(c.domain == "conformite" and ACCORD in c.kinds for c in found)
    assert found[0].distance <= found[1].distance


def test_extrait_d_une_autre_clause_jamais_rendu(pg, corpus):
    found = search(
        pg, "conformite", "sous-traitant instruction transfert", "transfert_hors_ue"
    )
    assert [(c.reference, c.kinds) for c in found] == [
        ("RGPD, art. 46", ["transfert_hors_ue"])
    ]


def test_k_resultats_au_plus(pg, corpus):
    assert len(search(pg, "conformite", "sécurité", ACCORD, k=1)) == 1


def test_seul_le_modele_d_embedding_courant_est_interroge(pg, corpus):
    assert search(pg, "conformite", "sous-traitant", ACCORD, model="autre-modele") == []


def test_recherche_rend_fin_de_validite_et_note(pg, corpus):
    found = search(pg, "financier", "pénalités de retard indemnité forfaitaire", DELAI)
    by_reference = {c.reference: c for c in found}
    l441 = by_reference["C. com., art. L441-10"]
    assert (l441.valid_until, l441.note) == (VALID_UNTIL, NOTE)
    assert (by_reference["piège"].valid_until, by_reference["piège"].note) == (
        None,
        None,
    )


def test_extraits_sans_rattachement_erreur_explicite(pg, corpus):
    # extrait indexé avant la migration 005 : jamais ignoré en silence, ingest à relancer
    with psycopg.connect(pg.admin) as conn:
        conn.execute(
            "UPDATE rag_chunks SET kinds = NULL WHERE source_id = %s "
            "AND reference = 'RGPD, art. 32'",
            (SOURCE,),
        )
    with pytest.raises(rag_store.RagStoreError, match="relancer ingest"):
        search(pg, "financier", "pénalités", DELAI)


def test_rattachement_hors_du_domaine_refuse():
    with pytest.raises(ValidationError, match="delai_paiement"):
        ChunkRow(
            domain="conformite",
            source_id=SOURCE,
            reference="r",
            text="t",
            kinds=[DELAI],
            embedding_model=EMBEDDER.model,
            embedding=[0.0],
        )
    with pytest.raises(ValidationError, match="aucun type"):
        ChunkRow(
            domain="conformite",
            source_id=SOURCE,
            reference="r",
            text="t",
            kinds=[],
            embedding_model=EMBEDDER.model,
            embedding=[0.0],
        )


def test_adaptateur_du_port_retriever_requete_en_texte(pg, corpus):
    embedder = HashEmbedder()
    retriever = rag_store.PgvectorRetriever(pg.app, embedder)
    found = retriever.search(
        "conformite", "sous-traitant instruction documentée", kind=ACCORD, k=5
    )
    assert [p.reference for p in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    assert embedder.calls == [
        "sous-traitant instruction documentée"
    ]  # vecteur par l'Embedder


def test_adaptateur_du_port_filtre_sur_son_modele(pg, corpus):
    class OtherModel(HashEmbedder):
        model = "autre-modele"

    retriever = rag_store.PgvectorRetriever(pg.app, OtherModel())
    assert retriever.search("conformite", "sous-traitant", kind=ACCORD, k=5) == []


# --- recherche sans filtre (mesure de la recherche seule, ADR 006) ----------------------


@pytest.fixture
def duplicated(pg, corpus):
    """Un même extrait indexé dans deux domaines (une ligne par domaine, comme
    l'ingestion d'une source rattachée à des clauses de deux domaines)."""
    text = "Déséquilibre significatif entre les droits et obligations des parties."
    rows = [
        ChunkRow(
            domain=domain,
            source_id=SOURCE,
            reference="C. com., art. L442-1",
            text=text,
            kinds=[kind],
            embedding_model=EMBEDDER.model,
            embedding=EMBEDDER.embed_passages([text])[0],
        )
        for domain, kind in [
            ("juridique", "responsabilite_acheteur"),
            ("operationnel", "preavis_resiliation"),
        ]
    ]
    assert rag_store.insert(pg.admin, rows) == 2


def test_sans_filtre_tout_le_corpus_du_modele_chaque_extrait_une_fois(pg, duplicated):
    retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder())
    found = retriever.search_unfiltered("sous-traitant instruction documentée", k=50)
    # autres domaines et autres clauses compris ; l'extrait des deux domaines, une fois
    assert sorted(p.reference for p in found) == sorted(
        [r for _, r, _, _ in CHUNKS] + ["C. com., art. L442-1"]
    )
    distances = [p.distance for p in found]
    assert distances == sorted(distances)
    assert found[0].text.startswith("Le sous-traitant agit sur instruction documentée")


def test_sans_filtre_k_resultats_au_plus_et_modele_courant_seul(pg, corpus):
    retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder())
    assert len(retriever.search_unfiltered("sécurité", k=2)) == 2

    class OtherModel(HashEmbedder):
        model = "autre-modele"

    other = rag_store.PgvectorRetriever(pg.app, OtherModel())
    assert other.search_unfiltered("sous-traitant", k=50) == []

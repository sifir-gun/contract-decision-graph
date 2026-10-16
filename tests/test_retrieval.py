"""Recherche dans le corpus : filtre par domaine, clause et modèle AVANT la distance,
recherche exacte."""

from datetime import date

import psycopg
import pytest
from doubles import HashEmbedder
from pydantic import ValidationError

from cdg.adapters.postgres import rag_store
from cdg.application import ingestion
from cdg.domain import corpus as corpus_text
from cdg.domain.config import SearchConfig, load_config
from cdg.domain.corpus import ChunkRow

pytestmark = pytest.mark.pg

EMBEDDER = HashEmbedder()
# recherche vectorielle seule, chaque extrait à son rang ; puis un extrait par référence
VECTOR = SearchConfig(distinct_references=False)
DISTINCT = SearchConfig(distinct_references=True)
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
ON = date(2026, 9, 25)  # date d'analyse des tests
NOTE = "Conformément à l'article 3 de la loi de test."


def embedded(reference: str, text: str) -> dict:
    """En-tête et empreinte du texte embarqué, comme à l'ingestion (référence seule)."""
    return {
        "header": reference,
        "embedded_hash": corpus_text.embedded_hash(
            "", corpus_text.embedded_text(reference, text)
        ),
    }


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
            **embedded(r, t),
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


def search(pg, domain, text, kind, k=5, model=EMBEDDER.model, settings=VECTOR, on=ON):
    query = EMBEDDER.embed_query(text)
    return rag_store.search(
        pg.app, domain, query, kind=kind, k=k, model=model, settings=settings, on=on
    )


def test_insertion_idempotente(pg, corpus):
    assert corpus == len(CHUNKS) and rag_store.insert(pg.admin, rows()) == 0


@pytest.fixture
def l441_row(pg):
    """La ligne de L441-10 de CHUNKS, seule, nettoyée après le test."""
    yield rows()[3]
    with psycopg.connect(pg.admin) as conn:
        conn.execute("DELETE FROM rag_chunks WHERE source_id = %s", (SOURCE,))


def test_meme_texte_deux_debuts_deux_lignes(pg, l441_row):
    """Migration 009 : un paragraphe commun à deux versions a une ligne par version."""
    old = l441_row.model_copy(update={"valid_from": date(2019, 4, 26)})
    new = l441_row.model_copy(update={"valid_from": date(2027, 1, 1)})
    assert rag_store.insert(pg.admin, [old]) == 1
    assert rag_store.insert(pg.admin, [new]) == 1


def test_insertion_sans_debut_deja_presente_ignoree(pg, l441_row):
    """NULLS NOT DISTINCT : un extrait sans début de validité reste dédoublonné."""
    assert rag_store.insert(pg.admin, [l441_row]) == 1
    assert rag_store.insert(pg.admin, [l441_row]) == 0


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
            **embedded("r", "t"),
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
            **embedded("r", "t"),
        )


def test_adaptateur_du_port_retriever_requete_en_texte(pg, corpus):
    embedder = HashEmbedder()
    retriever = rag_store.PgvectorRetriever(pg.app, embedder, VECTOR)
    found = retriever.search(
        "conformite", "sous-traitant instruction documentée", kind=ACCORD, k=5, on=ON
    )
    assert [p.reference for p in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    assert embedder.calls == [
        "sous-traitant instruction documentée"
    ]  # vecteur par l'Embedder


def test_adaptateur_du_port_filtre_sur_son_modele(pg, corpus):
    class OtherModel(HashEmbedder):
        model = "autre-modele"

    retriever = rag_store.PgvectorRetriever(pg.app, OtherModel(), VECTOR)
    assert (
        retriever.search("conformite", "sous-traitant", kind=ACCORD, k=5, on=ON) == []
    )


# --- recherche sans filtre (mesure de la recherche seule, ADR 006) ----------------------


DUPLICATED = "Déséquilibre significatif entre les droits et obligations des parties."


@pytest.fixture
def duplicated(pg, corpus):
    """Un même extrait indexé dans deux domaines (une ligne par domaine, comme
    l'ingestion d'une source rattachée à des clauses de deux domaines)."""
    text = DUPLICATED
    rows = [
        ChunkRow(
            domain=domain,
            source_id=SOURCE,
            reference="C. com., art. L442-1",
            text=text,
            kinds=[kind],
            embedding_model=EMBEDDER.model,
            embedding=EMBEDDER.embed_passages([text])[0],
            **embedded("C. com., art. L442-1", text),
        )
        for domain, kind in [
            ("juridique", "responsabilite_acheteur"),
            ("operationnel", "preavis_resiliation"),
        ]
    ]
    assert rag_store.insert(pg.admin, rows) == 2


def test_sans_filtre_tout_le_corpus_du_modele_chaque_extrait_une_fois(pg, duplicated):
    retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), VECTOR)
    found = retriever.search_unfiltered(
        "sous-traitant instruction documentée", k=50, on=ON
    )
    # autres domaines et autres clauses compris ; l'extrait des deux domaines, une fois
    assert sorted(p.reference for p in found) == sorted(
        [r for _, r, _, _ in CHUNKS] + ["C. com., art. L442-1"]
    )
    distances = [p.distance for p in found]
    assert distances == sorted(distances)
    assert found[0].text.startswith("Le sous-traitant agit sur instruction documentée")


def test_sans_filtre_k_resultats_au_plus_et_modele_courant_seul(pg, corpus):
    retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), VECTOR)
    assert len(retriever.search_unfiltered("sécurité", k=2, on=ON)) == 2

    class OtherModel(HashEmbedder):
        model = "autre-modele"

    other = rag_store.PgvectorRetriever(pg.app, OtherModel(), VECTOR)
    assert other.search_unfiltered("sous-traitant", k=50, on=ON) == []


def test_lecture_des_extraits_indexes_chacun_une_fois(pg, duplicated):
    """Extraits indexés du modèle (référence, début de validité, texte, empreinte du
    texte embarqué), chacun une fois, quel que soit le réglage de la recherche : la
    mesure les compare aux fichiers du corpus."""
    expected = sorted(
        (r, None, t, embedded(r, t)["embedded_hash"])
        for r, t in [(r, t) for _, r, t, _ in CHUNKS]
        + [("C. com., art. L442-1", DUPLICATED)]
    )
    for settings in (VECTOR, DISTINCT):
        retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), settings)
        assert sorted(retriever.indexed()) == expected


# --- un extrait par référence (ADR 006, PR 2, technique 1) ------------------------------


@pytest.fixture
def long_article(pg, corpus):
    """Un second extrait de l'article 28, très proche de la requête : l'article occupe
    alors deux rangs, sauf si la recherche ne garde qu'un extrait par référence."""
    text = (
        "Le sous-traitant agit sur instruction documentée, y compris pour un transfert."
    )
    row = ChunkRow(
        domain="conformite",
        source_id=SOURCE,
        reference="RGPD, art. 28",
        text=text,
        kinds=[ACCORD],
        embedding_model=EMBEDDER.model,
        embedding=EMBEDDER.embed_passages([text])[0],
        chunk_index=1,
        **embedded("RGPD, art. 28", text),
    )
    assert rag_store.insert(pg.admin, [row]) == 1


QUERY = "sous-traitant instruction documentée"


def test_sans_reglage_une_reference_occupe_plusieurs_rangs(pg, long_article):
    found = search(pg, "conformite", QUERY, ACCORD, k=2)
    assert [p.reference for p in found] == ["RGPD, art. 28", "RGPD, art. 28"]


def test_un_extrait_par_reference_le_plus_proche(pg, long_article):
    found = search(pg, "conformite", QUERY, ACCORD, k=2, settings=DISTINCT)
    assert [p.reference for p in found] == ["RGPD, art. 28", "RGPD, art. 32"]
    closest = search(pg, "conformite", QUERY, ACCORD, k=1)[0]
    assert found[0].id == closest.id  # l'extrait gardé est le plus proche
    retriever = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), DISTINCT)
    assert [
        p.reference
        for p in retriever.search("conformite", QUERY, kind=ACCORD, k=5, on=ON)
    ] == ["RGPD, art. 28", "RGPD, art. 32"]


def test_sans_filtre_un_extrait_par_reference(pg, long_article, duplicated):
    vector = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), VECTOR)
    distinct = rag_store.PgvectorRetriever(pg.app, HashEmbedder(), DISTINCT)
    every = [p.reference for p in vector.search_unfiltered(QUERY, k=50, on=ON)]
    once = [p.reference for p in distinct.search_unfiltered(QUERY, k=50, on=ON)]
    assert every.count("RGPD, art. 28") == 2
    assert once == list(dict.fromkeys(every))


# --- versions successives : la date d'analyse choisit (docs/spec-versions-corpus.md) ----

SYNTHETIQUES = ["code-test", "fiche-test"]
MAX_WORDS = load_config().corpus.chunk_max_words


@pytest.fixture
def synthetique(pg, corpus_synthetique):
    """Corpus synthétique indexé : un article à deux versions, dans deux domaines."""
    lignes = ingestion.rows(EMBEDDER, MAX_WORDS, "")
    assert rag_store.insert(pg.admin, lignes) == len(lignes)
    yield lignes
    with psycopg.connect(pg.admin) as conn:
        conn.execute(
            "DELETE FROM rag_chunks WHERE source_id = ANY(%s)", (SYNTHETIQUES,)
        )


@pytest.mark.parametrize(
    "on", [date(2019, 1, 1), date(2026, 8, 19), date(2026, 12, 31), date(2027, 1, 1)]
)
@pytest.mark.parametrize(
    "domain,kind", [("financier", DELAI), ("operationnel", "preavis_resiliation")]
)
def test_parite_sql_python_corpus_synthetique(pg, synthetique, on, domain, kind):
    """Le filtre SQL garde exactement ce que garde la règle pure `drop_superseded`."""
    found = search(pg, domain, "alinéa des deux versions", kind, k=100, on=on)
    rendus = {(p.reference, p.valid_from) for p in found if p.source_id in SYNTHETIQUES}
    lignes = [r for r in synthetique if r.domain == domain and kind in r.kinds]
    attendus = {
        (r.reference, r.valid_from) for r in corpus_text.drop_superseded(lignes, on)
    }
    assert rendus == attendus and rendus


@pytest.fixture
def reel(pg):
    """Corpus réel indexé (embedding de test) : L441-10 et ses deux versions."""
    lignes = ingestion.rows(EMBEDDER, MAX_WORDS, "")
    sources = sorted({r.source_id for r in lignes})
    assert rag_store.insert(pg.admin, lignes) == len(lignes)
    yield lignes, sources
    with psycopg.connect(pg.admin) as conn:
        conn.execute(
            "DELETE FROM rag_chunks WHERE embedding_model = %s AND source_id = ANY(%s)",
            (EMBEDDER.model, sources),
        )


@pytest.mark.parametrize(
    "on", [date(2026, 8, 19), date(2026, 12, 31), date(2027, 1, 1)]
)
def test_parite_sql_python_corpus_reel(pg, reel, on):
    lignes, sources = reel
    found = search(pg, "financier", "délai de paiement", DELAI, k=1000, on=on)
    rendus = {(p.reference, p.valid_from) for p in found if p.source_id in sources}
    candidates = [r for r in lignes if r.domain == "financier" and DELAI in r.kinds]
    attendus = {
        (r.reference, r.valid_from) for r in corpus_text.drop_superseded(candidates, on)
    }
    assert rendus == attendus
    l441 = {start for ref, start in rendus if ref == "C. com., art. L441-10"}
    assert l441 == {date(2027, 1, 1) if on >= date(2027, 1, 1) else date(2019, 4, 26)}


def test_recherche_groupee_sans_version_en_vigueur_rend_la_plus_proche(pg, synthetique):
    """Un extrait par référence (réglage de production) : avant ses deux versions,
    l'extrait rendu pour T1 est celui de la première, jamais celui de la seconde, même
    plus proche de la requête ; le constat « pas encore en vigueur » cite sa date."""
    for query in ("Alinéa propre à la seconde version", "Alinéa propre à la première"):
        found = search(
            pg, "financier", query, DELAI, k=10, settings=DISTINCT, on=date(2019, 1, 1)
        )
        [t1] = [p for p in found if p.source_id == "code-test"]
        assert t1.valid_from == date(2019, 4, 26)


@pytest.fixture
def expirees(pg):
    """Une référence à deux versions, toutes deux expirées au 01/03/2027."""
    lignes = [
        rows()[3].model_copy(
            update={
                "reference": "X",
                "valid_from": start,
                "valid_until": end,
                "text": t,
            }
        )
        for start, end, t in [
            (date(2015, 1, 1), date(2019, 4, 26), "Ancienne rédaction."),
            (date(2019, 4, 26), date(2027, 1, 1), "Rédaction suivante."),
        ]
    ]
    assert rag_store.insert(pg.admin, lignes) == 2
    yield lignes
    with psycopg.connect(pg.admin) as conn:
        conn.execute("DELETE FROM rag_chunks WHERE source_id = %s", (SOURCE,))


def test_parite_toutes_expirees_la_derniere(pg, expirees):
    on = date(2027, 3, 1)
    found = search(pg, "financier", "rédaction", DELAI, k=10, on=on)
    rendus = {(p.reference, p.valid_from) for p in found if p.reference == "X"}
    attendus = {
        (r.reference, r.valid_from) for r in corpus_text.drop_superseded(expirees, on)
    }
    assert rendus == attendus == {("X", date(2019, 4, 26))}

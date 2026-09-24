"""Embedding local : préfixes e5, dimension contrôlée, jamais de téléchargement implicite."""

import pytest
from doubles import HashEmbedder

from cdg import embeddings, settings
from cdg.config import load_config

CONFIG = load_config().embedding


class FakeModel:
    def __init__(self, dimension=1024):
        self.dimension, self.seen = dimension, []

    def embed(self, texts):
        texts = list(texts)
        self.seen.extend(texts)
        return [[0.5] * self.dimension for _ in texts]


def test_prefixes_e5_ajoutes():
    model = FakeModel()
    embedder = embeddings.FastembedEmbedder(CONFIG, model=model)
    embedder.embed_passages(["article 28"])
    embedder.embed_query("sous-traitant")
    assert model.seen == ["passage: article 28", "query: sous-traitant"]


def test_vecteurs_en_listes_de_la_bonne_dimension():
    embedder = embeddings.FastembedEmbedder(CONFIG, model=FakeModel())
    [vector] = embedder.embed_passages(["texte"])
    assert isinstance(vector, list) and len(vector) == 1024
    assert (embedder.model, embedder.dimension) == ("intfloat/multilingual-e5-large", 1024)


def test_dimension_inattendue_erreur_explicite():
    embedder = embeddings.FastembedEmbedder(CONFIG, model=FakeModel(dimension=768))
    with pytest.raises(embeddings.EmbeddingError, match="768"):
        embedder.embed_query("texte")


def test_poids_absents_erreur_explicite_sans_telechargement(tmp_path):
    # cache vide et local_files_only : aucune tentative réseau, erreur qui cite la commande
    with pytest.raises(embeddings.EmbeddingError, match="fetch-embedding-model"):
        embeddings.FastembedEmbedder(CONFIG, cache_dir=tmp_path)


def test_dossier_de_cache_obligatoire(monkeypatch):
    monkeypatch.delenv("EMBEDDING_CACHE_DIR", raising=False)
    with pytest.raises(settings.SettingsError, match="EMBEDDING_CACHE_DIR"):
        settings.embedding_cache_dir()


def test_dossier_de_cache_relatif_au_depot(monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", ".cache/embeddings")
    path = settings.embedding_cache_dir()
    assert path.is_absolute() and path.parts[-2:] == (".cache", "embeddings")


def test_doublure_deterministe_et_normee():
    a, b = HashEmbedder(), HashEmbedder()
    assert a.embed_query("sous-traitant RGPD") == b.embed_query("sous-traitant RGPD")
    assert round(sum(v * v for v in a.embed_query("x y")), 6) == 1.0

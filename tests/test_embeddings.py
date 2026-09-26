"""Embedding local : préfixes e5, dimension contrôlée, jamais de téléchargement implicite."""

import pytest
from doubles import HashEmbedder

from cdg import settings
from cdg.adapters import fastembed
from cdg.domain.config import load_config

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
    embedder = fastembed.FastembedEmbedder(CONFIG, model=model)
    embedder.embed_passages(["article 28"])
    embedder.embed_query("sous-traitant")
    assert model.seen == ["passage: article 28", "query: sous-traitant"]


def test_vecteurs_en_listes_de_la_bonne_dimension():
    embedder = fastembed.FastembedEmbedder(CONFIG, model=FakeModel())
    [vector] = embedder.embed_passages(["texte"])
    assert isinstance(vector, list) and len(vector) == 1024
    assert (embedder.model, embedder.dimension) == (
        "intfloat/multilingual-e5-large",
        1024,
    )


def test_dimension_inattendue_erreur_explicite():
    embedder = fastembed.FastembedEmbedder(CONFIG, model=FakeModel(dimension=768))
    with pytest.raises(fastembed.EmbeddingError, match="768"):
        embedder.embed_query("texte")


def test_poids_absents_erreur_explicite_sans_telechargement(tmp_path):
    # cache vide et local_files_only : aucune tentative réseau, erreur qui cite la commande
    with pytest.raises(fastembed.EmbeddingError, match="fetch-embedding-model"):
        fastembed.FastembedEmbedder(CONFIG, cache_dir=tmp_path)


def test_poids_sans_dossier_de_cache_erreur_explicite():
    with pytest.raises(fastembed.EmbeddingError, match="EMBEDDING_CACHE_DIR"):
        fastembed.FastembedEmbedder(CONFIG)


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


def test_mise_a_plat_par_liens_physiques(tmp_path):
    # imite le cache Hugging Face : instantané en liens symboliques vers des blobs
    # rangés dans des sous-dossiers différents (refusé par onnxruntime 1.30)
    for shard, name, content in [("29", "a", b"modele"), ("9e", "b", b"poids")]:
        (tmp_path / "blobs" / shard).mkdir(parents=True)
        (tmp_path / "blobs" / shard / name).write_bytes(content)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "model.onnx").symlink_to(tmp_path / "blobs" / "29" / "a")
    (snapshot / "model.onnx_data").symlink_to(tmp_path / "blobs" / "9e" / "b")

    flat = tmp_path / "flat"
    fastembed.materialize(snapshot, flat)
    fastembed.materialize(snapshot, flat)  # idempotent
    for name, blob in [("model.onnx", "29/a"), ("model.onnx_data", "9e/b")]:
        target = flat / name
        assert not target.is_symlink() and target.samefile(tmp_path / "blobs" / blob)
        assert target.resolve().parent == flat.resolve()  # même dossier une fois résolu

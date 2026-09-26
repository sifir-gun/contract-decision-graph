"""Embedding local : préfixes e5, dimension contrôlée, jamais de téléchargement implicite,
aucun appel réseau (télémétrie d'onnxruntime coupée, J5)."""

import os
import shutil
import subprocess
import sys
import types

import pytest
from dotenv import dotenv_values
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


# --- Aucun appel réseau : télémétrie d'onnxruntime coupée (J5) --------------------------------


@pytest.mark.parametrize(
    ("load", "error"),
    [
        (lambda cache: fastembed.FastembedEmbedder(CONFIG, cache_dir=cache), Exception),
        (lambda cache: fastembed.fetch_model(CONFIG, cache), RuntimeError),
    ],
    ids=["analyse", "fetch-embedding-model"],
)
def test_telemetrie_coupee_avant_tout_chargement(monkeypatch, tmp_path, load, error):
    # ORT_DISABLE_TELEMETRY avant l'import de fastembed, qui charge onnxruntime : seule
    # la variable coupe tout ; disable_telemetry_events() laisse partir ProcessInfo
    calls = []

    def disable():
        calls.append(
            ("disable_telemetry_events", os.environ.get("ORT_DISABLE_TELEMETRY"))
        )

    class Loader:
        @staticmethod
        def _get_model_description(model):
            calls.append(("fastembed", os.environ.get("ORT_DISABLE_TELEMETRY")))
            raise RuntimeError("chargement interrompu par le test")

    monkeypatch.delenv("ORT_DISABLE_TELEMETRY", raising=False)
    fake_ort = types.SimpleNamespace(disable_telemetry_events=disable)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    fake_fastembed = types.SimpleNamespace(TextEmbedding=Loader)
    monkeypatch.setitem(sys.modules, "fastembed", fake_fastembed)
    with pytest.raises(error):
        load(tmp_path)
    *before, last = calls
    assert last == ("fastembed", "1")  # variable posée avant l'import de fastembed
    assert before and set(before) == {("disable_telemetry_events", "1")}


# Bac à sable de macOS : le noyau tue le processus à sa première connexion IP sortante,
# qu'elle vienne de Python ou d'une bibliothèque native (la télémétrie d'onnxruntime a son
# propre client HTTP, invisible pour le module socket de Python). sandbox-exec est déprécié
# par Apple mais fonctionne ; le témoin échoue s'il cessait de bloquer.
SANDBOX = "(version 1)(allow default)(deny network-outbound (remote ip) (with send-signal SIGKILL))"
# variables qui coupent d'elles-mêmes la télémétrie d'onnxruntime (1.30,
# core/platform/telemetry_environment.h) : retirées, pour que seul l'adaptateur la coupe
ORT_OPT_OUTS = (
    "CI",
    "TF_BUILD",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "CIRCLECI",
    "TRAVIS",
    "JENKINS_URL",
    "CODEBUILD_BUILD_ID",
    "BUILDKITE",
    "TEAMCITY_VERSION",
    "APPVEYOR",
    "BITBUCKET_BUILD_NUMBER",
    "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI",
    "ORT_RUNNING_UNIT_TESTS",
    "ORT_DISABLE_TELEMETRY",
    "HF_HUB_OFFLINE",  # conditions d'une analyse réelle : pas de garde-fou extérieur
)
# la télémétrie tente sa première connexion de 5 à 20 s après le chargement (mesuré le 26/09)
HOLD_SECONDS = 30
EMBED = """
import sys, time
from pathlib import Path
from cdg.adapters.fastembed import FastembedEmbedder
from cdg.domain.config import load_config
embedder = FastembedEmbedder(load_config().embedding, Path(sys.argv[1]))
assert len(embedder.embed_query("plafond de responsabilité")) == 1024
time.sleep(float(sys.argv[2]))
print("embedding calculé, aucune connexion")
"""


def sandboxed(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("sandbox-exec") is None:
        pytest.skip("bac à sable macOS (sandbox-exec) indisponible sur ce système")
    env = {k: v for k, v in os.environ.items() if k not in ORT_OPT_OUTS}
    return subprocess.run(
        ["sandbox-exec", "-p", SANDBOX, sys.executable, "-c", code, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,  # le code de retour est ce que le test examine
    )


def local_weights(monkeypatch):
    # le dossier des poids, comme la CLI le lit : environnement, sinon `.env` du poste ;
    # seule cette variable est reprise, le reste de l'environnement des tests ne change pas
    if not os.environ.get("EMBEDDING_CACHE_DIR"):
        value = dotenv_values(settings.DEFAULT_ENV_PATH).get("EMBEDDING_CACHE_DIR")
        if value:
            monkeypatch.setenv("EMBEDDING_CACHE_DIR", value)
    try:
        cache = settings.embedding_cache_dir()
    except settings.SettingsError:
        cache = None
    if (
        cache is None
        or not (fastembed.flat_dir(CONFIG, cache) / "model.onnx").is_file()
    ):
        pytest.skip(
            "poids du modèle d'embedding absents (CI) : test réseau sur les vrais poids "
            "seulement, après fetch-embedding-model"
        )
    return cache


def test_temoin_le_bac_a_sable_tue_un_processus_qui_se_connecte():
    # 192.0.2.1 : adresse réservée à la documentation (RFC 5737), jamais routée
    code = "import socket; socket.create_connection(('192.0.2.1', 443), timeout=5)"
    assert sandboxed(code).returncode == -9


def test_aucune_connexion_reseau_au_chargement_du_modele_ni_a_l_embedding(monkeypatch):
    cache = local_weights(monkeypatch)
    result = sandboxed(EMBED, str(cache), str(HOLD_SECONDS))
    assert result.returncode != -9, (
        "connexion réseau tentée : processus tué par le bac à sable"
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert "aucune connexion" in result.stdout

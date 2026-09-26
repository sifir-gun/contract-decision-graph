"""Embedding local (fastembed, ONNX) : préfixes e5, dimension contrôlée.

À l'exécution, jamais de téléchargement : les poids se récupèrent une fois par
`cdg.cli fetch-embedding-model`, dans EMBEDDING_CACHE_DIR.

onnxruntime >= 1.30 exige que les poids externes (`model.onnx_data`) soient dans le même
dossier que `model.onnx` une fois les liens résolus ; le cache Hugging Face range chaque
fichier dans un sous-dossier de blobs différent. D'où une copie « à plat » faite de liens
physiques (aucun espace disque supplémentaire), chargée par `specific_model_path`.
"""

import os
from pathlib import Path

from cdg.domain.config import EmbeddingConfig


class EmbeddingError(Exception):
    """Poids absents, ou vecteur d'une dimension inattendue."""


def _snapshot(
    config: EmbeddingConfig, cache_dir: Path, *, local_files_only: bool
) -> Path:
    from fastembed import TextEmbedding

    description = TextEmbedding._get_model_description(config.model)
    return Path(
        TextEmbedding.download_model(
            description, str(cache_dir), local_files_only=local_files_only
        )
    )


def flat_dir(config: EmbeddingConfig, cache_dir: Path) -> Path:
    return cache_dir / "flat" / config.model.replace("/", "--")


def materialize(snapshot: Path, flat: Path) -> None:
    """Liens physiques vers les fichiers réels de l'instantané, dans un seul dossier."""
    flat.mkdir(parents=True, exist_ok=True)
    for source in snapshot.iterdir():
        if not source.is_file():
            continue
        real, target = Path(os.path.realpath(source)), flat / source.name
        if target.exists():
            if target.samefile(real):
                continue
            target.unlink()
        try:
            os.link(real, target)
        except (
            OSError
        ) as exc:  # autre système de fichiers : pas de copie silencieuse de 2 Go
            raise EmbeddingError(
                f"lien physique impossible vers {real} : EMBEDDING_CACHE_DIR doit être sur "
                "un seul système de fichiers"
            ) from exc


def _load(config: EmbeddingConfig, cache_dir: Path, *, local_files_only: bool):
    from fastembed import TextEmbedding

    flat = flat_dir(config, cache_dir)
    materialize(_snapshot(config, cache_dir, local_files_only=local_files_only), flat)
    return TextEmbedding(
        config.model,
        cache_dir=str(cache_dir),
        specific_model_path=str(flat),
        local_files_only=True,
    )


def fetch_model(config: EmbeddingConfig, cache_dir: Path) -> None:
    """Télécharge les poids dans le cache (commande réseau, lancée explicitement)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    _load(config, cache_dir, local_files_only=False)


class FastembedEmbedder:
    def __init__(
        self, config: EmbeddingConfig, cache_dir: Path | None = None, *, model=None
    ):
        self.model = config.model
        self.dimension = config.dimension
        self._config = config
        if model is None:
            if cache_dir is None:
                raise EmbeddingError(
                    f"poids de {config.model} : dossier du cache non indiqué (EMBEDDING_CACHE_DIR)"
                )
            try:
                model = _load(config, cache_dir, local_files_only=True)
            except Exception as exc:
                raise EmbeddingError(
                    f"poids de {config.model} introuvables dans {cache_dir} : lancer "
                    "`uv run python -m cdg.cli fetch-embedding-model` (téléchargement)"
                ) from exc
        self._model = model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [list(map(float, v)) for v in self._model.embed(texts)]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise EmbeddingError(
                    f"{self.model} : vecteur de dimension {len(vector)}, attendu {self.dimension}"
                )
        return vectors

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self._config.passage_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._config.query_prefix + text])[0]

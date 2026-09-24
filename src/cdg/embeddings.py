"""Embedding local (fastembed, ONNX) : préfixes e5, dimension contrôlée.

À l'exécution, `local_files_only=True` : jamais de téléchargement implicite. Les poids
se récupèrent une fois par `cdg.cli fetch-embedding-model`, dans EMBEDDING_CACHE_DIR.
"""

from pathlib import Path

from cdg.config import EmbeddingConfig


class EmbeddingError(Exception):
    """Poids absents, ou vecteur d'une dimension inattendue."""


def _load(config: EmbeddingConfig, cache_dir: Path, *, local_files_only: bool):
    from fastembed import TextEmbedding

    return TextEmbedding(config.model, cache_dir=str(cache_dir), local_files_only=local_files_only)


def fetch_model(config: EmbeddingConfig, cache_dir: Path) -> None:
    """Télécharge les poids dans le cache (commande réseau, lancée explicitement)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    _load(config, cache_dir, local_files_only=False)


class FastembedEmbedder:
    def __init__(self, config: EmbeddingConfig, cache_dir: Path | None = None, *, model=None):
        self.model = config.model
        self.dimension = config.dimension
        self._config = config
        if model is None:
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

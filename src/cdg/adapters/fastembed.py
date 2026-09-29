"""Embedding local (fastembed, ONNX) : préfixes e5, dimension contrôlée.

À l'exécution, jamais de téléchargement : les poids se récupèrent une fois par
`cdg.cli fetch-embedding-model`, dans EMBEDDING_CACHE_DIR.

onnxruntime >= 1.30 exige que les poids externes (`model.onnx_data`) soient dans le même
dossier que `model.onnx` une fois les liens résolus ; le cache Hugging Face range chaque
fichier dans un sous-dossier de blobs différent. D'où une copie « à plat » faite de liens
physiques (aucun espace disque supplémentaire), chargée par `specific_model_path`.

Aucun appel réseau (J5) : onnxruntime, chargé par fastembed, envoie par défaut de la
télémétrie à Microsoft (mobile.events.data.microsoft.com), par son propre client HTTP. Sur
macOS et Linux, seule la variable ORT_DISABLE_TELEMETRY, lue quand onnxruntime s'initialise,
la coupe entièrement (ni envoi, ni identifiant d'appareil) ; disable_telemetry_events()
laisse partir l'événement ProcessInfo (onnxruntime 1.30, core/platform/posix/telemetry.cc).
Les deux sont appliqués avant tout import de fastembed ; un test le vérifie dans un bac à
sable qui tue le processus à sa première connexion sortante.
"""

import os
from pathlib import Path

from cdg.domain.config import EmbeddingConfig


class EmbeddingError(Exception):
    """Poids absents, ou vecteur d'une dimension inattendue."""


def _without_telemetry() -> None:
    """Coupe la télémétrie d'onnxruntime ; à appeler avant tout import de fastembed."""
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime  # type: ignore[import-untyped]  # ni types ni paquet de types

    onnxruntime.disable_telemetry_events()


def _snapshot(
    config: EmbeddingConfig, cache_dir: Path, *, local_files_only: bool
) -> Path:
    _without_telemetry()
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


def _load(
    config: EmbeddingConfig,
    cache_dir: Path,
    *,
    local_files_only: bool,
    threads: int | None = None,
):
    _without_telemetry()
    from fastembed import TextEmbedding

    flat = flat_dir(config, cache_dir)
    materialize(_snapshot(config, cache_dir, local_files_only=local_files_only), flat)
    # threads : fils de calcul d'onnxruntime, alignés sur la limite CPU du pod (ADR 005) ;
    # None, défaut de fastembed 0.8.1 : celui d'onnxruntime, un fil par cœur visible
    return TextEmbedding(
        config.model,
        cache_dir=str(cache_dir),
        specific_model_path=str(flat),
        local_files_only=True,
        threads=threads,
    )


def fetch_model(config: EmbeddingConfig, cache_dir: Path) -> None:
    """Télécharge les poids dans le cache (commande réseau, lancée explicitement)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    _load(config, cache_dir, local_files_only=False)


class FastembedEmbedder:
    def __init__(
        self,
        config: EmbeddingConfig,
        cache_dir: Path | None = None,
        *,
        model=None,
        threads: int | None = None,
        batch_size: int | None = None,
    ):
        self.model = config.model
        self.dimension = config.dimension
        self._config = config
        if threads is not None and threads < 1:
            raise ValueError(
                f"nombre de fils de calcul invalide : {threads} (entier ≥ 1)"
            )
        if batch_size is not None and batch_size < 1:
            raise ValueError(f"taille des lots invalide : {batch_size} (entier ≥ 1)")
        # textes embarqués à la fois ; None : défaut de fastembed 0.8.1 (256), dont le pic
        # mémoire dépasse la limite d'un pod à l'ingestion (mesure du 28/09, ADR 005)
        self._batch = {} if batch_size is None else {"batch_size": batch_size}
        if model is None:
            if cache_dir is None:
                raise EmbeddingError(
                    f"poids de {config.model} : dossier du cache non indiqué (EMBEDDING_CACHE_DIR)"
                )
            try:
                model = _load(config, cache_dir, local_files_only=True, threads=threads)
            except Exception as exc:
                raise EmbeddingError(
                    f"poids de {config.model} introuvables dans {cache_dir} : lancer "
                    "`uv run python -m cdg.cli fetch-embedding-model` (téléchargement)"
                ) from exc
        self._model = model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [list(map(float, v)) for v in self._model.embed(texts, **self._batch)]
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

"""Environnement : .env (sans écraser les variables exportées), variables obligatoires.

Les chaînes de connexion PostgreSQL sont dans `adapters/postgres/conninfo.py`.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
APP_ROLE = "app_role"  # créé par migrations/001_audit.sql


class SettingsError(Exception):
    """Variable d'environnement obligatoire absente ou vide."""


def load_env(path: Path | str = DEFAULT_ENV_PATH) -> bool:
    # override=False : une variable déjà exportée garde la priorité sur .env
    return load_dotenv(path, override=False)


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SettingsError(
            f"variable d'environnement absente ou vide : {name} (voir .env.example)"
        )
    return value


def embedding_cache_dir() -> Path:
    """Cache persistant des poids d'embedding ; un chemin relatif part de la racine du dépôt."""
    path = Path(require("EMBEDDING_CACHE_DIR"))
    return path if path.is_absolute() else DEFAULT_ENV_PATH.parent / path

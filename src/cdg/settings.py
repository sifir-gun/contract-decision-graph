"""Environnement : .env (sans écraser les variables exportées) et chaînes de connexion."""

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
APP_ROLE = "app_role"   # créé par migrations/001_audit.sql


class SettingsError(Exception):
    """Variable d'environnement obligatoire absente ou vide."""


def load_env(path: Path | str = DEFAULT_ENV_PATH) -> bool:
    # override=False : une variable déjà exportée garde la priorité sur .env
    return load_dotenv(path, override=False)


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SettingsError(f"variable d'environnement absente ou vide : {name} "
                            "(voir .env.example)")
    return value


def _conninfo(user: str, password_var: str) -> str:
    return make_conninfo(host=os.environ.get("POSTGRES_HOST") or "localhost",
                         port=_require("POSTGRES_PORT"), dbname=_require("POSTGRES_DB"),
                         user=user, password=_require(password_var))


def admin_conninfo() -> str:
    """Superutilisateur : setup-db (tables du checkpointer, droits) uniquement."""
    return _conninfo(_require("POSTGRES_USER"), "POSTGRES_PASSWORD")


def app_conninfo() -> str:
    """Rôle applicatif app_role : exécution du graphe et CLI."""
    return _conninfo(APP_ROLE, "APP_DB_PASSWORD")

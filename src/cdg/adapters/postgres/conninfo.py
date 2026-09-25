"""Chaînes de connexion PostgreSQL, lues dans l'environnement (.env)."""

import os

from psycopg.conninfo import make_conninfo

from cdg.settings import APP_ROLE, require


def _conninfo(user: str, password_var: str) -> str:
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST") or "localhost",
        port=require("POSTGRES_PORT"),
        dbname=require("POSTGRES_DB"),
        user=user,
        password=require(password_var),
    )


def admin_conninfo() -> str:
    """Superutilisateur : setup-db (tables du checkpointer, droits) uniquement."""
    return _conninfo(require("POSTGRES_USER"), "POSTGRES_PASSWORD")


def app_conninfo() -> str:
    """Rôle applicatif app_role : exécution du graphe et CLI."""
    return _conninfo(APP_ROLE, "APP_DB_PASSWORD")

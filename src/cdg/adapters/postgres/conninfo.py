"""Chaînes de connexion PostgreSQL : adresse dans l'environnement (.env), utilisateur
administrateur et mots de passe par `settings.require_secret` (fichier monté, sinon
variable d'environnement).

Jamais de négociation Kerberos (`gssencmode=disable`) : le projet n'utilise pas Kerberos,
et la roue arm64 de psycopg-binary embarque pour lui une copie d'OpenSSL 1.1.1k, dont le
chemin de code n'est ainsi jamais pris (journal, 01/10/2026)."""

import os

from psycopg.conninfo import make_conninfo

from cdg.settings import APP_ROLE, require, require_secret


def _conninfo(user: str, password_var: str) -> str:
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST") or "localhost",
        port=require("POSTGRES_PORT"),
        dbname=require("POSTGRES_DB"),
        user=user,
        password=require_secret(password_var),
        gssencmode="disable",
    )


def admin_conninfo() -> str:
    """Superutilisateur : setup-db (tables du checkpointer, droits) uniquement."""
    return _conninfo(require_secret("POSTGRES_USER"), "POSTGRES_PASSWORD")


def app_conninfo() -> str:
    """Rôle applicatif app_role : exécution du graphe et CLI."""
    return _conninfo(APP_ROLE, "APP_DB_PASSWORD")

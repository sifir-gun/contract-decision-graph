"""Migrations, appliquées par `setup-db` : sur une base vide (sans journal d'audit), toutes
dans l'ordre, 001 comprise ; sur une base existante, les idempotentes (002 et suivantes),
sans effet si elles sont déjà passées. Relancé, setup-db ne change rien.

Le rôle applicatif existe avant les migrations, qui lui donnent ses droits :
- dans le cluster, CloudNativePG le gère (chart cdg-postgres : rôle déclaré, mot de passe
  lu dans un Secret, haché par l'opérateur) ;
- avec Docker Compose, la 001 le crée s'il manque (docker/initdb/00_migrate.sh) ;
- ailleurs, `ensure_role` le crée s'il manque, avec un mot de passe déjà haché côté client
  (SCRAM-SHA-256, bibliothèque standard) : le mot de passe n'atteint jamais le serveur, et
  la journalisation des requêtes est suspendue le temps de la création.
"""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
FIRST = MIGRATIONS / "001_audit.sql"
IDEMPOTENT = sorted(
    p for p in MIGRATIONS.glob("0*.sql") if not p.name.startswith("001_")
)
SCRAM_ITERATIONS = 4096  # scram_iterations par défaut de PostgreSQL
SALT_BYTES = 16  # longueur du sel que tire libpq (PQencryptPasswordConn)


class MigrationError(Exception):
    """Amorçage ou migration impossible : setup-db s'arrête, avec la cause."""


def scram_sha256(password: str, *, salt: bytes | None = None) -> str:
    """Vérificateur SCRAM-SHA-256 au format de PostgreSQL (RFC 5802 et 7677) :
    `SCRAM-SHA-256$<itérations>:<sel>$<StoredKey>:<ServerKey>`. PostgreSQL applique SASLprep
    au mot de passe : sans effet sur l'ASCII imprimable, seul admis ici."""
    if not (password.isascii() and password.isprintable() and password):
        raise MigrationError(
            "mot de passe du rôle hors ASCII imprimable : SASLprep n'est pas appliqué, "
            "le vérificateur ne serait pas celui de PostgreSQL"
        )
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def encode(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    return (
        f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encode(salt)}"
        f"${encode(stored_key)}:{encode(server_key)}"
    )


def ensure_role(
    conn: psycopg.Connection, role: str, password: Callable[[], str]
) -> bool:
    """Crée le rôle `role` (LOGIN) s'il n'existe pas ; rend True s'il vient d'être créé.
    Le mot de passe n'est demandé que pour une création, et n'est envoyé que haché."""
    exists = conn.execute(
        "SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = %s)", (role,)
    ).fetchone()
    conn.commit()  # fin de la lecture : la création a sa propre transaction
    if exists is not None and exists[0]:
        return False
    verifier = scram_sha256(password())
    with conn.transaction():
        # ni la requête, ni une éventuelle erreur ne passent dans le journal de PostgreSQL
        conn.execute("SET LOCAL log_statement = 'none'")
        conn.execute("SET LOCAL log_min_error_statement = 'panic'")
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(verifier)
            )
        )
    return True


def ensure_role_at(admin_conninfo: str, role: str, password: Callable[[], str]) -> bool:
    """`ensure_role` sur une connexion administrateur ouverte pour l'occasion."""
    with psycopg.connect(admin_conninfo) as conn:
        return ensure_role(conn, role, password)


def is_empty(conn: psycopg.Connection) -> bool:
    """Base vide : la 001 n'y est pas passée (aucun journal d'audit)."""
    row = conn.execute("SELECT to_regclass('public.audit_decisions')").fetchone()
    return row is None or row[0] is None


def apply(admin_conninfo: str) -> list[str]:
    """Migrations manquantes, dans l'ordre ; rend leurs noms. Le rôle applicatif doit
    exister (voir en tête du module)."""
    applied = []
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        if is_empty(conn):
            conn.execute(FIRST.read_text(encoding="utf-8"))
            applied.append(FIRST.name)
        for migration in IDEMPOTENT:  # plusieurs commandes par fichier, sans paramètre
            conn.execute(migration.read_text(encoding="utf-8"))
            applied.append(migration.name)
    return applied

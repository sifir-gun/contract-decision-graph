"""Migrations idempotentes (002 et suivantes), appliquées par `setup-db` sur une base
existante. La 001 exige la variable psql `app_password` : seul l'init Docker l'applique, sur
un volume vide (`docker/initdb/00_migrate.sh`, qui applique aussi toutes les suivantes)."""

from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
IDEMPOTENT = sorted(
    p for p in MIGRATIONS.glob("0*.sql") if not p.name.startswith("001_")
)


def apply(admin_conninfo: str) -> list[str]:
    """Applique, dans l'ordre, les migrations idempotentes ; rend leurs noms."""
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        for migration in IDEMPOTENT:  # plusieurs commandes par fichier, sans paramètre
            conn.execute(migration.read_text(encoding="utf-8"))
    return [m.name for m in IDEMPOTENT]

"""Connexions d'app_role à PostgreSQL : un pool par processus (psycopg-pool), partagé par
le checkpointer, le journal d'audit, la recherche, les verrous de contrat et la sonde de
disponibilité (décision du 28/09, ADR 005).

- Réglages qu'exige `PostgresSaver` (langgraph-checkpoint-postgres) : autocommit, lignes
  en dictionnaires, pas de requêtes préparées côté serveur. Les autres usagers ouvrent
  leurs transactions eux-mêmes et lisent leurs lignes en tuples.
- Chaque connexion est vérifiée avant d'être prêtée (`check`) : après un redémarrage ou
  une bascule de la base, une connexion morte n'est jamais rendue à un usager.
- Au retour de chaque connexion, `pg_advisory_unlock_all()` : un verrou consultatif de
  session ne suit jamais une connexion rendue au pool, même si son usager a échoué.
- Pool épuisé : attente bornée (`timeout`), puis `ConnectionsExhausted`, jamais une
  attente sans fin.

Les commandes d'administration (migrations, ingestion) et les tests passent une chaîne de
connexion : une connexion directe, aux mêmes réglages, ouverte le temps de l'opération.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from cdg.ports.connections import ConnectionsExhausted

# taille par défaut : voir le budget de connexions de l'ADR 005 (mesure du 28/09)
DEFAULT_SIZE = 10
KWARGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}

# le pool du processus : connexions à lignes en dictionnaires, comme l'exige PostgresSaver
Pool = ConnectionPool[psycopg.Connection[DictRow]]
# une chaîne de connexion (administration, tests) ou le pool du processus
Source = str | Pool


def release_session_locks(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SELECT pg_advisory_unlock_all()")


def open_pool(
    conninfo: str, *, max_size: int, timeout: float = 10.0, open: bool = True
) -> Pool:
    if max_size < 1:
        raise ValueError(f"taille du pool invalide : {max_size} (entier ≥ 1)")
    return ConnectionPool(
        conninfo,
        connection_class=psycopg.Connection[DictRow],
        kwargs=KWARGS,
        min_size=1,
        max_size=max_size,
        timeout=timeout,
        check=ConnectionPool.check_connection,
        reset=release_session_locks,
        name="cdg",
        open=open,
    )


@contextmanager
def connection(source: Source) -> Iterator[psycopg.Connection[DictRow]]:
    """Connexion empruntée au pool, ou ouverte directement depuis une chaîne."""
    if isinstance(source, str):
        with psycopg.connect(
            source, autocommit=True, prepare_threshold=0, row_factory=dict_row
        ) as conn:
            yield conn
        return
    try:
        with source.connection() as conn:
            yield conn
    except PoolTimeout as exc:
        raise ConnectionsExhausted() from exc

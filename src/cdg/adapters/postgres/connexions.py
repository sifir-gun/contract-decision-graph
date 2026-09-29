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

from collections.abc import Callable, Iterator
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
# une chaîne de connexion, ou la fonction qui la rend, relue à chaque nouvelle connexion :
# un mot de passe tourné (fichier de secret monté) vaut ensuite, sans redémarrage (ADR 005)
Conninfo = str | Callable[[], str]
# une chaîne ou sa fonction (administration, tests), ou le pool du processus
Source = Conninfo | Pool


def resolve(conninfo: Conninfo) -> str:
    """La chaîne de connexion à jour : relue si c'est une fonction."""
    return conninfo() if callable(conninfo) else conninfo


def release_session_locks(conn: psycopg.Connection[Any]) -> None:
    conn.execute("SELECT pg_advisory_unlock_all()")


def open_pool(
    conninfo: Conninfo, *, max_size: int, timeout: float = 10.0, open: bool = True
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


def ping(source: Source, timeout: float = 2.0) -> bool:
    """Base joignable : `SELECT 1` sur une connexion prêtée en `timeout` secondes au
    plus. Lève si la base ne répond pas (sonde de disponibilité)."""
    if not isinstance(source, ConnectionPool):
        with psycopg.connect(
            resolve(source), connect_timeout=int(timeout) or 1
        ) as conn:
            conn.execute("SELECT 1")
        return True
    with source.connection(timeout=timeout) as conn:
        conn.execute("SELECT 1")
    return True


@contextmanager
def connection(source: Source) -> Iterator[psycopg.Connection[DictRow]]:
    """Connexion empruntée au pool, ou ouverte directement depuis une chaîne (relue si
    c'est une fonction)."""
    if not isinstance(source, ConnectionPool):
        with psycopg.connect(
            resolve(source), autocommit=True, prepare_threshold=0, row_factory=dict_row
        ) as conn:
            yield conn
        return
    try:
        with source.connection() as conn:
            yield conn
    except PoolTimeout as exc:
        raise ConnectionsExhausted() from exc

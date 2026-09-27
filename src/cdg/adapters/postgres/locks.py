"""Verrous de contrat sur PostgreSQL (port `ContractLocks`) : un verrou consultatif de
session par contrat, pris sans attendre (`pg_try_advisory_lock`), sur une connexion dédiée
tenue le temps de l'opération.

- Plusieurs réplicas : un seul crée, tranche ou expire un contrat à la fois ; la seconde
  demande reçoit `ContractBusy` aussitôt.
- Un processus qui meurt ferme sa connexion : PostgreSQL relâche le verrou, et un autre
  réplica peut reprendre l'analyse interrompue.
- Une connexion par verrou : la session qui tient un verrou consultatif le reprendrait
  sans attendre (verrous réentrants, documentation de PostgreSQL 16) ; deux opérations
  du même processus ne partagent donc jamais une session.
- Pas de regroupeur de connexions en mode transaction (PgBouncer) entre l'application et
  la base : il ferait tenir le verrou par une session qui change sous l'application.
- Connexion empruntée au pool du processus : le verrou compte dans sa taille. Au retour,
  le pool relâche tout verrou de session qui resterait (`connexions.py`).
"""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from psycopg.rows import tuple_row

from cdg.adapters.postgres.connexions import Source, connection
from cdg.ports.locks import ContractBusy


def contract_lock_key(thread_id: str) -> int:
    """Clé du verrou d'un contrat : entier signé de 64 bits, calculé ici (pas par
    `hashtext`), dans un espace de noms distinct de celui du journal d'audit."""
    digest = hashlib.sha256(f"cdg.contrat:{thread_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PostgresContractLocks:
    """`source` : le pool du processus (ou une chaîne de connexion), obtenu à chaque
    verrou, comme les autres dépendances des opérations : une variable absente
    n'empêche pas de lire."""

    def __init__(self, source: Callable[[], Source]):
        self._source = source

    @contextmanager
    def hold(self, thread_id: str) -> Iterator[None]:
        key = contract_lock_key(thread_id)
        with connection(self._source()) as conn:
            cur = conn.cursor(row_factory=tuple_row)
            row = cur.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()
            if row is None or not row[0]:
                raise ContractBusy(thread_id)
            try:
                yield
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (key,))

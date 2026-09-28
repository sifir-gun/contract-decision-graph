"""Compteur de reprises sur PostgreSQL (port `ResumeCounter`) : une ligne par contrat dans
`contract_resumes` (migration 006), incrémentée en une seule instruction, atomique même
entre réplicas. Le compteur survit aux redémarrages ; `app_role` l'ajoute et l'incrémente,
sans droit de suppression.
"""

from collections.abc import Callable

from psycopg.rows import tuple_row

from cdg.adapters.postgres.connexions import Source, connection

_RECORD = """
    INSERT INTO contract_resumes (thread_id, resumes) VALUES (%s, 1)
    ON CONFLICT (thread_id) DO UPDATE
    SET resumes = contract_resumes.resumes + 1, last_resumed_at = now()
    RETURNING resumes
"""


class PostgresResumeCounter:
    """`source` : le pool du processus (ou une chaîne de connexion), obtenu à chaque
    reprise, comme pour les verrous."""

    def __init__(self, source: Callable[[], Source]):
        self._source = source

    def record(self, thread_id: str) -> int:
        with connection(self._source()) as conn:
            cur = conn.cursor(row_factory=tuple_row)
            row = cur.execute(_RECORD, (thread_id,)).fetchone()
        if row is None:  # RETURNING rend toujours la ligne insérée ou mise à jour
            raise RuntimeError(f"reprise de {thread_id} non comptée")
        return int(row[0])

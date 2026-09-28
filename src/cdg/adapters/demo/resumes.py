"""Compteur de reprises d'un seul processus (port `ResumeCounter`) : mode démonstration de
l'interface, et doublure des tests. En production, `adapters/postgres/resumes.py`."""

import threading
from collections import Counter


class LocalResumeCounter:
    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._guard = threading.Lock()

    def record(self, thread_id: str) -> int:
        with self._guard:
            self._counts[thread_id] += 1
            return self._counts[thread_id]

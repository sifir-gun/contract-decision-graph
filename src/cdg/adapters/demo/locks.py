"""Verrous de contrat d'un seul processus (port `ContractLocks`) : mode démonstration de
l'interface, et doublure des tests. En production, `adapters/postgres/locks.py`."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from cdg.ports.locks import ContractBusy


class LocalContractLocks:
    def __init__(self) -> None:
        self._held: set[str] = set()
        self._guard = threading.Lock()

    @contextmanager
    def hold(self, thread_id: str) -> Iterator[None]:
        with self._guard:
            if thread_id in self._held:
                raise ContractBusy(thread_id)
            self._held.add(thread_id)
        try:
            yield
        finally:
            with self._guard:
                self._held.discard(thread_id)

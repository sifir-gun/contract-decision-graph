"""Serveur de l'interface web (uvicorn), lancé par `cdg.cli web`.

Journaux : la configuration que lui passe la CLI (`adapters/journaux.py`), en texte ou
en JSON ; le journal d'accès n'en garde que la méthode, le chemin et le code. En-têtes de
mandataire ignorés (aucun mandataire devant une interface locale)."""

from typing import Any

import uvicorn
from fastapi import FastAPI


def serve(app: FastAPI, host: str, port: int, *, log_config: dict[str, Any]) -> None:
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=log_config,
        proxy_headers=False,
        server_header=False,
    )

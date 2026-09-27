"""Serveur de l'interface web (uvicorn), lancé par `cdg.cli web`.

Journal d'accès d'uvicorn : méthode, chemin et code de chaque requête, jamais leur corps ;
en-têtes de mandataire ignorés (aucun mandataire devant une interface locale)."""

import uvicorn
from fastapi import FastAPI


def serve(app: FastAPI, host: str, port: int) -> None:
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=False,
        server_header=False,
    )

"""Sondes de santé pour Kubernetes : une petite application à part, sur son propre port,
servie par un second serveur uvicorn (`server.py`). Aucune route de données, aucune
logique métier : chaque sonde lit un état que lui donne la racine de composition.

- `/sante/vie` : le processus répond (sonde de vie) ;
- `/sante/demarrage` : le modèle d'embedding est chargé (sonde de démarrage, qui laisse
  au modèle le temps de se charger) ;
- `/sante/pret` : démarré, base joignable, pas en cours d'arrêt (sonde de disponibilité).

GET et HEAD ; jamais en cache. Une vérification qui échoue ne rend que sa raison, jamais
son message (qui pourrait citer une chaîne de connexion) ; le journal n'en garde que le
type. Pas de liste d'hôtes admis : le kubelet appelle l'adresse du pod, et ces pages ne
disent rien d'autre que « oui » ou « non, pour telle raison ».
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)
METHODS = ["GET", "HEAD"]
HEADERS = {"cache-control": "no-store"}


@dataclass(frozen=True)
class Checks:
    """`started` : modèle d'embedding chargé ; `database` : base joignable (tous deux
    toujours vrais en démonstration) ; `draining` : ordre d'arrêt reçu."""

    started: Callable[[], bool]
    database: Callable[[], bool]
    draining: Callable[[], bool]


def _passes(name: str, check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    # une sonde qui échoue, quelle qu'en soit la cause, rend « pas prêt » et sa raison
    except Exception as exc:  # noqa: BLE001
        log.warning("sonde de santé : %s en échec (%s)", name, type(exc).__name__)
        return False


def create_health_app(checks: Checks) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def answer(status: str, reasons: list[str]) -> JSONResponse:
        if reasons:
            refused = "pas_pret" if status == "pret" else status
            content: dict[str, object] = {"statut": refused, "raisons": reasons}
            return JSONResponse(content, status_code=503, headers=HEADERS)
        return JSONResponse({"statut": status}, headers=HEADERS)

    @app.api_route("/sante/vie", methods=METHODS)
    def alive() -> JSONResponse:
        return answer("vivant", [])

    @app.api_route("/sante/demarrage", methods=METHODS)
    def started() -> JSONResponse:
        loaded = _passes("démarrage", checks.started)
        return answer(
            "demarre" if loaded else "demarrage",
            [] if loaded else ["modele non charge"],
        )

    @app.api_route("/sante/pret", methods=METHODS)
    def ready() -> JSONResponse:
        reasons = []
        if not _passes("démarrage", checks.started):
            reasons.append("modele non charge")
        if not _passes("base", checks.database):
            reasons.append("base injoignable")
        if checks.draining():
            reasons.append("arret en cours")
        return answer("pret", reasons)

    return app

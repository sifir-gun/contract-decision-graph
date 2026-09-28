"""Serveurs uvicorn de l'interface web, lancés par `cdg.cli web` : l'interface elle-même
et, sur demande, les sondes de santé (`sante.py`) sur un port à part.

- Les sondes tournent dans un thread à elles, avec leur propre boucle : hors du thread
  principal, uvicorn n'installe pas de gestionnaire de signaux (`Server.capture_signals`,
  uvicorn 0.54). Seul le serveur de l'interface reçoit l'ordre d'arrêt ; les sondes
  s'arrêtent après lui.
- Aucune route de l'interface sur le port des sondes, aucune sonde sur celui de
  l'interface : deux applications distinctes.
- Journaux : la configuration que passe la CLI (`adapters/journaux.py`) ; pas de journal
  d'accès pour les sondes, appelées toutes les quelques secondes.
- En-têtes de mandataire ignorés (aucun mandataire de confiance devant l'interface) ;
  pas de bannière de serveur.
- Arrêt propre : à l'ordre d'arrêt (SIGTERM, SIGINT), `on_exit` lève le drapeau d'arrêt
  (sonde de disponibilité à 503, modifications refusées) ; uvicorn ferme ses sockets
  d'écoute et laisse finir les requêtes en cours pendant `grace_seconds`, puis annule
  celles qui restent (uvicorn 0.54, `Server.shutdown`).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Any

import uvicorn


class DrainingServer(uvicorn.Server):
    """Serveur de l'interface : l'ordre d'arrêt lève d'abord le drapeau d'arrêt."""

    def __init__(self, config: uvicorn.Config, on_exit: Callable[[], None]):
        super().__init__(config)
        self._on_exit = on_exit

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._on_exit()
        super().handle_exit(sig, frame)


@dataclass(frozen=True)
class Probes:
    app: Any  # application ASGI des sondes
    host: str
    port: int


def servers(
    app: Any,
    host: str,
    port: int,
    *,
    log_config: dict[str, Any],
    probes: Probes | None = None,
    on_exit: Callable[[], None] = lambda: None,
    grace_seconds: int | None = None,
) -> tuple[uvicorn.Server, uvicorn.Server | None]:
    main = DrainingServer(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=log_config,
            proxy_headers=False,
            server_header=False,
            timeout_graceful_shutdown=grace_seconds,
        ),
        on_exit,
    )
    if probes is None:
        return main, None
    health = uvicorn.Server(
        uvicorn.Config(
            probes.app,
            host=probes.host,
            port=probes.port,
            log_config=log_config,
            access_log=False,
            proxy_headers=False,
            server_header=False,
            lifespan="off",
        )
    )
    return main, health


def run(main: uvicorn.Server, health: uvicorn.Server | None) -> None:
    """Les sondes d'abord, dans leur thread ; l'interface ensuite ; les sondes s'arrêtent
    quand l'interface a fini, arrêt propre compris."""
    thread = None
    if health is not None:
        thread = threading.Thread(target=health.run, name="sondes", daemon=True)
        thread.start()
    try:
        main.run()
    finally:
        if health is not None and thread is not None:
            health.should_exit = True
            thread.join(10)


def serve(
    app: Any,
    host: str,
    port: int,
    *,
    log_config: dict[str, Any],
    probes: Probes | None = None,
    on_exit: Callable[[], None],
    grace_seconds: int,
) -> None:
    run(
        *servers(
            app,
            host,
            port,
            log_config=log_config,
            probes=probes,
            on_exit=on_exit,
            grace_seconds=grace_seconds,
        )
    )

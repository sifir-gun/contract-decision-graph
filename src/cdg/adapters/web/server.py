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
"""

import threading
from dataclasses import dataclass
from typing import Any

import uvicorn


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
) -> tuple[uvicorn.Server, uvicorn.Server | None]:
    main = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=log_config,
            proxy_headers=False,
            server_header=False,
        )
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
) -> None:
    run(*servers(app, host, port, log_config=log_config, probes=probes))

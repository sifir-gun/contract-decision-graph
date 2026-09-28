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
- Ports liés avant tout lancement : uvicorn 0.54 écrit « Application startup complete »
  avant d'ouvrir son port, puis sort en code 3 s'il est pris (`Server._serve`,
  `Server.startup`) ; une relance sur un port occupé laissait croire que l'interface
  avait démarré, et l'ancienne instance restait seule à servir (28/09). Un port pris lève
  `PortBusy` avant tout message ; l'interface ne s'annonce (`on_started`) qu'une fois son
  port à l'écoute.
"""

import errno
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Any

import uvicorn


class PortBusy(Exception):
    """Port d'écoute déjà pris : rien n'a démarré."""


def bind(host: str, port: int, *, option: str) -> list[socket.socket]:
    """Sockets d'écoute liées comme les lie asyncio (`loop.create_server`, Python 3.12) :
    une par adresse résolue, SO_REUSEADDR, IPv6 seul sur une socket IPv6, une adresse
    absente de la machine ignorée (IPv6 désactivé). `listen` vient au démarrage du
    serveur. `option` : l'option de la CLI qui règle ce port, citée dans l'erreur."""
    resolved = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )
    sockets: list[socket.socket] = []
    try:
        for family, kind, proto, _, address in set(resolved):
            sock = socket.socket(family, kind, proto)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                sock.bind(address)
            except OSError as exc:
                if exc.errno != errno.EADDRNOTAVAIL:
                    raise
                sockets.pop().close()
        if not sockets:
            raise OSError(f"aucune adresse de {host} disponible sur cette machine")
    except OSError as exc:
        close(sockets)
        if exc.errno == errno.EADDRINUSE:
            raise PortBusy(
                f"port {port} déjà utilisé sur {host} : rien n'a démarré. Arrêter "
                f"l'instance qui l'occupe, ou choisir un autre port ({option})"
            ) from exc
        raise
    return sockets


def close(sockets: list[socket.socket] | None) -> None:
    for sock in sockets or []:
        sock.close()


class DrainingServer(uvicorn.Server):
    """Serveur de l'interface : annoncé une fois son port à l'écoute ; l'ordre d'arrêt
    lève d'abord le drapeau d'arrêt."""

    def __init__(
        self,
        config: uvicorn.Config,
        on_exit: Callable[[], None],
        on_started: Callable[[], None],
    ):
        super().__init__(config)
        self._on_exit = on_exit
        self._on_started = on_started

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:  # port à l'écoute, application démarrée
            self._on_started()

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
    on_started: Callable[[], None] = lambda: None,
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
        on_started,
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
    """Les ports d'abord, tous liés avant tout message ; puis les sondes, dans leur
    thread ; l'interface ensuite ; les sondes s'arrêtent quand l'interface a fini, arrêt
    propre compris."""
    sockets = bind(main.config.host, main.config.port, option="--port")
    try:
        probe_sockets = (
            None
            if health is None
            else bind(health.config.host, health.config.port, option="--port-sante")
        )
    except Exception:
        close(sockets)
        raise
    thread = None
    if health is not None:
        thread = threading.Thread(
            target=health.run,
            kwargs={"sockets": probe_sockets},
            name="sondes",
            daemon=True,
        )
        thread.start()
    try:
        main.run(sockets=sockets)
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
    on_started: Callable[[], None],
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
            on_started=on_started,
        )
    )

"""Journaux du processus : en texte sur le poste (défaut), en JSON dans Kubernetes
(`--journaux json` ou `CDG_JOURNAUX=json`), sur la sortie standard ; sur la sortie
d'erreur pour le serveur MCP, dont la sortie standard est réservée au protocole.

Jamais le texte d'un contrat ni un secret :
- une exception n'y laisse que son type et les lignes de code traversées, jamais son
  message, qui pourrait citer une donnée reçue (Starlette relance l'exception après la
  page d'erreur, et uvicorn la journalise) ;
- le journal d'accès d'uvicorn : la méthode, le chemin et le code ; ni le corps, ni
  l'adresse du client ;
- les bibliothèques ne passent qu'à partir des avertissements (racine en WARNING) : ni
  requêtes ni réponses détaillées des clients HTTP.

La configuration est un dictionnaire de `logging.config` : la CLI l'applique, et la passe
au serveur web, qui la donne à uvicorn.
"""

import json
import logging
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

ACCESS = "uvicorn.access"
FORMATS = ("texte", "json")

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _exception(record: logging.LogRecord) -> dict[str, Any] | None:
    """Type et pile de l'exception, sans son message."""
    if not record.exc_info or record.exc_info[0] is None:
        return None
    kind, _, trace = record.exc_info
    frames = [
        f"{frame.filename}:{frame.lineno} {frame.name}"
        for frame in traceback.extract_tb(trace)
    ]
    return {"type": f"{kind.__module__}.{kind.__qualname__}", "pile": frames}


def _access(record: logging.LogRecord) -> dict[str, Any] | None:
    """Méthode, chemin et code d'une ligne du journal d'accès d'uvicorn (arguments :
    adresse du client, méthode, chemin, version HTTP, code)."""
    if record.name != ACCESS or not isinstance(record.args, tuple):
        return None
    if len(record.args) != 5:
        return None
    _, method, path, _, status = record.args
    return {"methode": method, "chemin": path, "statut": status}


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    """Champs d'un événement du journal des accès (`extra={"acces": {...}}`), déjà
    filtrés par liste blanche à leur source (`adapters/web/acces.py`)."""
    fields = getattr(record, "acces", None)
    return dict(fields) if isinstance(fields, dict) else {}


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par entrée : horodatage UTC, niveau, journal, message."""

    def format(self, record: logging.LogRecord) -> str:
        created = datetime.fromtimestamp(record.created, UTC)
        entry: dict[str, Any] = {
            "horodatage": created.isoformat(timespec="milliseconds"),
            "niveau": record.levelname,
            "journal": record.name,
        }
        access = _access(record)
        if access is not None:
            entry |= {"message": "requête", **access}
        else:
            entry["message"] = record.getMessage().strip()
        entry |= _fields(record)
        exception = _exception(record)
        if exception is not None:
            entry["exception"] = exception
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Une ligne lisible ; une exception n'y laisse que son type et sa pile."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s : %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        access = _access(record)
        message = (
            f"{access['methode']} {access['chemin']} {access['statut']}"
            if access is not None
            else record.getMessage().strip()
        )
        line = f"{self.formatTime(record)} {record.levelname} {record.name} : {message}"
        fields = _fields(record)
        if fields:
            line += " " + " ".join(f"{key}={value}" for key, value in fields.items())
        exception = _exception(record)
        if exception is not None:
            line += f"\n  {exception['type']}\n  " + "\n  ".join(exception["pile"])
        return line


FORMATTERS: dict[str, type[logging.Formatter]] = {
    "texte": TextFormatter,
    "json": JsonFormatter,
}


def config(fmt: str, stream: str = "ext://sys.stdout") -> dict[str, Any]:
    """Configuration de `logging.config.dictConfig`, pour la CLI et pour uvicorn ;
    `stream` : la sortie standard par défaut, la sortie d'erreur pour le serveur MCP,
    dont la sortie standard est réservée au protocole."""
    if fmt not in FORMATTERS:
        raise ValueError(f"format de journal inconnu : {fmt!r} (texte ou json)")
    handler = {
        "class": "logging.StreamHandler",
        "stream": stream,
        "formatter": fmt,
    }
    ours = {"handlers": ["sortie"], "level": "INFO", "propagate": False}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {fmt: {"()": f"{__name__}.{FORMATTERS[fmt].__name__}"}},
        "handlers": {"sortie": handler},
        "loggers": {
            "uvicorn": ours,
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": ours,
            "cdg": ours,
        },
        "root": {"handlers": ["sortie"], "level": "WARNING"},
    }

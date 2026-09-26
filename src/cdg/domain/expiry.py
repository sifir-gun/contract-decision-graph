"""expire : sélection des threads en attente au-delà du délai, décision système NO_GO.

Fonctions pures ; l'horloge est injectée. Jamais d'approbation automatique.
"""

import re
from datetime import datetime, timedelta
from typing import Any

EXPIRE_REVIEWER = "systeme:expire"
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> timedelta:
    """« 24h », « 30m », « 2d », « 90s » : entier positif ou nul, une unité."""
    match = re.fullmatch(r"(\d+)([smhd])", text)
    if not match:
        raise ValueError(f"durée invalide : {text!r} (attendu par exemple 24h, 30m, 2d)")
    return timedelta(**{_UNITS[match[2]]: int(match[1])})


def format_duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def expired(
    pending: list[tuple[str, datetime]], older_than: timedelta, now: datetime
) -> list[tuple[str, datetime]]:
    """Threads en attente depuis strictement plus que `older_than`."""
    if now.tzinfo is None:
        raise ValueError("horloge sans fuseau horaire : comparaison ambiguë")
    return [(thread_id, since) for thread_id, since in pending if now - since > older_than]


def system_decision(waited: timedelta, older_than: timedelta) -> dict[str, Any]:
    """Réponse de reprise : NO_GO système, motif timeout, tracée comme telle."""
    return {
        "decision": "NO_GO",
        "reviewer": EXPIRE_REVIEWER,
        "source": "systeme",
        "overrides_block": False,
        "reason": f"timeout : en attente depuis {format_duration(waited)}, "
        f"délai {format_duration(older_than)}",
    }

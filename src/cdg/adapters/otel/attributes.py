"""Liste blanche des attributs émis (ADR 008), par sorte de span et pour les métriques.

Toute autre clé est refusée (`ValueError`) : c'est une erreur de programmation, que les
tests attrapent. Une valeur de texte doit avoir la forme d'un identifiant (contrat, nœud,
modèle, décision, type d'exception, `sub`) ; une autre est remplacée par `REFUSED`, et
l'avertissement nomme la clé, jamais la valeur. Jamais d'attribut de contenu
(`gen_ai.input.messages`, `langfuse.observation.input`…) : Langfuse range tous les
attributs reçus dans ses métadonnées.
"""

import logging
import re
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)
REFUSED = "<refusé>"
_IDENTIFIER = re.compile(r"[\w.:/@+|=-]{1,200}")

ALLOWED: dict[str, frozenset[str]] = {
    "operation": frozenset(
        {
            "langfuse.trace.name",
            "langfuse.session.id",
            "langfuse.user.id",  # le `sub`, sur réglage seulement
            "cdg.operation",
            "cdg.canal",
            "cdg.etat",
            "cdg.decision.proposee",
            "cdg.decision.finale",
            "cdg.rejet",  # présence d'un motif de rejet, jamais le motif
            "cdg.appels_llm",
            "cdg.tokens.entree",
            "cdg.tokens.sortie",
            "cdg.cout_usd",
            "error.type",
        }
    ),
    "etape": frozenset(
        {
            "langfuse.observation.type",
            "cdg.noeud",
            "cdg.tentative",
            "cdg.domaine",
            "error.type",
        }
    ),
    "appel": frozenset(
        {
            "langfuse.observation.type",
            "gen_ai.operation.name",
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.cost",  # extension de Langfuse : coût total, calculé ici
            "cdg.noeud",
            "cdg.niveau",
            "cdg.tentative",
            "cdg.latence_ms",
            "error.type",
        }
    ),
    "metrique": frozenset(
        {
            "gen_ai.operation.name",
            "gen_ai.provider.name",
            "gen_ai.request.model",
            "gen_ai.token.type",
            "cdg.operation",
            "cdg.etat",
            "error.type",
        }
    ),
}


def identifier(value: str) -> bool:
    return _IDENTIFIER.fullmatch(value) is not None


def checked(kind: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Les attributs d'une sorte, contrôlés ; une valeur `None` est omise."""
    allowed = ALLOWED[kind]
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"attribut {key} hors de la liste blanche ({kind})")
        if value is None:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        elif isinstance(value, str):
            if identifier(value):
                out[key] = value
            else:
                log.warning(
                    "attribut %s : valeur qui n'a pas la forme d'un identifiant, "
                    "remplacée",
                    key,
                )
                out[key] = REFUSED
        else:
            raise TypeError(f"attribut {key} : type {type(value).__name__} refusé")
    return out

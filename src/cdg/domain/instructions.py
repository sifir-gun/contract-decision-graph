"""Détection, par motifs, d'instructions adressées à l'outil dans le texte d'un contrat.

Un contrat n'a pas à s'adresser à l'outil qui l'analyse : un passage qui parle à une IA, à
un modèle ou à un analyste, qui demande d'ignorer des règles ou de conclure une décision,
est une tentative d'instruction. Détectée (motifs de `input.instruction_patterns`), elle
devient un constat du contrat et impose la revue humaine ; une citation d'extraction prise
dans un tel passage est refusée. La détection se contourne par paraphrase : c'est une
couche de défense parmi d'autres (prompt, vérifications, règles, revue humaine).
"""

import re
from collections.abc import Sequence
from functools import cache

from cdg.domain.text import normalize

FINDING = "tentative d'instruction détectée"


@cache
def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def passages(text: str, patterns: Sequence[str]) -> list[str]:
    """Lignes du texte qui contiennent un motif d'instruction, normalisées, dans l'ordre et
    sans doublon."""
    compiled = _compiled(tuple(patterns))
    found: list[str] = []
    for line in text.splitlines():
        passage = normalize(line)
        if (
            passage
            and passage not in found
            and any(p.search(passage) for p in compiled)
        ):
            found.append(passage)
    return found


def findings(text: str, patterns: Sequence[str]) -> list[str]:
    """Constats du contrat : un par passage détecté, avec le passage."""
    return [f"{FINDING} : « {passage} »" for passage in passages(text, patterns)]

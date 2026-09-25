"""Contrôle de l'entrée d'un contrat, sans LLM : taille, langue, données personnelles
résiduelles, date d'analyse. Le texte est déjà masqué par `orchestrator.run_contract`."""

import re
from datetime import date

from cdg.domain.config import InputConfig
from cdg.domain.masking import residual_pii
from cdg.domain.numeric import rounded

# mots-outils propres au français (ni « a » ni « on », communs à l'anglais)
FRENCH_STOPWORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "des",
        "du",
        "de",
        "et",
        "est",
        "en",
        "un",
        "une",
        "pour",
        "par",
        "dans",
        "sur",
        "au",
        "aux",
        "que",
        "qui",
        "ne",
        "pas",
        "ce",
        "cette",
        "ces",
        "son",
        "sa",
        "ses",
        "leur",
        "leurs",
        "sont",
        "être",
        "avec",
        "ou",
        "il",
        "elle",
        "ils",
        "elles",
        "nous",
        "vous",
        "se",
        "lui",
        "dont",
        "où",
        "tout",
        "toute",
        "tous",
        "toutes",
        "entre",
        "sans",
        "sous",
        "selon",
        "après",
        "avant",
        "chaque",
        "ainsi",
    ]
)
_WORD = re.compile(r"[a-zàâäçéèêëîïôöûùüÿœæ]+")


def french_ratio(text: str) -> tuple[int, float]:
    words = _WORD.findall(text.lower())
    if not words:
        return 0, 0.0
    return len(words), sum(w in FRENCH_STOPWORDS for w in words) / len(words)


def rejection(text: str, analysis_date: object, limits: InputConfig) -> str | None:
    """Motif de rejet de l'entrée, ou None si elle est recevable."""
    if not text.strip():
        return "texte du contrat vide"
    if len(text) > limits.max_chars:
        return f"texte trop long : {len(text)} caractères, maximum {limits.max_chars}"
    words, ratio = french_ratio(text)
    if words < limits.min_words:
        return (
            f"texte trop court pour vérifier la langue : {words} mots, minimum {limits.min_words}"
        )
    if rounded(ratio) < rounded(limits.min_french_ratio):
        return (
            f"langue non reconnue comme français : part de mots-outils "
            f"{rounded(ratio)}, minimum {limits.min_french_ratio}"
        )
    residual = residual_pii(text)
    if residual:
        return "texte non masqué : " + ", ".join(residual)
    # date à laquelle les versions des textes sont jugées : jamais implicite
    if not isinstance(analysis_date, date):
        return "date d'analyse absente"
    return None

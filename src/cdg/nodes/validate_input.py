"""validate_input : contrôle de l'entrée, écrit `route` (extract_clauses ou reject).

Le texte reçu est déjà masqué par `orchestrator.run_contract` ; ce nœud vérifie
taille, langue et absence de données personnelles résiduelles.
"""

import re

from cdg.config import DecisionConfig
from cdg.masking import residual_pii
from cdg.numeric import rounded
from cdg.state import ContractState

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


def _reject(reason: str) -> dict:
    return {"route": "reject", "reject_reason": reason}


def validate_input(state: ContractState, decision_config: DecisionConfig) -> dict:
    text = state.get("raw_text", "")
    limits = decision_config.input
    if not text.strip():
        return _reject("texte du contrat vide")
    if len(text) > limits.max_chars:
        return _reject(f"texte trop long : {len(text)} caractères, maximum {limits.max_chars}")
    words, ratio = french_ratio(text)
    if words < limits.min_words:
        return _reject(
            f"texte trop court pour vérifier la langue : {words} mots, minimum {limits.min_words}"
        )
    if rounded(ratio) < rounded(limits.min_french_ratio):
        return _reject(
            f"langue non reconnue comme français : part de mots-outils "
            f"{rounded(ratio)}, minimum {limits.min_french_ratio}"
        )
    residual = residual_pii(text)
    if residual:
        return _reject("texte non masqué : " + ", ".join(residual))
    return {"route": "extract_clauses", "extraction_attempts": 0}

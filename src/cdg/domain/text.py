"""Forme de comparaison des textes : typographie unifiée, espaces réduits.

Partagée par la vérification de l'extraction (citations, termes d'absence) et par la
détection d'instructions dans le contrat.
"""

import re
import unicodedata

# typographie équivalente : apostrophes, guillemets, tirets, espaces insécables
_TYPOGRAPHY = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": "-",
        "‑": "-",
    }
)


def normalize(text: str) -> str:
    """NFKC, typographie unifiée, espaces réduits ; la casse est conservée."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    return re.sub(r"\s+", " ", text).strip()


def folded(text: str) -> str:
    """Forme de comparaison des termes : normalisée, sans casse."""
    return normalize(text).casefold()

"""Vérification de l'extraction, par code et sans LLM.

Chaque citation d'une clause présente doit figurer mot pour mot dans le texte masqué,
après normalisation ; chaque type des `REQUIRED_KINDS` est rendu une fois et une seule ;
la catégorie n'est donnée que pour les types qui l'exigent. Sinon : nouvel essai avec
retour ciblé, puis ESCALADE après le dernier.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from cdg.domain.models import (
    CATEGORY_KINDS,
    KIND_CATEGORIES,
    REQUIRED_KINDS,
    Clause,
    category_required,
)

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


def problems_of(raw_text: str, clauses: list[Clause]) -> list[str]:
    text = normalize(raw_text)
    counts = Counter(c.kind for c in clauses)
    problems = [f"clause manquante: {k}" for k in REQUIRED_KINDS if counts[k] == 0]
    problems += [f"clause en double: {k}" for k in counts if counts[k] > 1]
    problems += [f"type de clause inconnu: {k}" for k in counts if k not in REQUIRED_KINDS]
    problems += [
        f"catégorie manquante: {c.kind}"
        for c in clauses
        if category_required(c) and c.category is None
    ]
    problems += [
        f"catégorie invalide: {c.kind}"
        for c in clauses
        if c.kind in CATEGORY_KINDS
        and c.category is not None
        and c.category not in KIND_CATEGORIES[c.kind]
    ]
    problems += [
        f"catégorie inattendue: {c.kind}"
        for c in clauses
        if c.kind not in CATEGORY_KINDS and c.category is not None
    ]
    problems += [
        f"citation introuvable: {c.kind}"
        for c in clauses
        if c.present and normalize(c.quote) not in text
    ]
    return problems


@dataclass(frozen=True)
class ExtractionCheck:
    outcome: Literal["verified", "retry", "escalate"]
    problems: list[str]
    failure_report: dict[str, Any] | None = None  # escalate seulement


def check_extraction(
    raw_text: str, clauses: list[Clause], attempts: int, max_attempts: int
) -> ExtractionCheck:
    """`attempts` : essais d'extraction déjà faits ; au-delà de `max_attempts`, ESCALADE."""
    problems = problems_of(raw_text, clauses)
    if not problems:
        return ExtractionCheck(outcome="verified", problems=[])
    if attempts < max_attempts:
        return ExtractionCheck(outcome="retry", problems=problems)
    report = {"stage": "extraction", "attempts": attempts, "problems": problems}
    return ExtractionCheck(outcome="escalate", problems=problems, failure_report=report)

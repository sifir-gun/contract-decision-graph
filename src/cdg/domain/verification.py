"""Vérification de l'extraction, par code et sans LLM.

Chaque citation d'une clause présente doit figurer mot pour mot dans le texte masqué,
après normalisation ; chaque type des `REQUIRED_KINDS` est rendu une fois et une seule ;
la catégorie n'est donnée que pour les types qui l'exigent. Une clause déclarée absente
alors que le texte contient un terme qui l'évoque (`extraction.absence_terms`) est
redemandée : une absence ne laisse aucune citation à vérifier (attaque par omission,
série 4 du J4). Une clause chiffrée doit porter sa valeur, avec son unité, dans sa
citation ; une citation prise dans un passage détecté comme instruction
(`domain/instructions.py`) est refusée, et un terme d'absence n'y compte pas. Sinon :
nouvel essai avec retour ciblé, puis ESCALADE après le dernier.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from cdg.domain import instructions
from cdg.domain.models import (
    CATEGORY_KINDS,
    KIND_CATEGORIES,
    REQUIRED_KINDS,
    Clause,
    category_required,
)
from cdg.domain.numeric import rounded
from cdg.domain.text import folded, normalize

# unité de la valeur de chaque type chiffré (« Règles par domaine » de la spec)
VALUE_UNITS: dict[str, str] = {
    "responsabilite_acheteur": "%",
    "responsabilite_fournisseur": "%",
    "revision_prix": "%",
    "penalites_execution": "%",
    "delai_paiement": "jours",
    "duree_engagement": "mois",
    "preavis_resiliation": "mois",
}
_QUANTITY = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|pour\s?cents?|jours?|mois)(?!\w)")
_UNIT = {"%": "%", "jour": "jours", "jours": "jours", "mois": "mois"}


def quantities(quote: str) -> set[tuple[float, str]]:
    """Quantités d'une citation avec leur unité : « 1,5 % » donne (1.5, "%")."""
    return {
        (rounded(float(number.replace(",", "."))), _UNIT.get(unit, "%"))
        for number, unit in _QUANTITY.findall(folded(quote))
    }


def value_mismatches(clauses: list[Clause]) -> list[str]:
    """Clauses chiffrées dont la valeur, avec son unité, n'apparaît pas dans la citation."""
    problems = []
    for c in clauses:
        unit = VALUE_UNITS.get(c.kind)
        if not c.present or c.value is None or unit is None:
            continue
        if (rounded(c.value), unit) not in quantities(c.quote):
            problems.append(
                f"valeur absente de la citation ({c.value:g} {unit}): {c.kind}"
            )
    return problems


def _occurrences(text: str, part: str) -> list[tuple[int, int]]:
    spans, start = [], text.find(part)
    while part and start != -1:
        spans.append((start, start + len(part)))
        start = text.find(part, start + 1)
    return spans


def quotes_from_instructions(
    raw_text: str, clauses: list[Clause], passages: Sequence[str]
) -> list[str]:
    """Clauses citées seulement dans un passage détecté comme instruction."""
    text = normalize(raw_text)
    detected = [span for p in passages for span in _occurrences(text, p)]
    problems = []
    for c in clauses:
        found = _occurrences(text, normalize(c.quote)) if c.present else []
        if found and all(
            any(start < end_p and start_p < end for start_p, end_p in detected)
            for start, end in found
        ):
            problems.append(
                f"citation prise dans un passage détecté comme instruction: {c.kind}"
            )
    return problems


def mentioned_absences(
    raw_text: str,
    clauses: list[Clause],
    absence_terms: Mapping[str, Sequence[str]],
    passages: Sequence[str] = (),
) -> list[str]:
    """Clauses déclarées absentes alors que le texte, hors des passages détectés comme
    instruction, contient un terme qui les évoque : un retour ciblé ne renvoie jamais le
    modèle vers une consigne injectée."""
    kept = [normalize(line) for line in raw_text.splitlines()]
    text = folded(" ".join(line for line in kept if line not in passages))
    problems = []
    for c in clauses:
        if c.present:
            continue
        term = next(
            (t for t in absence_terms.get(c.kind, ()) if folded(t) in text), None
        )
        if term is not None:
            problems.append(
                f"clause déclarée absente, mais le contrat contient « {term} »: {c.kind}"
            )
    return problems


def problems_of(
    raw_text: str,
    clauses: list[Clause],
    *,
    absence_terms: Mapping[str, Sequence[str]],
    instruction_patterns: Sequence[str],
) -> list[str]:
    text = normalize(raw_text)
    passages = instructions.passages(raw_text, instruction_patterns)
    counts = Counter(c.kind for c in clauses)
    problems = [f"clause manquante: {k}" for k in REQUIRED_KINDS if counts[k] == 0]
    problems += [f"clause en double: {k}" for k in counts if counts[k] > 1]
    problems += [
        f"type de clause inconnu: {k}" for k in counts if k not in REQUIRED_KINDS
    ]
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
    problems += value_mismatches(clauses)
    problems += quotes_from_instructions(raw_text, clauses, passages)
    problems += mentioned_absences(raw_text, clauses, absence_terms, passages)
    return problems


@dataclass(frozen=True)
class ExtractionCheck:
    outcome: Literal["verified", "retry", "escalate"]
    problems: list[str]
    failure_report: dict[str, Any] | None = None  # escalate seulement


def check_extraction(
    raw_text: str,
    clauses: list[Clause],
    attempts: int,
    max_attempts: int,
    *,
    absence_terms: Mapping[str, Sequence[str]],
    instruction_patterns: Sequence[str],
) -> ExtractionCheck:
    """`attempts` : essais d'extraction déjà faits ; au-delà de `max_attempts`, ESCALADE."""
    problems = problems_of(
        raw_text,
        clauses,
        absence_terms=absence_terms,
        instruction_patterns=instruction_patterns,
    )
    if not problems:
        return ExtractionCheck(outcome="verified", problems=[])
    if attempts < max_attempts:
        return ExtractionCheck(outcome="retry", problems=problems)
    report = {"stage": "extraction", "attempts": attempts, "problems": problems}
    return ExtractionCheck(outcome="escalate", problems=problems, failure_report=report)

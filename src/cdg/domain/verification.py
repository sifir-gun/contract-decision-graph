"""Vérification de l'extraction, par code et sans LLM.

Chaque citation d'une clause présente doit figurer mot pour mot dans le texte masqué,
après normalisation ; chaque type des `REQUIRED_KINDS` est rendu une fois et une seule ;
la catégorie n'est donnée que pour les types qui l'exigent. Une clause déclarée absente
alors que le texte contient un terme qui l'évoque (`extraction.absence_terms`) est
redemandée : une absence ne laisse aucune citation à vérifier (attaque par omission,
série 4 du J4). Une clause chiffrée doit porter sa valeur, avec son unité, dans sa
citation : en chiffres (« 45 jours »), en chiffres entre parenthèses (« quarante-cinq (45)
jours ») ou seulement en lettres, de zéro à cent (J5) ; la catégorie d'une clause doit être
celle qu'évoque sa citation (`extraction.category_terms`, J5) ; une citation prise dans un
passage détecté comme instruction (`domain/instructions.py`) est refusée, et un terme d'absence
n'y compte pas. Sinon : nouvel essai avec retour ciblé, puis ESCALADE après le dernier.
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
# unité suivie d'une demie (« un an et demi ») : non lue, jamais lue en partie
_UNITS = r"(%|pour\s?cents?|jours?|mois|ans?|années?)(?!\w)(?!\s+et\s+demie?\b)"
# chiffres, seuls ou entre parenthèses après le nombre en lettres : « quarante-cinq (45)
# jours » ; le chiffre fait foi
_QUANTITY = re.compile(r"(?:\(\s*(\d+(?:[.,]\d+)?)\s*\)|(\d+(?:[.,]\d+)?))\s*" + _UNITS)
# unité de la règle et facteur de conversion : une durée en années est comptée en mois
# (J5) ; « pour cent » et ses variantes valent « % »
_UNIT: dict[str, tuple[str, int]] = {
    "%": ("%", 1),
    "jour": ("jours", 1),
    "jours": ("jours", 1),
    "mois": ("mois", 1),
    "an": ("mois", 12),
    "ans": ("mois", 12),
    "année": ("mois", 12),
    "années": ("mois", 12),
}
_PERCENT = ("%", 1)


def _quantity(number: float, unit: str) -> tuple[float, str]:
    name, factor = _UNIT.get(unit, _PERCENT)
    return rounded(number * factor), name


# nombres écrits seulement en lettres, de zéro à cent (au-delà : non lus, limite
# documentée), mots séparés par une espace après remplacement des traits d'union
_DIGITS = (
    "zéro",
    "un",
    "deux",
    "trois",
    "quatre",
    "cinq",
    "six",
    "sept",
    "huit",
    "neuf",
)
_TEENS = ("dix", "onze", "douze", "treize", "quatorze", "quinze", "seize")
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _below_twenty(n: int) -> list[str]:
    if n < 10:
        return [_DIGITS[n], "une"] if n == 1 else [_DIGITS[n]]
    if n < 17:
        return [_TEENS[n - 10]]
    return [f"dix {unit}" for unit in _below_twenty(n - 10)]


def _spellings(n: int) -> list[str]:
    """Écritures de n, de 0 à 100 : « quatre vingt dix », « vingt et un », « vingt un »."""
    if n < 20:
        return _below_twenty(n)
    if n == 100:
        return ["cent"]
    if n < 70:
        tens, rest = _TENS[n // 10], n % 10
    elif n < 80:
        tens, rest = "soixante", n - 60
    else:
        tens, rest = "quatre vingt", n - 80
    if rest == 0:
        return ["quatre vingts", "quatre vingt"] if n == 80 else [tens]
    tails = _below_twenty(rest)
    forms = [f"{tens} {tail}" for tail in tails]
    if rest in (1, 11):  # « et » d'usage, ou en variante (« quatre-vingt-et-un »)
        forms += [f"{tens} et {tail}" for tail in tails]
    return forms


_SPELLED: dict[str, int] = {form: n for n in range(101) for form in _spellings(n)}
_NUMBER_WORDS = {word for form in _SPELLED for word in form.split()} - {"et"}
_NUMBER_WORDS |= {"cents", "mille"}
_SPELLED_QUANTITY = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(form) for form in sorted(_SPELLED, key=len, reverse=True))
    + r")\s*"
    + _UNITS
)


def _extends_a_number(before: list[str]) -> bool:
    """Le nombre en lettres prolonge-t-il un autre nombre (« cent vingt », « trente et
    quarante ») ? Alors il n'est pas lu : jamais « vingt » dans « cent vingt »."""
    if before[-1:] == ["et"]:
        before = before[:-1]
    return bool(before) and before[-1] in _NUMBER_WORDS


def quantities(quote: str) -> set[tuple[float, str]]:
    """Quantités d'une citation avec leur unité : « 1,5 % » donne (1.5, "%") ;
    « quarante-cinq (45) jours » et « quarante-cinq jours » donnent (45.0, "jours") ;
    « trois ans » donne (36.0, "mois")."""
    text = folded(quote)
    found = {
        _quantity(float((in_parentheses or number).replace(",", ".")), unit)
        for in_parentheses, number, unit in _QUANTITY.findall(text)
    }
    words = re.sub(r"\s+", " ", text.replace("-", " "))
    for match in _SPELLED_QUANTITY.finditer(words):
        if not _extends_a_number(words[: match.start()].split()):
            found.add(_quantity(float(_SPELLED[match[1]]), match[2]))
    return found


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


def category_mismatches(
    clauses: list[Clause],
    category_terms: Mapping[str, Mapping[str, Sequence[str]]],
) -> list[str]:
    """Clauses dont la citation évoque une autre catégorie que celle rendue. Parmi les
    catégories dont un terme figure dans la citation, la plus spécifique, la première dans
    l'ordre de la configuration, l'emporte ; une citation qui n'en évoque aucune n'est pas
    contrôlée (J5, série 6 : « factures périodiques » lu comme une date de facture)."""
    problems = []
    for c in clauses:
        if not c.present or c.category is None:
            continue
        quote = folded(c.quote)
        evoked = next(
            (
                (category, term)
                for category, terms in category_terms.get(c.kind, {}).items()
                for term in terms
                if folded(term) in quote
            ),
            None,
        )
        if evoked is not None and evoked[0] != c.category:
            category, term = evoked
            problems.append(
                f"catégorie contredite par la citation (« {term} » : {category}): "
                f"{c.kind}"
            )
    return problems


def problems_of(
    raw_text: str,
    clauses: list[Clause],
    *,
    absence_terms: Mapping[str, Sequence[str]],
    category_terms: Mapping[str, Mapping[str, Sequence[str]]],
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
    problems += [  # J5, série 7 : une clause absente n'a pas de catégorie
        f"catégorie sur une clause absente: {c.kind}"
        for c in clauses
        if c.kind in CATEGORY_KINDS and not c.present and c.category is not None
    ]
    problems += [
        f"citation introuvable: {c.kind}"
        for c in clauses
        if c.present and normalize(c.quote) not in text
    ]
    problems += value_mismatches(clauses)
    problems += category_mismatches(clauses, category_terms)
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
    category_terms: Mapping[str, Mapping[str, Sequence[str]]],
    instruction_patterns: Sequence[str],
) -> ExtractionCheck:
    """`attempts` : essais d'extraction déjà faits ; au-delà de `max_attempts`, ESCALADE."""
    problems = problems_of(
        raw_text,
        clauses,
        absence_terms=absence_terms,
        category_terms=category_terms,
        instruction_patterns=instruction_patterns,
    )
    if not problems:
        return ExtractionCheck(outcome="verified", problems=[])
    if attempts < max_attempts:
        return ExtractionCheck(outcome="retry", problems=problems)
    report = {"stage": "extraction", "attempts": attempts, "problems": problems}
    return ExtractionCheck(outcome="escalate", problems=problems, failure_report=report)

"""Masquage des données personnelles d'un contrat, avant toute entrée dans le graphe.

Appliqué par `orchestrator.run_contract` : le texte original n'est jamais écrit en
base (checkpoints) ni envoyé à un fournisseur LLM. `validate_input` contrôle ensuite
qu'aucun motif ne subsiste (`residual_pii`).
"""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_PHONE = re.compile(r"(?<![\d+])(?:(?:\+|00)33[ .-]?|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)")
# SIRET (14 chiffres) avant SIREN (9) ; groupes séparés par espace ou point tolérés
_SIRET = re.compile(r"(?<!\d)\d{3}[ .]?\d{3}[ .]?\d{3}[ .]?\d{5}(?!\d)")
_SIREN = re.compile(r"(?<!\d)\d{3}[ .]?\d{3}[ .]?\d{3}(?!\d)")
# un montant, une durée ou un taux n'est pas un identifiant d'entreprise
_UNIT_AFTER = re.compile(
    r"\s*(?:€|euros?\b|EUR\b|%|mois\b|jours?\b|ans?\b|années?\b)", re.IGNORECASE
)


def _luhn(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        n = int(char) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _company_id(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group())
    followed_by_unit = _UNIT_AFTER.match(match.string, match.end()) is not None
    return _luhn(digits) and not followed_by_unit


# (type, motif, filtre éventuel), dans l'ordre d'application
_PATTERNS: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], bool] | None]] = [
    ("EMAIL", _EMAIL, None),
    ("IBAN", _IBAN, None),
    ("SIRET", _SIRET, _company_id),
    ("SIREN", _SIREN, _company_id),
    ("TELEPHONE", _PHONE, None),
]


@dataclass(frozen=True)
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def mask(text: str, parties: Sequence[str] = ()) -> MaskResult:
    counts: Counter[str] = Counter()
    for kind, pattern, keep in _PATTERNS:

        def replace(
            match: re.Match[str],
            kind: str = kind,
            keep: Callable[[re.Match[str]], bool] | None = keep,
        ) -> str:
            if keep is not None and not keep(match):
                return match.group()
            counts[kind] += 1
            return f"[{kind}]"

        text = pattern.sub(replace, text)
    # noms déclarés, du plus long au plus court, en mot entier, sans tenir compte de la casse
    numbered = {
        name: f"[PARTIE_{i}]" for i, name in enumerate(parties, start=1) if name
    }
    for name in sorted(numbered, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
        text, n = pattern.subn(numbered[name], text)
        if n:
            counts["PARTIE"] += n
    return MaskResult(text=text, counts=dict(counts))


def residual_pii(text: str) -> list[str]:
    """Types de données personnelles encore détectés (noms de parties exceptés)."""
    found = []
    for kind, pattern, keep in _PATTERNS:
        if any(keep is None or keep(m) for m in pattern.finditer(text)):
            found.append(kind)
    return found

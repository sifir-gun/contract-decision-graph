"""Arrondi unique : tout flottant comparé à un seuil ou sérialisé passe par `rounded`."""

import math

DECIMALS = 6  # constante de format, pas un réglage métier
GROUPING_FROM = 10_000  # constante de format : à partir de là, un entier est groupé


def rounded(x: float) -> float:
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"flottant non fini : {value!r}")
    return round(value, DECIMALS) + 0.0  # + 0.0 normalise -0.0 en 0.0


def shown(value: float) -> str:
    """Valeur pour un message ou une requête : un entier de 10 000 ou plus est groupé par
    milliers avec une espace (« 2 000 000 »), sinon la forme `:g` (« 45 », « 2.5 »),
    jamais la notation scientifique d'un montant (« 2e+06 »)."""
    if value >= GROUPING_FROM and value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:g}"

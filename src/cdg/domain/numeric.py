"""Arrondi unique : tout flottant comparé à un seuil ou sérialisé passe par `rounded`."""

import math

DECIMALS = 6  # constante de format, pas un réglage métier
GROUPING_FROM = 10_000  # constante de format : à partir de là, un nombre est groupé


def rounded(x: float) -> float:
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"flottant non fini : {value!r}")
    return round(value, DECIMALS) + 0.0  # + 0.0 normalise -0.0 en 0.0


def shown(value: float) -> str:
    """Valeur pour un message ou une requête, arrondie d'abord (`rounded`), puis écrite :
    à partir de 10 000, groupée par milliers avec une espace (« 2 000 000 »,
    « 10 000.5 »), jamais en notation scientifique (« 2e+06 ») ; en dessous, la forme
    `:g` (« 45 », « 2.5 »)."""
    value = rounded(value)
    if value < GROUPING_FROM:
        return f"{value:g}"
    fixed = f"{value:.{DECIMALS}f}".rstrip("0").rstrip(".")
    whole, _, decimals = fixed.partition(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return f"{grouped}.{decimals}" if decimals else grouped

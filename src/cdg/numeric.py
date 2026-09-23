"""Arrondi unique : tout flottant comparé à un seuil ou sérialisé passe par `rounded`."""

import math

DECIMALS = 6   # constante de format, pas un réglage métier


def rounded(x: float) -> float:
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"flottant non fini : {value!r}")
    return round(value, DECIMALS) + 0.0   # + 0.0 normalise -0.0 en 0.0

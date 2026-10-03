"""Détecteur de fuites de texte, partagé par les tests du serveur MCP et de
l'observabilité : fenêtres de six mots consécutifs d'un texte source cherchées dans une
sortie, telles qu'elles y paraîtraient en JSON."""

import json
import re
from collections.abc import Iterable

# une enveloppe de contenu non fiable (serveur MCP), en JSON (sauts de ligne échappés)
ENVELOPE = re.compile(
    r"<<<CONTENU-NON-FIABLE-([0-9a-f]+)>>>.*?<<<FIN-CONTENU-NON-FIABLE-\1>>>", re.DOTALL
)
WINDOW = 6  # mots consécutifs : une fuite partielle se voit, pas une coïncidence


def escaped(text: str) -> str:
    """Le texte tel qu'il paraît dans du JSON."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def outside(out: str) -> str:
    """Ce qui reste d'une réponse hors des enveloppes : délimité par les balises, pas
    par la liste qui les porte (ses métadonnées restent contrôlées)."""
    return ENVELOPE.sub("", out)


def leaks(source: str, out: str, allowed: Iterable[str] = ()) -> list[str]:
    """Fenêtres de six mots consécutifs du texte source trouvées dans la sortie, hors
    des textes écrits par le code (`allowed` : constats des règles, synthèse…), qui
    reprennent parfois quelques mots du contrat (« à 50 % du montant annuel »)."""
    for text in allowed:
        out = out.replace(escaped(text), "")
    found = set()
    for line in source.splitlines():
        words = line.split()
        for i in range(len(words) - WINDOW + 1):
            window = " ".join(words[i : i + WINDOW])
            if escaped(window) in out:
                found.add(window)
    return sorted(found)

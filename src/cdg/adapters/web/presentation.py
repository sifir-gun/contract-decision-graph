"""Présentation de l'interface web : libellés, surlignage des citations, lignes de
tableau. Aucune logique métier : le dossier vient du service des contrats, tel quel.

Le surlignage produit le seul HTML construit en Python : le texte du contrat et chaque
citation sont échappés d'abord, puis les citations sont cherchées dans le texte échappé et
entourées de balises fixes. Les gabarits n'emploient jamais le filtre « safe ».

L'adresse d'un contrat ne se forme qu'ici (`contract_path`), pour les liens des pages
comme pour les redirections.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote as percent_encode

from markupsafe import Markup, escape

CONTRACT_ACTIONS = ("decision", "rejeu")

DECISION_LABELS = {
    "GO": "GO",
    "GO_RESERVES": "GO avec réserves",
    "NO_GO": "NO GO",
    "ESCALADE": "Escalade vers un humain",
}
STATE_LABELS = {
    "en_attente": "En attente de revue",
    "termine": "Terminé",
    "rejete": "Rejeté à l'entrée",
    "en_cours": "En cours",
}
DOMAIN_LABELS = {
    "juridique": "Juridique",
    "financier": "Financier",
    "conformite": "Conformité",
    "operationnel": "Opérationnel",
}
RETRIEVAL_LABELS = {
    "OK": "références trouvées",
    "INSUFFISANT": "références insuffisantes",
}


def contract_path(thread_id: str, action: str = "") -> str:
    """Adresse d'un contrat dans l'interface : préfixe fixe, identifiant encodé comme un
    seul segment de chemin. « / », « \\ », « ? », « # », « : », blancs et fins de ligne
    sont encodés : rien de l'identifiant ne sort de son segment, et une redirection vers
    cette adresse reste sur l'interface (alertes CodeQL py/url-redirection, 27/09).
    `action` : la page du contrat, ou l'une de `CONTRACT_ACTIONS`."""
    if action and action not in CONTRACT_ACTIONS:
        raise ValueError(f"action inconnue pour un contrat : {action}")
    path = "/contrats/" + percent_encode(thread_id, safe="")
    return path + "/" + action if action else path


def _pattern(quote: str) -> re.Pattern[str] | None:
    """Citation échappée, dont chaque blanc accepte un retour à la ligne du contrat."""
    words = str(escape(quote)).split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(w) for w in words))


def highlight(text: str, quotes: Sequence[tuple[str, str]]) -> Markup:
    """Texte échappé, chaque citation trouvée surlignée et reliée à sa clause
    (`#clause-<type>`). Une citation introuvable (typographie différente) n'est pas
    surlignée : le tableau des clauses la montre quand même."""
    escaped = str(escape(text))
    spans: list[tuple[int, int, str]] = []
    for anchor, quote in quotes:
        pattern = _pattern(quote)
        match = pattern.search(escaped) if pattern else None
        if match is None:
            continue
        start, end = match.span()
        if any(start < e and s < end for s, e, _ in spans):
            continue  # citation qui en chevauche une autre : la première suffit
        spans.append((start, end, str(escape(anchor))))
    out, cursor = [], 0
    for start, end, anchor in sorted(spans):
        out.append(escaped[cursor:start])
        out.append(
            f'<a class="citation" id="citation-{anchor}" href="#clause-{anchor}">'
            f"<mark>{escaped[start:end]}</mark></a>"
        )
        cursor = end
    out.append(escaped[cursor:])
    return Markup("".join(out))


def quotes_of(clauses: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(c["kind"], c["quote"]) for c in clauses if c["present"] and c["quote"]]


def reference_rows(
    references: Mapping[str, Mapping[str, str] | None],
) -> list[dict[str, str | None]]:
    """Références retenues : texte et source, ou None si le corpus ne l'a plus."""
    return [
        {
            "reference": reference,
            "source": known["source"] if known else None,
            "texte": known["texte"] if known else None,
        }
        for reference, known in references.items()
    ]


def short_hash(value: str | None) -> str:
    return f"{value[:12]}…" if value else "—"

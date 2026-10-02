"""Saisie d'un contrat à analyser, commune aux portes qui reçoivent un texte : l'interface
web (formulaire) et le serveur MCP (arguments d'un outil). Une règle, deux portes :
contrat du jeu de démonstration ou texte fourni, parties à masquer, identifiant et date
d'analyse. La CLI lit un fichier et nomme son contrat d'après lui : elle ne passe pas par
ici.

Rien n'est masqué ici : le texte part tel quel au service, qui le masque avant le graphe
(`orchestrator.run_contract`). Les messages d'erreur ne citent jamais le texte du contrat
(un identifiant refusé, oui).
"""

import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from cdg.application import demo_set
from cdg.domain.identifiers import ContractIdError, check_contract_id

# caractères de contrôle hors tabulation et fins de ligne : pas du texte brut
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InputError(ValueError):
    """Saisie refusée : message montré à qui l'a faite (formulaire, outil MCP)."""


@dataclass(frozen=True)
class ContractInput:
    text: str
    parties: list[str]  # noms à masquer avant l'analyse
    base: str  # base de l'identifiant par défaut


def _names(parties: Sequence[str]) -> list[str]:
    return [p.strip() for p in parties if p.strip()]


def from_demo_set(
    contract: str, parties: Sequence[str], *, demo: bool
) -> ContractInput:
    """Contrat du jeu de démonstration. En démonstration, ses parties déclarées, dont
    dépend l'extraction simulée ; sinon les parties saisies, ou à défaut les déclarées."""
    _, contracts = demo_set.load()
    chosen = contracts.get(contract)
    if chosen is None:
        raise InputError("contrat inconnu du jeu de démonstration")
    declared = list(chosen.parties)
    given = _names(parties)
    return ContractInput(
        text=chosen.text(),
        parties=declared if demo else given or declared,
        base=chosen.id,
    )


def from_text(
    read: Callable[[], str], parties: Sequence[str], *, demo: bool
) -> ContractInput:
    """Texte fourni (collé, envoyé, ou argument d'un outil). `read` n'est appelé
    qu'après le refus de la démonstration : un envoi n'est pas décodé pour rien."""
    if demo:
        raise InputError(
            "démonstration : l'extraction est simulée pour les contrats du jeu "
            "seulement ; choisissez-en un"
        )
    text = read()
    if not text.strip():
        raise InputError("le texte du contrat est vide")
    if CONTROL.search(text):
        raise InputError(
            "le texte contient un caractère de contrôle : ce n'est pas du texte brut"
        )
    return ContractInput(text=text, parties=_names(parties), base="contrat")


def contract_id(wanted: str, base: str, now: datetime) -> str:
    """Identifiant saisi, selon la règle du domaine (commune avec la CLI) ; à défaut, la
    base horodatée et un suffixe aléatoire."""
    wanted = wanted.strip()
    if not wanted:
        return f"{base}-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    try:
        return check_contract_id(wanted)
    except ContractIdError as exc:
        raise InputError(str(exc)) from exc


def analysis_date(raw: str) -> date | None:
    """Date d'analyse saisie, AAAA-MM-JJ ; vide : celle du service par défaut."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InputError("date invalide : AAAA-MM-JJ attendu") from exc

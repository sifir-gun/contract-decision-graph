"""Corpus RAG, partie pure : nettoyage des textes publics, versions, découpage, fiches.

La lecture des fichiers et la construction des extraits embarqués sont dans
`application/ingestion.py`.

Règles de nettoyage explicites, testées sur les fichiers réels :
1. Légifrance : « Version en vigueur … » et « Modifié par … » / « Création … » deviennent
   des métadonnées (début, fin de validité, texte modificateur), pas du contenu indexé ;
2. lignes d'interface en fin de fichier (« Voir les versions »…) : supprimées ;
3. notes « Conformément à … » : sorties du texte, conservées en métadonnée `note` ;
4. la fin de validité est stockée ; `expired` dit si une version a expiré à une date.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from cdg.domain.models import Domain

FICHE_DISCLAIMER = "Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique."

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "janvier",
            "février",
            "mars",
            "avril",
            "mai",
            "juin",
            "juillet",
            "août",
            "septembre",
            "octobre",
            "novembre",
            "décembre",
        ],
        start=1,
    )
}
_DATE = r"(\d{1,2}(?:er)? [a-zéû]+ \d{4})"
_VERSION = re.compile(rf"^Version en vigueur (?:du {_DATE} au {_DATE}|depuis le {_DATE})$")
_AMENDMENT = re.compile(r"^(Modifié par|Création) (.+)$")
_NOTE = re.compile(r"^Conformément (?:à|aux?) ")
_INTERFACE = ("Voir les versions", "Comparer les versions", "Textes liés")
_TITLE = re.compile(r"^Article (\S+)$")


def french_date(text: str) -> date:
    match = re.fullmatch(r"(\d{1,2})(?:er)? ([a-zéû]+) (\d{4})", text.strip())
    if not match or match[2] not in _MONTHS:
        raise ValueError(f"date française illisible : {text!r}")
    return date(int(match[3]), _MONTHS[match[2]], int(match[1]))


def expired(valid_until: date | None, on: date) -> bool:
    """Version expirée à la date `on` : sa fin de validité est atteinte."""
    return valid_until is not None and on >= valid_until


@dataclass(frozen=True)
class Article:
    article: str
    heading: str | None
    text: str
    valid_from: date | None = None
    valid_until: date | None = None
    amendment: tuple[str, str] | None = None  # (modification | création, texte modificateur)
    note: str | None = None
    source_id: str = ""
    reference: str = ""
    retrieved_at: date | None = None


def _lines_without_interface(raw: str) -> list[str]:
    lines = raw.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith(_INTERFACE)):
        lines.pop()
    return lines


def _paragraphs(lines: list[str]) -> str:
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in lines)).strip()


def _title(lines: list[str]) -> tuple[str, list[str]]:
    while lines and not lines[0].strip():
        lines = lines[1:]
    match = _TITLE.match(lines[0].strip()) if lines else None
    if not match:
        raise ValueError(f"titre d'article attendu en première ligne : {lines[:1]!r}")
    return match[1], lines[1:]


def parse_legifrance(raw: str) -> Article:
    number, lines = _title(_lines_without_interface(raw))
    valid_from = valid_until = amendment = None
    body, notes = [], []
    for line in lines:
        stripped = line.strip()
        if version := _VERSION.match(stripped):
            start, end, since = version.groups()
            valid_from = french_date(start or since)
            valid_until = french_date(end) if end else None
        elif amended := _AMENDMENT.match(stripped):
            kind = "modification" if amended[1] == "Modifié par" else "création"
            amendment = (kind, amended[2])
        elif _NOTE.match(stripped):
            notes.append(stripped)
        else:
            body.append(line)
    return Article(
        article=number,
        heading=None,
        text=_paragraphs(body),
        valid_from=valid_from,
        valid_until=valid_until,
        amendment=amendment,
        note="\n".join(notes) or None,
    )


def parse_eurlex(raw: str) -> Article:
    number, lines = _title(_lines_without_interface(raw))
    rest = [line for line in lines]
    while rest and not rest[0].strip():
        rest = rest[1:]
    heading = rest[0].strip() if rest else None
    return Article(article=number, heading=heading, text=_paragraphs(rest[1:]))


def chunk(text: str, max_words: int) -> list[str]:
    """Paragraphes regroupés jusqu'à `max_words` mots ; un paragraphe trop long est coupé
    aux fins de phrase, puis aux mots en dernier recours."""
    pieces: list[str] = []
    for paragraph in text.split("\n\n"):
        if len(paragraph.split()) <= max_words:
            pieces.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.;:])\s+", paragraph):
            words = sentence.split()
            pieces += [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]
    chunks, current = [], []
    for piece in pieces:
        if current and len(" ".join(current + [piece]).split()) > max_words:
            chunks.append("\n\n".join(current))
            current = []
        current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# --- Manifeste ------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    sources: dict

    def files(self) -> list[tuple[str, tuple[str, str]]]:
        return [
            (f"{s['directory']}/{s['file'].format(article=a)}", (sid, a))
            for sid, s in self.sources.items()
            for a in s["articles"]
        ]

    def excluded_files(self) -> list[str]:
        return [
            f"{s['directory']}/{s['file'].format(article=a)}"
            for s in self.sources.values()
            for a in s.get("excluded", {})
        ]

    def admits(self, source_id: str, article: str) -> bool:
        return article in self.sources.get(source_id, {}).get("articles", {})


# --- Fiches ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fiche:
    id: str
    title: str
    domains: list[str]
    body: str


_CITATION = re.compile(r"\(sources? : ([^)]+)\)")
_ONE = re.compile(r"([a-z-]+), art\. ([A-Z]?\d+(?:-\d+)?)")


def claim_lines(body: str) -> list[str]:
    """Lignes d'affirmation : tout sauf l'avertissement, les titres et les lignes vides."""
    return [
        line for line in body.splitlines()[1:] if line.strip() and not line.lstrip().startswith("#")
    ]


def citations(line: str) -> list[tuple[str, str]]:
    return [(m[1], m[2]) for block in _CITATION.findall(line) for m in _ONE.finditer(block)]


# --- Extrait à ingérer -------------------------------------------------------------------


class ChunkRow(BaseModel):
    """Extrait à ingérer (administrateur)."""

    domain: Domain
    source_id: str
    reference: str
    text: str
    embedding_model: str
    embedding: list[float]
    article: str | None = None
    chunk_index: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None  # fin de validité de la version du texte
    amendment: str | None = None
    note: str | None = None
    retrieved_at: date | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

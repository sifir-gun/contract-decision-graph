"""Corpus RAG, partie pure : nettoyage des textes publics, versions, découpage, fiches.

La lecture des fichiers et la construction des extraits embarqués sont dans
`application/ingestion.py`.

Règles de nettoyage explicites, testées sur les fichiers réels :
1. Légifrance : « Version en vigueur … » et « Modifié par … » / « Création … » deviennent
   des métadonnées (début, fin de validité, texte modificateur), pas du contenu indexé ;
2. lignes d'interface en fin de fichier (« Voir les versions »…) : supprimées ;
3. notes « Conformément à … » : sorties du texte, conservées en métadonnée `note` ;
   l'étiquette « NOTA : » qui les précède sur la page est supprimée ;
4. début et fin de validité sont stockés ; `expired` dit si une version a expiré à une
   date, `in_force` si elle y est en vigueur, et `drop_superseded` écarte les versions
   supplantées : la date d'analyse choisit la version (docs/spec-versions-corpus.md).

Rattachement déclaré (J4) : chaque article du manifeste et chaque fiche déclarent les types
de clause qu'ils peuvent justifier ; les domaines d'indexation s'en déduisent (`by_domain`).
"""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol

from pydantic import BaseModel, model_validator

from cdg.domain.models import DOMAIN_KINDS, DOMAINS, Domain

FICHE_DISCLAIMER = (
    "Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique."
)

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
_VERSION = re.compile(
    rf"^Version en vigueur (?:du {_DATE} au {_DATE}|depuis le {_DATE}"
    rf"|à partir du {_DATE})$"
)
_AMENDMENT = re.compile(r"^(Modifié par|Création) (.+)$")
_NOTE = re.compile(r"^Conformément (?:à|aux?) ")
_NOTE_LABEL = re.compile(r"^NOTA ?:$")  # étiquette de la page, avant la note
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


def in_force(valid_from: date | None, valid_until: date | None, on: date) -> bool:
    """Version en vigueur à la date `on` : commencée (aucun début, ou début atteint) et
    non expirée."""
    return (valid_from is None or valid_from <= on) and not expired(valid_until, on)


class Versioned(Protocol):
    """Extrait ou ligne d'une version d'une référence du corpus."""

    @property
    def reference(self) -> str: ...

    @property
    def valid_from(self) -> date | None: ...

    @property
    def valid_until(self) -> date | None: ...


def drop_superseded[T: Versioned](items: Sequence[T], on: date) -> list[T]:
    """Écarte les versions supplantées à la date `on` : pour une référence qui a au moins
    une version en vigueur, ses autres versions. Une référence sans version en vigueur
    garde toutes les siennes, que le CRAG signale. Ordre conservé."""
    current = {i.reference for i in items if in_force(i.valid_from, i.valid_until, on)}
    return [
        i
        for i in items
        if i.reference not in current or in_force(i.valid_from, i.valid_until, on)
    ]


def expiring(
    validities: Mapping[str, date | None], on: date, days: int
) -> list[tuple[str, date]]:
    """Sources dont la version cesse d'être valide au plus tard `days` jours après `on`,
    déjà expirées comprises ; de la plus proche échéance à la plus lointaine."""
    horizon = on + timedelta(days=days)
    return sorted(
        ((source, end) for source, end in validities.items() if end and end <= horizon),
        key=lambda item: (item[1], item[0]),
    )


@dataclass(frozen=True)
class Article:
    article: str
    heading: str | None
    text: str
    valid_from: date | None = None
    valid_until: date | None = None
    amendment: tuple[str, str] | None = (
        None  # (modification | création, texte modificateur)
    )
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
            start, end, since, upcoming = version.groups()
            valid_from = french_date(start or since or upcoming)
            valid_until = french_date(end) if end else None
        elif _NOTE_LABEL.match(stripped):
            continue
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
            pieces += [
                " ".join(words[i : i + max_words])
                for i in range(0, len(words), max_words)
            ]
    chunks: list[str] = []
    current: list[str] = []
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
    sources: dict[str, Any]

    def __post_init__(self) -> None:
        for sid, source in self.sources.items():
            unknown = sorted(
                set(source.get("later_versions", {})) - set(source["articles"])
            )
            if unknown:
                raise ValueError(
                    f"{sid} : versions suivantes d'un article non admis : "
                    + ", ".join(unknown)
                )

    def files(self) -> list[tuple[str, tuple[str, str]]]:
        """Fichiers des articles admis, chaque version dans l'ordre : le fichier de
        `file`, puis ceux des versions suivantes (`later_versions`)."""
        return [
            (f"{s['directory']}/{name}", (sid, a))
            for sid, s in self.sources.items()
            for a in s["articles"]
            for name in [
                s["file"].format(article=a),
                *(v["file"] for v in self.later(sid, a)),
            ]
        ]

    def later(self, source_id: str, article: str) -> list[dict[str, Any]]:
        """Versions suivantes d'un article (fichier, url, date de récupération)."""
        return list(self.sources[source_id].get("later_versions", {}).get(article, []))

    def excluded_files(self) -> list[str]:
        return [
            f"{s['directory']}/{s['file'].format(article=a)}"
            for s in self.sources.values()
            for a in s.get("excluded", {})
        ]

    def admits(self, source_id: str, article: str) -> bool:
        return article in self.sources.get(source_id, {}).get("articles", {})


def check_chain(versions: Sequence[tuple[str, Article]]) -> None:
    """Versions successives d'un article, (fichier, article) dans l'ordre du manifeste :
    une version suivante est nommée par son début de validité (`…_AAAA-MM-JJ.txt`), la
    fin de chacune est le début de la suivante, seule la dernière peut rester ouverte.
    Tout écart est une erreur qui nomme le fichier."""
    for index, (path, article) in enumerate(versions):
        if index:
            suffix = PurePosixPath(path).stem.rpartition("_")[2]
            if article.valid_from is None or suffix != article.valid_from.isoformat():
                raise ValueError(
                    f"{path} : suffixe {suffix} différent du début de validité "
                    f"{article.valid_from}"
                )
            previous_path, previous = versions[index - 1]
            if previous.valid_until != article.valid_from:
                raise ValueError(
                    f"{path} : début de validité {article.valid_from}, fin de "
                    f"{previous_path} : {previous.valid_until} : versions mal enchaînées"
                )
        if index < len(versions) - 1 and article.valid_until is None:
            raise ValueError(f"{path} : version ouverte avant la dernière")


# --- Rattachement aux types de clause --------------------------------------------------


def by_domain(kinds: list[str]) -> list[tuple[Domain, list[str]]]:
    """Domaines d'indexation d'une source, chacun avec ses types de clause, dans l'ordre
    des domaines puis des types. Un type inconnu, ou aucun type, est une erreur."""
    if not kinds:
        raise ValueError("aucun type de clause déclaré")
    known = {kind for domain_kinds in DOMAIN_KINDS.values() for kind in domain_kinds}
    unknown = sorted(set(kinds) - known)
    if unknown:
        raise ValueError(f"type de clause inconnu : {', '.join(unknown)}")
    return [
        (domain, [k for k in DOMAIN_KINDS[domain] if k in kinds])
        for domain in DOMAINS
        if set(DOMAIN_KINDS[domain]) & set(kinds)
    ]


# --- Fiches ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fiche:
    id: str
    title: str
    kinds: list[str]  # types de clause que la fiche peut justifier
    body: str
    # (source, article) -> débuts de validité des versions contre lesquelles la fiche a
    # été relue (`versions_verifiees`) ; absent : la première version seule
    verified: Mapping[tuple[str, str], Sequence[date]] = field(default_factory=dict)

    @property
    def domains(self) -> list[Domain]:
        return [domain for domain, _ in by_domain(self.kinds)]


_CITATION = re.compile(r"\(sources? : ([^)]+)\)")
_ONE = re.compile(r"([a-z-]+), art\. ([A-Z]?\d+(?:-\d+)?)")


TEXT_SECTION = "Ce que dit le texte"  # paraphrases fidèles, chacune sourcée
APPLICATION_SECTION = "Comment le projet l'applique"  # choix de politique d'achat


def fiche_sections(body: str) -> dict[str, list[str]]:
    """Lignes d'affirmation par section de niveau 2 (`## …`), dans l'ordre du fichier."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif line.strip() and not line.lstrip().startswith("#") and current is not None:
            current.append(line)
    return sections


Window = tuple[date | None, date | None]  # début et fin de validité ; None : sans borne


def fiche_window(
    fiche_id: str,
    cited: set[tuple[str, str]],
    verified: Mapping[tuple[str, str], Sequence[date]],
    versions: Mapping[tuple[str, str], Sequence[Window]],
) -> Window:
    """Période de validité d'une fiche : pour chaque article cité, les versions qu'elle
    déclare avoir relues, sinon la première seule ; la fiche vaut sur l'intersection de
    ces périodes. Une version ajoutée sans relecture la fait donc expirer à son entrée en
    vigueur. Déclaration incohérente ou intersection vide : erreur qui nomme la fiche."""
    foreign = sorted(set(verified) - cited)
    if foreign:
        raise ValueError(
            f"{fiche_id} : versions déclarées pour un article non cité : "
            + ", ".join(f"{s} {a}" for s, a in foreign)
        )
    start: date | None = None
    end: date | None = None
    for key in sorted(cited):
        chain = list(versions[key])
        covered = chain[:1]
        if key in verified:
            if not verified[key]:
                raise ValueError(
                    f"{fiche_id} : aucune version déclarée pour {key[0]} {key[1]}"
                )
            starts = [version_start for version_start, _ in chain]
            indexes = []
            for declared in verified[key]:
                if declared not in starts:
                    raise ValueError(
                        f"{fiche_id} : version du {declared} de {key[0]} {key[1]} "
                        "absente du corpus"
                    )
                indexes.append(starts.index(declared))
            if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
                raise ValueError(
                    f"{fiche_id} : versions déclarées non consécutives pour "
                    f"{key[0]} {key[1]}"
                )
            covered = chain[indexes[0] : indexes[-1] + 1]
        first, last = covered[0][0], covered[-1][1]
        if first is not None:
            start = first if start is None else max(start, first)
        if last is not None:
            end = last if end is None else min(end, last)
    if start is not None and end is not None and start >= end:
        raise ValueError(f"{fiche_id} : fenêtre de validité vide ({start} → {end})")
    return start, end


def claim_lines(body: str) -> list[str]:
    """Lignes d'affirmation : tout sauf l'avertissement, les titres et les lignes vides."""
    return [
        line
        for line in body.splitlines()[1:]
        if line.strip() and not line.lstrip().startswith("#")
    ]


def citations(line: str) -> list[tuple[str, str]]:
    return [
        (m[1], m[2]) for block in _CITATION.findall(line) for m in _ONE.finditer(block)
    ]


# --- Extrait à ingérer -------------------------------------------------------------------


def embedded_text(header: str, text: str) -> str:
    """Texte embarqué : l'en-tête écrit par le code, puis le texte de la source."""
    return f"{header}\n{text}"


def embedded_hash(passage_prefix: str, embedded: str) -> str:
    """Empreinte de ce qui détermine le vecteur, à modèle égal : préfixe de passage du
    modèle et texte embarqué. Elle change, l'extrait est réindexé (`sync`)."""
    return hashlib.sha256(f"{passage_prefix}{embedded}".encode()).hexdigest()


class ChunkRow(BaseModel):
    """Extrait à ingérer (administrateur), rattaché aux types de clause de son domaine que
    sa source peut justifier."""

    domain: Domain
    source_id: str
    reference: str
    text: str
    kinds: list[str]
    embedding_model: str
    embedding: list[float]
    header: str  # en-tête écrit par le code, embarqué avant le texte (ADR 006)
    embedded_hash: str  # empreinte du texte embarqué et du préfixe (`embedded_hash`)
    article: str | None = None
    chunk_index: int | None = None
    valid_from: date | None = None
    valid_until: date | None = None  # fin de validité de la version du texte
    amendment: str | None = None
    note: str | None = None
    retrieved_at: date | None = None

    @model_validator(mode="after")
    def _clauses_du_domaine(self) -> "ChunkRow":
        if not self.kinds:
            raise ValueError(f"{self.reference} : aucun type de clause déclaré")
        foreign = [k for k in self.kinds if k not in DOMAIN_KINDS[self.domain]]
        if foreign:
            raise ValueError(
                f"{self.reference} : types hors du domaine {self.domain} : "
                + ", ".join(foreign)
            )
        return self

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

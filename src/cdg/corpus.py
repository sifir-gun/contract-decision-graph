"""Corpus RAG : nettoyage des textes publics, versions, découpage, manifeste, fiches.

Règles de nettoyage explicites, testées sur les fichiers réels :
1. Légifrance : « Version en vigueur … » et « Modifié par … » / « Création … » deviennent
   des métadonnées (début, fin de validité, texte modificateur), pas du contenu indexé ;
2. lignes d'interface en fin de fichier (« Voir les versions »…) : supprimées ;
3. notes « Conformément à … » : sorties du texte, conservées en métadonnée `note` ;
4. la fin de validité est stockée ; `expired` dit si une version a expiré à une date.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yaml

from cdg.deps import Embedder
from cdg.rag_store import ChunkRow

ROOT = Path(__file__).resolve().parents[2] / "data" / "corpus"
RAW, FICHES, MANIFEST = ROOT / "raw", ROOT / "fiches", ROOT / "manifest.yaml"
FICHE_DISCLAIMER = "Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique."

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
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


def load_manifest(path: Path = MANIFEST) -> Manifest:
    return Manifest(sources=yaml.safe_load(path.read_text(encoding="utf-8"))["sources"])


def articles(manifest: Manifest | None = None) -> Iterator[tuple[Article, list[str]]]:
    """Articles admis, nettoyés, avec leurs domaines d'indexation."""
    manifest = manifest or load_manifest()
    parsers = {"legifrance": parse_legifrance, "eurlex": parse_eurlex}
    for path, (source_id, number) in manifest.files():
        source = manifest.sources[source_id]
        article = parsers[source["format"]]((RAW / path).read_text(encoding="utf-8"))
        if article.article != number:
            raise ValueError(f"{path} : titre « Article {article.article} », attendu {number}")
        yield (
            replace(
                article,
                source_id=source_id,
                reference=f"{source['citation']}, art. {number}",
                retrieved_at=source["retrieved_at"],
            ),
            source["articles"][number],
        )


# --- Fiches ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fiche:
    id: str
    title: str
    domains: list[str]
    body: str


def load_fiches(directory: Path = FICHES) -> list[Fiche]:
    fiches = []
    for path in sorted(directory.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        meta = yaml.safe_load(front)
        fiches.append(
            Fiche(id=meta["id"], title=meta["titre"], domains=meta["domaines"], body=body.strip())
        )
    return fiches


_CITATION = re.compile(r"\(sources? : ([^)]+)\)")
_ONE = re.compile(r"([a-z-]+), art\. ([A-Z]?\d+(?:-\d+)?)")


def claim_lines(body: str) -> list[str]:
    """Lignes d'affirmation : tout sauf l'avertissement, les titres et les lignes vides."""
    return [
        line for line in body.splitlines()[1:] if line.strip() and not line.lstrip().startswith("#")
    ]


def citations(line: str) -> list[tuple[str, str]]:
    return [(m[1], m[2]) for block in _CITATION.findall(line) for m in _ONE.finditer(block)]


# --- Extraits à ingérer ------------------------------------------------------------------


def rows(embedder: Embedder, max_words: int) -> list[ChunkRow]:
    """Extraits des articles admis et des fiches, un par domaine d'indexation.

    Le texte embarqué est précédé de la référence (et de l'intitulé) pour la recherche ;
    le texte stocké reste celui de l'article, cité tel quel.
    """
    pending: list[tuple[dict, str, list[str]]] = []
    for article, domains in articles():
        header = article.reference + (f" — {article.heading}" if article.heading else "")
        amendment = (
            f"{article.amendment[0]} : {article.amendment[1]}" if article.amendment else None
        )
        for index, text in enumerate(chunk(article.text, max_words)):
            meta = {
                "source_id": article.source_id,
                "reference": article.reference,
                "text": text,
                "article": article.article,
                "chunk_index": index,
                "valid_from": article.valid_from,
                "valid_until": article.valid_until,
                "amendment": amendment,
                "note": article.note,
                "retrieved_at": article.retrieved_at,
            }
            pending.append((meta, f"{header}\n{text}", domains))
    for fiche in load_fiches():
        reference = f"Fiche projet : {fiche.title}"
        for index, text in enumerate(chunk(fiche.body, max_words)):
            meta = {
                "source_id": fiche.id,
                "reference": reference,
                "text": text,
                "chunk_index": index,
            }
            pending.append((meta, f"{reference}\n{text}", fiche.domains))
    vectors = embedder.embed_passages([embedded for _, embedded, _ in pending])
    return [
        ChunkRow(domain=domain, embedding_model=embedder.model, embedding=vector, **meta)
        for (meta, _, domains), vector in zip(pending, vectors, strict=True)
        for domain in domains
    ]

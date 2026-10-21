"""Explication d'une décision déjà rendue : constats à expliquer, contrôles, gabarit.

Le LLM (`application/explanation.py`) reçoit le verdict figé et les constats, jamais le
texte du contrat, et n'explique que les constats : une entrée par constat, avec sa clause,
les références citées et le texte. La synthèse, qui nomme la décision finale et décrit le
parcours (règles seules, revue humaine, décision système, tentative d'instruction), est
écrite par le code (`path`), pour le LLM comme pour le gabarit : la série 4 a montré qu'un
LLM peut décrire le parcours à tort. L'explication est refusée :
- si elle nomme une autre décision que la décision finale (détection par règles sur les
  libellés de décision) ;
- si elle cite une référence non retenue pour la clause du constat : dans le champ
  `references`, ou un article ou un point d'annexe dans le texte. Un article que le texte
  du constat cite lui-même reste citable : ce texte vient du code des règles, pas du LLM.
  Une citation qui nomme une source du corpus (citation ou titre du manifeste, reçus en
  paramètre : le domaine ne lit aucun fichier) est comparée par source et numéro, une
  citation sans nom par son seul numéro : deux sources peuvent porter le même numéro
  (CPI et C. mon. fin., art. L112-2). Limite : un numéro partagé cité sans nom de source
  passe, même s'il vise l'autre source ; le lecteur voit les références du constat, qui
  la nomment ;
- si elle n'explique pas chaque constat une fois et une seule, ou change sa clause.
Le gabarit, sans LLM, passe ces contrôles ; il prend le relais après les essais refusés.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from cdg.domain.models import (
    DOMAINS,
    AgentVerdict,
    Decision,
    Domain,
    HumanDecision,
    HumanReview,
)
from cdg.domain.text import folded

Source = Literal["llm", "gabarit"]


class FindingToExplain(BaseModel):
    """Constat d'un verdict, rattaché à sa clause, avec les seules références citables."""

    id: str  # « domaine-rang », rang à partir de 1 dans les constats du verdict
    domain: Domain
    kind: (
        str | None
    )  # clause du constat ; None : constat du CRAG, ou verdict d'avant J4
    text: str
    references: list[str]  # retenues pour la clause du constat


class ExplanationRequest(BaseModel):
    """Verdict figé transmis à l'explication."""

    decision: Decision  # décision finale
    proposed_decision: Decision | None
    margin: float | None
    human: HumanReview | HumanDecision | None
    failure_stage: str | None
    input_findings: list[str] = []  # tentative d'instruction détectée dans le contrat
    findings: list[FindingToExplain]


class ExplainedFinding(BaseModel):
    id: str
    kind: str | None
    references: list[str]
    text: str


class Draft(BaseModel):
    """Sortie structurée demandée au LLM : les constats expliqués, rien d'autre."""

    findings: list[ExplainedFinding]


class Explanation(BaseModel):
    """Explication retenue, scellée avec sa source et ses motifs (hors partie décision)."""

    source: Source
    decision: Decision
    findings: list[ExplainedFinding]
    synthesis: str  # écrite par le code (`path`) : décision finale et parcours
    attempts: int = Field(ge=0)  # essais du LLM ; 0 : gabarit sans appel
    # motifs : refus des essais du LLM, erreur, ou absence de LLM ; vide si le premier
    # essai est accepté
    reasons: list[str]

    def draft(self) -> Draft:
        return Draft(findings=self.findings)


# --- Constats à expliquer ----------------------------------------------------------------


def _findings(verdicts: list[AgentVerdict]) -> list[FindingToExplain]:
    by_domain = {v.domain: v for v in verdicts}
    result = []
    for domain in DOMAINS:
        verdict = by_domain.get(domain)
        if verdict is None:
            continue
        kinds = verdict.finding_kinds or [None] * len(verdict.findings)
        retrieval = verdict.retrieval.clauses if verdict.retrieval else []
        retained = {c.kind: c.retained for c in retrieval}
        for rank, (text, kind) in enumerate(
            zip(verdict.findings, kinds, strict=True), start=1
        ):
            result.append(
                FindingToExplain(
                    id=f"{domain}-{rank}",
                    domain=domain,
                    kind=kind,
                    text=text,
                    references=list(retained.get(kind, [])) if kind else [],
                )
            )
    return result


def request(state: Mapping[str, Any]) -> ExplanationRequest:
    """Ce que reçoit l'explication, lu dans l'état : décision finale, parcours, constats.
    Ni le texte du contrat, ni les citations des clauses."""
    decision = state.get("final_decision")
    if decision is None:
        raise ValueError("explication impossible : aucune décision finale dans l'état")
    report = state.get("failure_report") or {}
    return ExplanationRequest(
        decision=decision,
        proposed_decision=state.get("proposed_decision"),
        margin=state.get("margin"),
        human=state.get("human"),
        failure_stage=report.get("stage"),
        input_findings=list(state.get("input_findings", [])),
        findings=_findings(state.get("verdicts", [])),
    )


# --- Libellés de décision et articles cités ----------------------------------------------

# du plus long au plus court : un libellé reconnu est retiré avant de chercher les suivants
# (GO_RESERVES et NO_GO contiennent GO)
_LABELS: tuple[tuple[Decision, re.Pattern[str]], ...] = (
    ("NO_GO", re.compile(r"\bno[\s_-]*go\b", re.IGNORECASE)),
    (
        "GO_RESERVES",
        re.compile(
            r"\bgo[\s_-]*(?:(?:avec|sous)[\s_-]+)?r[ée]serves?\b", re.IGNORECASE
        ),
    ),
    ("ESCALADE", re.compile(r"\bescalad\w*", re.IGNORECASE)),
    ("GO", re.compile(r"\bgo\b", re.IGNORECASE)),
)
_NUMBER = r"(?:L\.?\s?)?\d+(?:-\d+)*"
_ARTICLE = re.compile(
    rf"\bart(?:icles?|s?\.?)\s*({_NUMBER}(?:\s+(?:et|à|ou)\s+{_NUMBER})*)",
    re.IGNORECASE,
)
# point d'annexe : numéro à points, au moins un (« 4.2.10.2.1 », « 21.A.307 ») ; « point 8 »
# précise un article (« l'article 4, point 8 ») et n'est pas lu
_POINT_NUMBER = r"\d+(?:\.[0-9A-Z]+)+"
_POINT = re.compile(
    rf"\bpoints?\s+({_POINT_NUMBER}(?:\s+(?:et|à|ou)\s+{_POINT_NUMBER})*)",
    re.IGNORECASE,
)
# Ce qui sépare une citation du nom de sa source, sur le texte sans casse
# (`text.folded`). Avant elle, sous la forme des références : virgules et espaces, et
# l'annexe d'un point (« Règl. (UE) 748/2012, annexe I, point 21.A.307 »). Après elle :
# l'annexe d'un point, puis une préposition (« l'article L112-2 du code monétaire et
# financier », « art. 28 RGPD ») ; une virgule l'arrête (« art. 46, par. 3, a) RGPD »).
_SEPARATORS = r"(?:[\s,]*\bannexes?(?:\s+[ivxlc]+\b)?)?[\s,]*"
_BEFORE = re.compile(_SEPARATORS + r"\Z")
# Avant une citation, après ces séparateurs : une parenthèse ouvrante ou deux-points
# (« Code de la propriété intellectuelle (article L112-2) », « Code civil : art. 1231-5 »),
# ou « en son », « en ses » (« Le RGPD, en son article 28 »).
_INTRODUCED = re.compile(r"(?:\s*[(:]|[\s,]*\ben\s+s(?:on|es)\b)\s*\Z")
# Entre deux noms d'une énumération de noms (« le Code de commerce et le Code civil : »).
_LISTED = re.compile(
    r"[\s,]*\b(?:et|ou)\s+"
    r"(?:(?:le|la|les|du|des|au|aux|de\s+la|à\s+la)\s+|(?:de\s+|à\s+)?l')?\Z"
)
# Entre le nom qui suit une énumération et la citation suivante qu'il ouvre (`_opens`) :
# les mêmes séparateurs, ou le « l' » de « l'article » et de « l'art. » (la citation
# commence à « art »).
_OPENING = re.compile(_SEPARATORS + r"(?:\bl')?")
_AFTER = re.compile(
    r"\A\s*(?:de\s+l'annexe(?:\s+[ivxlc]+\b)?\s+)?(?:de\s+l'|d'|(?:du|des|de\s+la|de)\s+)?"
)
# Après une citation, un nom seul entre parenthèses (« l'article L112-2 (CPI) »).
_OPENED = re.compile(r"\A\s*\(\s*")
_CLOSED = re.compile(r"\s*\)")
_WORD = re.compile(r"\w")
# noms des sources, sans casse, du plus long au plus court : (nom, source)
_Lookup = list[tuple[str, str]]
Citation = tuple[str | None, str]  # (source rattachée, ou None ; numéro)


def named_decisions(text: str) -> set[Decision]:
    """Libellés de décision nommés dans un texte (GO sous réserves compris)."""
    found: set[Decision] = set()
    for label, pattern in _LABELS:
        if pattern.search(text):
            found.add(label)
            text = pattern.sub(" ", text)
    return found


def _lookup(names: Mapping[str, Sequence[str]]) -> _Lookup:
    """Noms des sources sans casse, du plus long au plus court : le nom le plus long
    l'emporte (« Code civil local » sur « Code civil »). Un nom vide, ou commun à deux
    sources, est une erreur : il ne rattacherait une citation à aucune source sûre ; une
    chaîne seule aussi, qui serait lue lettre par lettre."""
    owners: dict[str, str] = {}
    for source, own in names.items():
        if isinstance(own, str):
            raise TypeError(f"{source} : noms de source attendus en liste")
        for name in own:
            key = folded(name)
            if not key:
                raise ValueError(f"{source} : nom de source vide")
            owner = owners.setdefault(key, source)
            if owner != source:
                raise ValueError(f"nom de source commun à {owner} et {source} : {name}")
    return sorted(owners.items(), key=lambda item: len(item[0]), reverse=True)


def check_names(names: Mapping[str, Sequence[str]]) -> None:
    """Refuse des noms de source vides, communs à deux sources, ou donnés en chaîne seule
    (`_lookup`) : appelé à leur lecture, avant toute explication."""
    _lookup(names)


def _glued(char: str) -> bool:
    return bool(_WORD.match(char))


def _ending(head: str, lookup: _Lookup) -> tuple[str, int] | None:
    """Source dont un nom finit `head`, et la position où ce nom commence."""
    for name, source in lookup:
        if head.endswith(name) and not _glued(head[-len(name) - 1 : -len(name)]):
            return source, len(head) - len(name)
    return None


def _source_before(head: str, lookup: _Lookup) -> str | None:
    """Source dont un nom finit `head`, le texte qui précède une citation : après des
    séparateurs (`_BEFORE`), ou suivi d'une parenthèse ouvrante, de deux-points ou de
    « en son » (`_INTRODUCED`), sauf s'il suit un autre nom et « et » ou « ou »
    (`_LISTED`) : rien ne dit alors lequel des noms vise la citation."""
    head = _BEFORE.sub("", head, count=1)
    found = _ending(head, lookup)
    if found is not None:
        return found[0]
    introduced = _INTRODUCED.search(head)
    if introduced is None:
        return None
    head = head[: introduced.start()]
    found = _ending(head, lookup)
    if found is None:
        return None
    source, start = found
    listed = _LISTED.search(head[:start])
    if listed is not None and _ending(head[: listed.start()], lookup) is not None:
        return None
    return source


def _source_after(tail: str, lookup: _Lookup) -> tuple[str, int, bool] | None:
    """Source dont un nom commence `tail`, le texte qui suit une citation ; la longueur
    de `tail` jusqu'à la fin de ce nom ; et si le nom est lié à la citation : précédé
    d'une préposition (« du », « de l' », « de l'annexe »…), hors des espaces `_AFTER` ne
    retire rien d'autre, ou seul entre parenthèses (« (CPI) », longueur jusqu'à la
    parenthèse fermante)."""
    opened = _OPENED.match(tail)
    if opened is not None:
        rest = tail[opened.end() :]
        for name, source in lookup:
            closed = _CLOSED.match(rest, len(name)) if rest.startswith(name) else None
            if closed is not None:
                return source, opened.end() + closed.end(), True
        return None
    rest = _AFTER.sub("", tail, count=1)
    gap = len(tail) - len(rest)
    for name, source in lookup:
        if rest.startswith(name) and not _glued(rest[len(name) : len(name) + 1]):
            return source, gap + len(name), bool(tail[:gap].strip())
    return None


def _citations(text: str, lookup: _Lookup) -> set[Citation]:
    text = folded(text)
    matches = sorted(
        (
            (match, number, form)
            for pattern, number, form in (
                (_ARTICLE, _NUMBER, lambda n: re.sub(r"[.\s]", "", n).upper()),
                (_POINT, _POINT_NUMBER, str.upper),
            )
            for match in pattern.finditer(text)
        ),
        key=lambda item: item[0].start(),
    )
    found: set[Citation] = set()
    # fin du dernier nom pris ou consommé après une citation : il ne sert pas de nom
    # avant à la suivante (« art. 1171 du Code civil, art. L442-1 du Code de commerce »)
    taken = 0
    # nom laissé à la citation suivante, qu'il ouvre (`_opens`), par une citation qui a
    # déjà un nom avant : il en est le nom avant, même séparé d'elle, après une
    # énumération, par « l' » (« … et 1231-5 C. com., l'article L442-1 »)
    left: str | None = None
    for index, (match, number, form) in enumerate(matches):
        numbers = [form(n) for n in re.findall(number, match[1], re.IGNORECASE)]
        # dans une énumération, le nom qui précède touche le premier numéro, le nom qui
        # suit le dernier ; les autres numéros restent sans source
        sources: list[str | None] = [None] * len(numbers)
        before = (
            left
            if left is not None
            else _source_before(text[taken : match.start()], lookup)
        )
        left = None
        after = _source_after(text[match.end() :], lookup)
        if before is not None:
            sources[0] = before
        following = matches[index + 1][0].start() if index + 1 < len(matches) else None
        enumeration = len(numbers) > 1
        if after is not None and before is None:
            sources[-1] = after[0]
            taken = match.end() + after[1]
        elif after is not None:
            # la citation a un nom avant : le nom qui la suit ouvre la suivante (`_opens`)
            # ou il est consommé, pris par le dernier numéro d'une énumération, ignoré par
            # une citation seule, que le nom avant rattache (« C. com., art. L441-10 du
            # Code civil » : Code de commerce)
            if _opens(text, match.end(), after, following, enumeration=enumeration):
                left = after[0]
            else:
                if enumeration:
                    sources[-1] = after[0]
                taken = match.end() + after[1]
        found |= set(zip(sources, numbers, strict=True))
    return found


def _opens(
    text: str,
    cited: int,
    after: tuple[str, int, bool],
    following: int | None,
    *,
    enumeration: bool,
) -> bool:
    """Le nom `after` (`_source_after`), qui suit la citation finie à `cited`, ouvre la
    citation suivante, qui commence à `following` : il en est le nom avant (« C. com.,
    art. L441-10 C. civ., art. 1231-5 », « … et 1231-5 C. com., l'article L442-1 »).
    Seulement s'il n'est pas lié à sa citation (`_source_after` : ni préposition, « … et
    L112-2 du code monétaire et financier, art. L441-10 » : il suit L112-2, ni
    parenthèses), et si seuls des séparateurs le séparent de la citation suivante
    (`_BEFORE`), ou, après une énumération, « l'article » ou « l'art. » (`_OPENING`).
    Limite : après une citation seule, « l' » ne l'ouvre pas."""
    _, length, bound = after
    gap = _OPENING if enumeration else _BEFORE
    return (
        following is not None
        and not bound
        and gap.fullmatch(text[cited + length : following]) is not None
    )


def citations(text: str, names: Mapping[str, Sequence[str]]) -> set[Citation]:
    """Articles cités (« art. 28 », « article L. 441-10 », « articles 44 à 46 »), numéros
    normalisés (L441-10), et points d'annexe (« point 4.2.10.2.1 », « points 21.A.307 et
    21.A.308 »), qui gardent leurs points (21.A.307) ; chacun rattaché à la source dont un
    nom (`names` : citation et titre de chaque source du corpus) le touche, avant lui
    (« C. mon. fin., art. L112-2 ») ou après lui (« l'article L112-2 du code monétaire et
    financier »), casse ignorée, sur le texte normalisé ; sinon à aucune (None). Un nom
    absent du corpus (« code de la consommation ») ne rattache rien.

    Un nom touche aussi la citation seul entre parenthèses, juste après elle (« l'article
    L112-2 (CPI) ») : il lui est lié, comme après une préposition. Ou avant elle, suivi
    d'une parenthèse ouvrante, de deux-points ou de « en son » (« Le Code de la propriété
    intellectuelle (article L112-2) », « Code civil : art. 1231-5 », « Le CPI, en son
    article L112-2 ») ; sauf, sous ces trois formes, s'il suit un autre nom et « et » ou
    « ou » (« Le Code de commerce et le Code civil : art. L441-10 et art. 1231-5 ») : rien
    ne dit lequel des noms vise la citation, comparée par son seul numéro.

    Un nom pris après une citation ne sert pas de nom avant à la suivante. Une citation
    qui a un nom avant consomme aussi le nom qui la suit et la touche, avec ou sans
    préposition : il ne sert à aucune autre citation (« C. com., art. L441-10 du Code de
    commerce, art. 1231-5 du Code civil » : 1231-5 au Code civil). S'ils désignent deux
    sources, le nom avant l'emporte (« C. com., art. L441-10 du Code civil » : Code de
    commerce). Sauf si ce nom ouvre la citation suivante (`_opens`) : il n'est pas lié à
    la sienne (ni préposition, ni parenthèses), et seuls des séparateurs le séparent de la
    suivante (« C. com., art. L441-10 C. civ., art. 1231-5 » : C. civ. est à 1231-5), ou,
    après une énumération seulement, « l'article » ou « l'art. ».

    Dans une énumération, seul le numéro que le nom touche lui est rattaché : le dernier
    pour un nom qui suit (« articles 1171 et L442-1 du Code de commerce » : L442-1), le
    premier pour un nom qui précède (« C. civ., art. 1171 et L442-1 » : 1171), les deux
    s'il y a un nom de chaque côté (« C. civ., art. 1231-1 et L112-2 du code monétaire et
    financier »), sauf si le nom qui suit ouvre la citation suivante (« C. civ., art. 1171
    et 1231-5 C. com., art. L442-1 », « …, l'article L442-1 » : il est à L442-1). Un nom
    lié à sa citation n'ouvre jamais la suivante (« C. civ., art. 1171 et L112-2 du code
    monétaire et financier, art. L441-10 » : il est à L112-2). Les autres numéros sont
    sans source, comparés par leur seul numéro.

    Limites. Un numéro d'une énumération qui viserait l'homonyme d'un autre code passe,
    comme une citation sans nom ; cela vaut aussi pour le texte du constat, écrit par le
    code et lu par la même fonction (`_citable`) : « (art. 44 à 46 RGPD) » laisse 44 sans
    source, citable sous tout nom (aucun homonyme de 44 au corpus). Après une citation
    seule, « l'article » n'ouvre pas la suivante : « C. com., art. L441-10 C. civ.,
    l'article 1231-5 » laisse 1231-5 sans source, comparé par son seul numéro. Après un
    nom qui ne suit aucune citation non plus : « Selon le CPI, l'article L112-2 » et « CPI,
    l'article L112-2 » laissent L112-2 sans source, comme « Au-delà du Code civil,
    l'article L442-1 », qui ne vise pas le Code civil. Un nom entre parenthèses suivi
    d'autre chose (« L112-2 (CPI, indexation) ») ne rattache rien. En sens inverse
    (refus, puis gabarit) : la garde de l'énumération de noms ne vaut pas pour les
    séparateurs des références (« Le Code de commerce et le Code civil, art. L441-10 » :
    Code civil), et un nom avant, sous toutes ses formes, l'emporte sur un autre nom qui
    suit la citation (« Hors du Code civil : article L441-10 du Code de commerce » : Code
    civil)."""
    return _citations(text, _lookup(names))


def articles(text: str) -> set[str]:
    """Numéros des articles et des points d'annexe cités (`citations`), sans leur
    source."""
    return {number for _, number in _citations(text, [])}


def _citable(finding: FindingToExplain, lookup: _Lookup) -> set[Citation]:
    """Ce que l'explication d'un constat peut citer : ses références retenues, chacune
    lue à part (le nom de la suivante ne rattache pas l'article de la précédente), et le
    texte du constat, écrit par le code des règles, avec les mêmes règles et la même
    limite (`citations` : un numéro d'énumération que le nom ne touche pas est sans
    source)."""
    found = _citations(finding.text, lookup)
    for reference in finding.references:
        found |= _citations(reference, lookup)
    return found


def _uncitable(cited: set[Citation], citable: set[Citation]) -> dict[str, list[str]]:
    """Citations du LLM hors de `citable`, chacune avec sa source si elle en nomme une,
    les articles (« article ») à part des points d'annexe (« point d'annexe ») : un numéro
    d'article est normalisé sans point, un numéro de point en garde au moins un. Sans
    source : comparée par son seul numéro, comme avant les homonymes. Avec une source :
    par source et numéro ; un numéro que le texte du constat cite sans nom de source
    reste citable sous tout nom."""
    numbers = {number for _, number in citable}
    unnamed = {number for source, number in citable if source is None}
    found: dict[str, list[str]] = {"article": [], "point d'annexe": []}
    for source, number in cited:
        if (
            number in numbers
            if source is None
            else ((source, number) in citable or number in unnamed)
        ):
            continue
        unit = "point d'annexe" if "." in number else "article"
        found[unit].append(number if source is None else f"{number} ({source})")
    return {unit: sorted(wrong) for unit, wrong in found.items()}


# --- Contrôles ---------------------------------------------------------------------------


def refusals(
    draft: Draft, req: ExplanationRequest, *, names: Mapping[str, Sequence[str]]
) -> list[str]:
    """Motifs de refus d'une explication ; vide : acceptée. `names` : noms de chaque
    source du corpus, qui rattachent une citation à sa source (`citations`)."""
    lookup = _lookup(names)
    expected = {f.id: f for f in req.findings}
    ids = [e.id for e in draft.findings]
    reasons = []
    for label, wrong in (
        ("constats non expliqués", [i for i in expected if i not in ids]),
        ("constats inconnus", [i for i in ids if i not in expected]),
        (
            "constats expliqués plusieurs fois",
            sorted({i for i in ids if ids.count(i) > 1}),
        ),
    ):
        if wrong:
            reasons.append(f"{label} : {', '.join(wrong)}")

    def others(text: str) -> str:
        return ", ".join(sorted(named_decisions(text) - {req.decision}))

    for entry in draft.findings:
        prefix = f"{entry.id} : "
        if not entry.text.strip():
            reasons.append(prefix + "texte vide")
        if labels := others(entry.text):
            reasons.append(
                prefix + "nomme une autre décision que la décision finale "
                f"({req.decision}) : {labels}"
            )
        finding = expected.get(entry.id)
        if finding is None:
            continue
        if entry.kind != finding.kind:
            reasons.append(prefix + f"clause {entry.kind}, attendue {finding.kind}")
        foreign = [r for r in entry.references if r not in finding.references]
        if foreign:
            reasons.append(
                prefix
                + f"référence non retenue pour la clause {finding.kind} : "
                + ", ".join(foreign)
            )
        # motif d'un article mot pour mot, renvoyé au LLM et scellé
        cited = _uncitable(_citations(entry.text, lookup), _citable(finding, lookup))
        for unit, wrong in cited.items():
            if wrong:
                reasons.append(
                    prefix + f"{unit} cité hors des références retenues pour la clause "
                    f"{finding.kind} : {', '.join(wrong)}"
                )

    return reasons


def accepted(
    draft: Draft, req: ExplanationRequest, *, attempts: int, reasons: list[str]
) -> Explanation:
    return Explanation(
        source="llm",
        decision=req.decision,
        findings=draft.findings,
        synthesis=path(req),
        attempts=attempts,
        reasons=reasons,
    )


# --- Gabarit -----------------------------------------------------------------------------


def path(req: ExplanationRequest) -> str:
    """Synthèse écrite par le code, pour le LLM comme pour le gabarit : la décision finale
    et le parcours, sans autre libellé de décision. Ni relecteur ni motif humain : texte
    libre, scellé avec la décision humaine."""
    parts = [f"Décision finale : {req.decision}."]
    human = req.human
    if human is None:
        margin = (
            f", marge de {req.margin:g} au seuil le plus proche"
            if req.margin is not None
            else ""
        )
        parts.append(
            f"Décision rendue par les règles du projet, sans revue humaine{margin}."
        )
    elif human.source == "systeme":
        parts.append(
            "Décision système : le délai de revue humaine est dépassé, aucune décision "
            "humaine n'a été rendue."
        )
    else:
        if req.proposed_decision == "ESCALADE":
            how = (
                "Les règles n'ont proposé aucune décision et ont demandé une revue "
                "humaine, qui a tranché"
            )
        elif human.decision == req.proposed_decision:
            how = "Décision proposée par les règles et confirmée en revue humaine"
        else:
            how = "Décision prise en revue humaine, différente de la proposition des règles"
        if human.overrides_block:
            how += ", avec levée d'un blocage dur"
        parts.append(how + ".")
    if req.input_findings:
        parts.append(
            "Tentative d'instruction détectée dans le contrat : revue humaine "
            "obligatoire."
        )
    if req.failure_stage is not None:
        parts.append(f"Rapport d'échec : stade {req.failure_stage}.")
    count = len(req.findings)
    parts.append(
        f"{count} constat(s) des règles et de la recherche, détaillés ci-dessus."
        if count
        else "Aucune règle déclenchée : aucun constat."
    )
    return " ".join(parts)


def template(
    req: ExplanationRequest, *, reasons: list[str], attempts: int = 0
) -> Explanation:
    """Explication sans LLM : chaque constat tel que l'ont écrit les règles, avec les
    références retenues pour sa clause, puis la synthèse du parcours."""
    return Explanation(
        source="gabarit",
        decision=req.decision,
        findings=[
            ExplainedFinding(id=f.id, kind=f.kind, references=f.references, text=f.text)
            for f in req.findings
        ],
        synthesis=path(req),
        attempts=attempts,
        reasons=reasons,
    )

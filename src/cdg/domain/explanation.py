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
  `references`, ou un article dans le texte. Un article que le texte du constat cite
  lui-même reste citable : ce texte vient du code des règles, pas du LLM ;
- si elle n'explique pas chaque constat une fois et une seule, ou change sa clause.
Le gabarit, sans LLM, passe ces contrôles ; il prend le relais après les essais refusés.
"""

import re
from collections.abc import Mapping
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


def named_decisions(text: str) -> set[Decision]:
    """Libellés de décision nommés dans un texte (GO sous réserves compris)."""
    found: set[Decision] = set()
    for label, pattern in _LABELS:
        if pattern.search(text):
            found.add(label)
            text = pattern.sub(" ", text)
    return found


def articles(text: str) -> set[str]:
    """Numéros d'articles cités (« art. 28 », « article L. 441-10 », « articles 44 à 46 »),
    normalisés (L441-10). La source (RGPD, code) n'est pas comparée."""
    return {
        re.sub(r"[.\s]", "", number).upper()
        for match in _ARTICLE.finditer(text)
        for number in re.findall(_NUMBER, match[1], re.IGNORECASE)
    }


def _citable(finding: FindingToExplain) -> set[str]:
    return articles(" ".join(finding.references)) | articles(finding.text)


# --- Contrôles ---------------------------------------------------------------------------


def refusals(draft: Draft, req: ExplanationRequest) -> list[str]:
    """Motifs de refus d'une explication ; vide : acceptée."""
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
        cited = articles(entry.text) - _citable(finding)
        if cited:
            reasons.append(
                prefix + "article cité hors des références retenues pour la clause "
                f"{finding.kind} : {', '.join(sorted(cited))}"
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

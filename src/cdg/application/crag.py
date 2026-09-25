"""CRAG : recherche corrective dans le corpus, en fonctions pures (nœuds du sous-graphe).

Une recherche par type de clause du domaine (`DOMAIN_KINDS`). Pour chacune : retrieve,
puis grade (juge de pertinence, modèle léger) ; generate si un extrait est pertinent,
sinon rewrite et nouvelle recherche, dans la limite de `crag.max_passes`. Le sous-graphe,
compilé sans checkpointer dans `adapters/langgraph/orchestrator.py`, traite une clause ;
`per_clause` l'applique à chaque type du domaine et `combine` rassemble les résultats.

- La requête d'une clause est construite à partir de son seul type, de sa valeur et de sa
  catégorie, jamais de sa citation : aucun texte du contrat n'atteint le CRAG.
- generate n'appelle aucun LLM : il rassemble les références retenues pour la clause.
  Une référence dont la version a expiré à la date d'analyse est signalée et jamais
  retenue.
- Le domaine est INSUFFISANT dès qu'une de ses clauses n'a aucune référence en vigueur :
  l'issue la plus prudente l'emporte.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, StringConstraints

from cdg.application.deps import RetrievalResult
from cdg.domain.corpus import expired
from cdg.domain.models import (
    CLAUSE_CATEGORIES,
    DOMAIN_KINDS,
    Clause,
    ClauseRetrieval,
    Domain,
    RetrievalTrace,
    Usage,
)
from cdg.ports.llm import LLMOutputError, LLMProvider
from cdg.ports.retriever import Passage, Retriever

PROMPTS = Path(__file__).parent / "prompts"
_GRADE_SYSTEM = (PROMPTS / "crag_grade_system.md").read_text(encoding="utf-8")
_REWRITE_SYSTEM = (PROMPTS / "crag_rewrite_system.md").read_text(encoding="utf-8")

# type de clause -> (sujet, unité de `value`, sens d'une valeur nulle) ; voir la spec,
# « Règles par domaine »
_SUBJECTS = {
    "responsabilite_acheteur": (
        "plafond de responsabilité de l'acheteur",
        "% du montant annuel",
        "illimitée",
    ),
    "responsabilite_fournisseur": (
        "plafond de responsabilité du fournisseur",
        "% du montant annuel",
        "illimitée",
    ),
    "revision_prix": ("révision du prix", "%", "non plafonnée"),
    "penalites_execution": (
        "pénalités d'exécution à la charge du fournisseur",
        "% du montant du contrat",
        "non plafonnées",
    ),
    "delai_paiement": ("délai de paiement par l'acheteur", "jours", "non chiffré"),
    "duree_engagement": ("durée d'engagement", "mois", "non chiffrée"),
    "preavis_resiliation": ("préavis de résiliation", "mois", "non chiffré"),
    "donnees_personnelles": ("traitement de données à caractère personnel", None, None),
    "accord_traitement_donnees": ("accord de sous-traitance des données personnelles", None, None),
    "transfert_hors_ue": (
        "transfert de données personnelles hors de l'Union européenne",
        None,
        None,
    ),
}


# libellés des catégories dans les requêtes ; par défaut, le nom sans soulignés
_CATEGORY_LABELS = {c: c.replace("_", " ") for c in CLAUSE_CATEGORIES} | {
    "date_facture": "date de facture",
    "facture_periodique": "après une facture périodique",
}


class ClauseResult(BaseModel):
    """Résultat du CRAG pour une clause : son résumé, ses constats, sa consommation."""

    trace: ClauseRetrieval
    findings: list[str]
    usage: list[Usage]


class CragState(TypedDict, total=False):
    domain: Domain
    clause: Clause  # la clause recherchée : une requête par type de clause
    analysis_date: date
    query: str  # requête courante
    queries: list[str]  # requêtes essayées, dans l'ordre
    attempts: int  # recherches effectuées
    docs: list[Passage]  # extraits de la dernière recherche
    relevant: list[Passage]  # extraits jugés pertinents
    route: Literal["rewrite", "generate"]  # écrite par grade, lue par l'arête
    usage: list[Usage]
    result: ClauseResult


class GradeOutput(BaseModel):
    relevant: list[int]  # numéros des extraits pertinents


class RewriteOutput(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _describe(clause: Clause) -> str:
    subject, unit, none_label = _SUBJECTS[clause.kind]
    if not clause.present:
        detail = "clause absente"
    elif clause.value is None:
        # catégorie seule (transfert), sinon sens d'une valeur nulle
        detail = _CATEGORY_LABELS.get(clause.category) or none_label or "clause présente"
    else:
        detail = f"{clause.value:g} {unit}"
        if clause.category is not None:  # point de départ d'un délai de paiement
            detail += f" {_CATEGORY_LABELS[clause.category]}"
    return f"{subject} : {detail}"


def clause_query(domain: Domain, clause: Clause) -> str:
    """Requête d'une clause : son type, sa valeur et sa catégorie, jamais sa citation."""
    return f"{domain} : {_describe(clause)}"


def start(domain: Domain, clause: Clause, analysis_date: date) -> CragState:
    return {
        "domain": domain,
        "clause": clause,
        "analysis_date": analysis_date,
        "query": clause_query(domain, clause),
        "queries": [],
        "attempts": 0,
        "usage": [],
    }


def retrieve(state: CragState, retriever: Retriever, top_k: int) -> dict:
    docs = retriever.search(state["domain"], state["query"], k=top_k)
    return {
        "docs": docs,
        "attempts": state["attempts"] + 1,
        "queries": [*state["queries"], state["query"]],
    }


def _grade_message(state: CragState) -> str:
    blocks = [
        f"<<<EXTRAIT {n}>>>\n{p.reference}\n{p.text}\n<<<FIN EXTRAIT {n}>>>"
        for n, p in enumerate(state["docs"], start=1)
    ]
    subject = _SUBJECTS[state["clause"].kind][0]
    header = f"Domaine : {state['domain']}\nClause : {subject}\nRecherche : {state['query']}"
    return header + "\n\n" + "\n\n".join(blocks)


def _node(state: CragState, step: str) -> str:
    return f"crag_{step}:{state['domain']}:{state['clause'].kind}"


def grade(state: CragState, llm: LLMProvider, max_passes: int) -> dict:
    """Juge de pertinence (modèle léger) ; sans extrait, aucun appel au juge."""
    docs, usage, relevant = state["docs"], state["usage"], []
    if docs:
        output, used = llm.structured(
            tier="light",
            system=_GRADE_SYSTEM,
            user=_grade_message(state),
            schema=GradeOutput,
            node=_node(state, "grade"),
        )
        numbers = output.relevant
        if len(set(numbers)) != len(numbers) or not all(1 <= n <= len(docs) for n in numbers):
            raise LLMOutputError(
                f"juge CRAG ({state['domain']}, {state['clause'].kind}) : numéro d'extrait "
                "invalide ou répété : "
                f"{numbers}, {len(docs)} extraits"
            )
        relevant = [docs[n - 1] for n in sorted(numbers)]
        usage = [*usage, used]
    route = "generate" if relevant or state["attempts"] >= max_passes else "rewrite"
    return {"relevant": relevant, "route": route, "usage": usage}


def rewrite(state: CragState, llm: LLMProvider) -> dict:
    """Nouvelle requête (modèle léger), à partir des seules requêtes déjà essayées."""
    tried = "\n".join(f"- {q}" for q in state["queries"])
    subject = _SUBJECTS[state["clause"].kind][0]
    output, used = llm.structured(
        tier="light",
        system=_REWRITE_SYSTEM,
        user=f"Domaine : {state['domain']}\nClause : {subject}\nRequêtes déjà essayées :\n{tried}",
        schema=RewriteOutput,
        node=_node(state, "rewrite"),
    )
    return {"query": output.query, "usage": [*state["usage"], used]}


def generate(state: CragState) -> dict:
    """Références retenues pour la clause, sans LLM ; les versions expirées sont signalées,
    jamais retenues."""
    on, relevant, kind = state["analysis_date"], state.get("relevant", []), state["clause"].kind
    valid = [p for p in relevant if not expired(p.valid_until, on)]
    retained = list(dict.fromkeys(p.reference for p in valid))
    old: dict[str, date] = {}
    for p in relevant:
        if expired(p.valid_until, on):
            old.setdefault(p.reference, p.valid_until)
    findings = [
        f"référence expirée à la date d'analyse ({on.isoformat()}) : {reference}, "
        f"version en vigueur jusqu'au {until.isoformat()}, non retenue ({kind})"
        for reference, until in old.items()
    ]
    if old and not valid:
        findings.append(
            f"références pertinentes toutes expirées pour la clause {kind} : elles ne "
            "peuvent pas justifier seules le verdict"
        )
    trace = ClauseRetrieval(
        kind=kind,
        queries=state["queries"],
        passes=state["attempts"],
        retained=retained,
        expired=list(old),
    )
    return {"result": ClauseResult(trace=trace, findings=findings, usage=state["usage"])}


def combine(results: list[ClauseResult]) -> RetrievalResult:
    """Résultat du domaine : INSUFFISANT dès qu'une clause n'a aucune référence en vigueur."""
    findings = [f for r in results for f in r.findings]
    lacking = [r.trace.kind for r in results if not r.trace.retained]
    findings += [f"aucune référence en vigueur retenue pour la clause {kind}" for kind in lacking]
    return RetrievalResult(
        status="INSUFFISANT" if lacking else "OK",
        evidence_ids=list(dict.fromkeys(ref for r in results for ref in r.trace.retained)),
        usage=[u for r in results for u in r.usage],
        findings=findings,
        trace=RetrievalTrace(clauses=[r.trace for r in results]),
    )


def per_clause(
    domain: Domain,
    clauses: list[Clause],
    analysis_date: date,
    run_clause: Callable[[CragState], ClauseResult],
) -> RetrievalResult:
    """Une recherche par type de clause du domaine, dans l'ordre de `DOMAIN_KINDS`."""
    by_kind = {c.kind: c for c in clauses}
    return combine(
        [run_clause(start(domain, by_kind[kind], analysis_date)) for kind in DOMAIN_KINDS[domain]]
    )

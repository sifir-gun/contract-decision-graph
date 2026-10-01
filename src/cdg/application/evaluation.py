"""Évaluation de la recherche seule, sans LLM (chantier « qualité de la recherche »).

Jeu d'évaluation (`data/evaluation/recherche.yaml`) : une entrée par requête du CRAG que
donnent les constats du jeu de démonstration, avec les références qui justifient vraiment
le constat. Elles sont choisies à la lecture du texte de chaque article ou fiche, jamais
d'après le rattachement déclaré (manifeste, en-tête des fiches), que la recherche filtrée
utilise déjà : sinon la mesure avec filtre serait circulaire.

Seule la première requête d'une clause est mesurée : elle est écrite par le code
(`crag.clause_query`) ; la réécriture appelle le LLM. Deux modes : avec le filtre du CRAG
(domaine et rattachement déclaré, port `Retriever`) et sans filtre (tout le corpus, port
`CorpusSearch`) ; deux portées : toutes les références, et les articles de loi seuls.
Les métriques sont des fonctions pures de `domain/evaluation.py`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cdg.application import demo_set, ingestion
from cdg.application.crag import clause_query
from cdg.domain.config import DecisionConfig
from cdg.domain.evaluation import first_ranks, mean, scores, top_references
from cdg.domain.models import DOMAINS, Clause, Domain
from cdg.domain.numeric import rounded
from cdg.domain.rules import RULES
from cdg.ports.retriever import CorpusSearch, Retriever

EVALUATION = (
    Path(__file__).resolve().parents[3] / "data" / "evaluation" / "recherche.yaml"
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedReference(_Strict):
    """Référence qui justifie le constat : article de loi ou fiche du projet."""

    reference: str
    type: Literal["article", "fiche"]
    justification: str  # pourquoi le texte de la référence justifie le constat
    quote: str  # extrait mot pour mot du texte de la référence


class RejectedReference(_Strict):
    """Référence proche, écartée : piège lexical ou choix discutable, motivé."""

    reference: str
    reason: str


class EvaluationQuery(_Strict):
    domain: Domain
    kind: str
    query: str  # requête du CRAG, recalculée et comparée par les tests
    contracts: tuple[str, ...]  # contrats du jeu dont un constat donne cette requête
    expected: tuple[ExpectedReference, ...]
    rejected: tuple[RejectedReference, ...] = Field(default=())


def load_queries(path: Path = EVALUATION) -> list[EvaluationQuery]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [EvaluationQuery.model_validate(item) for item in data["queries"]]


@dataclass(frozen=True)
class DemoQuery:
    domain: Domain
    kind: str
    query: str
    contracts: tuple[str, ...]


def demo_clause(contract_id: str, kind: str) -> Clause:
    """Clause attendue d'un contrat du jeu de démonstration."""
    _, contracts = demo_set.load()
    return next(c for c in contracts[contract_id].clauses if c.kind == kind)


def demo_queries(config: DecisionConfig) -> list[DemoQuery]:
    """Requêtes du CRAG pour les constats du jeu de démonstration, comme dans l'analyste :
    règles du domaine sur toutes les clauses attendues, puis une requête par clause qui
    porte un constat. Une requête donnée par plusieurs contrats est comptée une fois ; un
    contrat rejeté à l'entrée n'atteint pas les analystes."""
    _, contracts = demo_set.load()
    found: dict[tuple[Domain, str, str], list[str]] = {}
    for contract in contracts.values():
        clauses = list(contract.clauses)
        if not clauses:
            continue
        for domain in DOMAINS:
            for kind in RULES[domain](clauses, config).kinds_to_justify():
                clause = next(c for c in clauses if c.kind == kind)
                key = (domain, kind, clause_query(domain, clause))
                found.setdefault(key, []).append(contract.id)
    return [
        DemoQuery(domain, kind, query, tuple(ids))
        for (domain, kind, query), ids in found.items()
    ]


# --- mesure --------------------------------------------------------------------------

# rangs mesurés par défaut : 4, la valeur du CRAG (crag.top_k) ; 20, celle de l'article
# d'Anthropic ; constante de la mesure, pas un réglage de la décision
DEFAULT_KS = (1, 2, 4, 8, 20)
Mode = Literal["filtre", "sans_filtre"]
MODES: tuple[Mode, ...] = ("filtre", "sans_filtre")
SCOPES = ("toutes", "articles")


class IndexMismatch(Exception):
    """Corpus indexé différent des fichiers du corpus : la mesure serait fausse."""


class EvaluationSetOutdated(Exception):
    """Jeu d'évaluation qui ne couvre plus exactement les constats du jeu de
    démonstration : une requête sans références attendues ne serait pas mesurée."""


def check_index(corpus: CorpusSearch, max_words: int, probe: str) -> int:
    """Extraits indexés du modèle, comparés un à un (référence, texte) aux extraits que
    donnent les fichiers du corpus ; leur nombre, s'ils sont identiques."""
    wanted = {
        (meta["reference"], meta["text"])
        for meta, _, _ in ingestion.pending_chunks(max_words)
    }
    found = [
        (p.reference, p.text)
        for p in corpus.search_unfiltered(probe, k=len(wanted) + 1)
    ]
    indexed = set(found)
    if len(found) != len(indexed) or indexed != wanted:
        raise IndexMismatch(
            f"corpus indexé différent des fichiers ({len(wanted - indexed)} extrait(s) "
            f"manquant(s), {len(indexed - wanted)} en trop) : relancer ingest"
        )
    return len(wanted)


def _check_set(queries: Sequence[EvaluationQuery], config: DecisionConfig) -> None:
    def key(q: EvaluationQuery | DemoQuery) -> tuple[str, str, str, tuple[str, ...]]:
        return q.domain, q.kind, q.query, q.contracts

    demo, jeu = {key(q) for q in demo_queries(config)}, {key(q) for q in queries}
    if demo != jeu:
        missing = sorted(q[2] for q in demo - jeu)
        extra = sorted(q[2] for q in jeu - demo)
        raise EvaluationSetOutdated(
            "jeu d'évaluation à mettre à jour (data/evaluation/recherche.yaml) : "
            f"requêtes des constats absentes du jeu {missing} ; requêtes du jeu sans "
            f"constat {extra}"
        )


def _mode(
    expected: dict[str, list[str]],
    references: list[str],
    ks: Sequence[int],
    fiches: set[str],
) -> dict[str, Any]:
    """Scores d'une requête dans un mode, par portée et par rang, à partir des références
    des extraits rendus au rang le plus grand."""
    result: dict[str, Any] = {
        "rendues": top_references(references, max(ks)),
        "rangs": first_ranks(expected["toutes"], references),
    }
    for scope in SCOPES:
        result[scope] = {}
        for k in ks:
            found = top_references(references, k)
            if scope == "articles":
                found = [r for r in found if r not in fiches]
            s = scores(expected[scope], found)
            result[scope][k] = {"rappel": s.recall, "precision": s.precision}
    return result


def measure(
    queries: Sequence[EvaluationQuery],
    retriever: Retriever,
    corpus: CorpusSearch,
    ks: Sequence[int],
) -> dict[str, Any]:
    """Rappel et précision de chaque requête aux rangs `ks`, avec et sans filtre, pour
    toutes les références et pour les articles seuls ; puis leurs moyennes. Une recherche
    par requête et par mode, au rang le plus grand : la recherche exacte rend les mêmes
    premiers extraits à tout rang."""
    fiches = {ingestion.fiche_reference(f) for f in ingestion.load_fiches()}
    k_max = max(ks)
    rows: list[dict[str, Any]] = []
    for q in queries:
        expected = {
            "toutes": [r.reference for r in q.expected],
            "articles": [r.reference for r in q.expected if r.type == "article"],
        }
        found = {
            "filtre": retriever.search(q.domain, q.query, kind=q.kind, k=k_max),
            "sans_filtre": corpus.search_unfiltered(q.query, k=k_max),
        }
        row: dict[str, Any] = {
            "domaine": q.domain,
            "clause": q.kind,
            "requete": q.query,
            "contrats": list(q.contracts),
            "attendues": expected["toutes"],
        }
        for mode in MODES:
            references = [p.reference for p in found[mode]]
            row[mode] = _mode(expected, references, ks, fiches)
        rows.append(row)
    averages: dict[str, Any] = {}
    for mode in MODES:
        averages[mode] = {}
        for scope in SCOPES:
            averages[mode][scope] = {}
            for k in ks:
                recall = mean([r[mode][scope][k]["rappel"] for r in rows])
                averages[mode][scope][k] = {
                    "rappel": recall,
                    "precision": mean([r[mode][scope][k]["precision"] for r in rows]),
                    "echec": rounded(1 - recall),
                }
    return {"moyennes": averages, "par_requete": rows}


def run(
    config: DecisionConfig,
    retriever: Retriever,
    corpus: CorpusSearch,
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, Any]:
    """Mesure de la recherche seule (commande `mesure-recherche`) : le jeu couvre-t-il
    exactement les constats du jeu de démonstration, le corpus indexé est-il celui des
    fichiers, puis la mesure. Tout écart est une erreur explicite."""
    queries = load_queries()
    _check_set(queries, config)
    chunks = check_index(corpus, config.corpus.chunk_max_words, queries[0].query)
    ranks = sorted(set(ks))
    return {
        "extraits": chunks,
        "requetes": len(queries),
        "k": ranks,
        **measure(queries, retriever, corpus, ranks),
    }

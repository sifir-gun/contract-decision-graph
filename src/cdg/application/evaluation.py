"""Évaluation de la recherche seule, sans LLM (chantier « qualité de la recherche »).

Jeu d'évaluation (`data/evaluation/recherche.yaml`) : une entrée par requête du CRAG que
donnent les constats du jeu de démonstration, avec les références qui justifient vraiment
le constat. Elles sont choisies à la lecture du texte de chaque article ou fiche, jamais
d'après le rattachement déclaré (manifeste, en-tête des fiches), que la recherche filtrée
utilise déjà : sinon la mesure avec filtre serait circulaire.

Seule la première requête d'une clause est mesurée : elle est écrite par le code
(`crag.clause_query`) ; la réécriture appelle le LLM.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cdg.application import demo_set
from cdg.application.crag import clause_query
from cdg.domain.config import DecisionConfig
from cdg.domain.models import DOMAINS, Clause, Domain
from cdg.domain.rules import RULES

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

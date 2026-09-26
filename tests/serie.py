"""Série réelle sur le jeu de démonstration (J5) : mesures en fonctions pures, testées sans
LLM (`test_serie.py`) ; la série elle-même est dans `test_llm_jeu.py`.

- issue d'un essai comparée à l'issue attendue (`attendus.yaml`) : conforme, plus
  prudente, plus favorable (l'invariant : jamais), ou autre écart ;
- écarts d'extraction par clause ;
- coût aux tarifs publiés, latences par étape, durée réelle de l'étape des analystes ;
- résumé de la série : concordance, stabilité, coût et durée par contrat.
"""

import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from statistics import median
from typing import Any

from cdg.domain.models import Clause, Usage

# tarifs publiés par Mistral, dollars par million de tokens (entrée, sortie), relevés le
# 26/09/2026 sur mistral.ai/pricing/api : Mistral Small 4 (mistral-small-2603) et
# Ministral 3 8B (ministral-8b-2512). Hors configuration : ce ne sont pas des réglages de
# l'analyse, et ils changeraient son empreinte.
PRICES_USD_PER_MTOKEN: dict[str, tuple[float, float]] = {
    "mistral-small-2603": (0.15, 0.60),
    "ministral-8b-2512": (0.15, 0.15),
}
# ordre de faveur des décisions finales ; une revue humaine en attente n'accorde rien
RANK = {"GO": 2, "GO_RESERVES": 1, "NO_GO": 0, None: 0}

# limite du compte pour le modèle principal (console Mistral, 25/09/2026) ; l'API ne compte
# que les tokens consommés, max_tokens n'est pas réservé (mesuré le même jour)
ACCOUNT_TOKENS_PER_MINUTE = 20_000


class Pacer:
    """Cadence des essais : après un essai qui a consommé t tokens, attendre
    t × 60 / limite secondes avant le suivant. Pas une relance : seulement un espacement,
    pour ne pas provoquer soi-même un 429."""

    def __init__(self, tokens_per_minute: int):
        self.tokens_per_minute, self.next_at = tokens_per_minute, 0.0

    def wait(self) -> None:
        delay = self.next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def consumed(self, tokens: int) -> None:
        self.next_at = time.monotonic() + tokens * 60 / self.tokens_per_minute


@dataclass(frozen=True)
class Outcome:
    """Issue du gate : décision proposée, revue humaine ou non, décision finale sans
    revue ; ou rejet à l'entrée."""

    proposed: str | None
    review: bool
    final: str | None
    rejected: bool = False


def outcome_of(status: dict[str, Any]) -> Outcome:
    """Issue d'un statut de thread (`thread_status`), avant toute reprise humaine."""
    if status.get("reject_reason"):
        return Outcome(None, False, None, rejected=True)
    if status["statut"] not in ("termine", "suspendu"):
        raise ValueError(f"statut inattendu : {status['statut']}")
    review = status["statut"] == "suspendu"
    return Outcome(
        status.get("proposed_decision"),
        review,
        None if review else status.get("final_decision"),
    )


def expected_outcome(expected: dict[str, Any]) -> Outcome:
    if "reject_reason" in expected:
        return Outcome(None, False, None, rejected=True)
    review = "human" in expected
    return Outcome(
        expected["proposed_decision"],
        review,
        None if review else expected["final_decision"],
    )


def classify(expected: dict[str, Any], obtained: Outcome) -> str:
    """conforme ; plus_favorable : décision automatique plus favorable que la décision
    finale attendue (l'invariant de la série) ; plus_prudente : revue humaine là où une
    décision automatique était attendue, ou décision automatique moins favorable ; ecart :
    le reste (revue sautée à décision égale, autre proposition en revue)."""
    wanted = expected_outcome(expected)
    if obtained == wanted:
        return "conforme"
    automatic = not obtained.review and not obtained.rejected
    target = RANK[expected.get("final_decision")]
    if automatic and RANK[obtained.final] > target:
        return "plus_favorable"
    if obtained.review and not wanted.review:
        return "plus_prudente"
    automatic_expected = not wanted.review and not wanted.rejected
    if automatic and automatic_expected and RANK[obtained.final] < target:
        return "plus_prudente"
    return "ecart"


def describe(outcome: Outcome) -> str:
    if outcome.rejected:
        return "rejet"
    if outcome.review:
        return f"{outcome.proposed}, revue humaine"
    return f"{outcome.final}, automatique"


def clause_gaps(obtained: list[Clause], expected: list[Clause]) -> list[str]:
    """Clauses dont la présence, la valeur ou la catégorie s'écarte de l'attendu."""
    got = {c.kind: c for c in obtained}
    gaps = []
    for wanted in expected:
        want = (wanted.present, wanted.value, wanted.category)
        clause = got.get(wanted.kind)
        if clause is None:
            gaps.append(f"{wanted.kind} : attendu {want}, obtenu non rendu")
            continue
        have = (clause.present, clause.value, clause.category)
        if have != want:
            gaps.append(f"{wanted.kind} : attendu {want}, obtenu {have}")
    return gaps


def cost_usd(usage: list[Usage]) -> float:
    total = 0.0
    for u in usage:
        if u.model not in PRICES_USD_PER_MTOKEN:
            raise ValueError(
                f"tarif inconnu pour {u.model} : l'ajouter, avec sa source"
            )
        price_in, price_out = PRICES_USD_PER_MTOKEN[u.model]
        total += (u.tokens_in * price_in + u.tokens_out * price_out) / 1_000_000
    return total


def tokens_by_model(usage: list[Usage]) -> dict[str, int]:
    tokens: Counter[str] = Counter()
    for u in usage:
        tokens[u.model] += u.tokens_in + u.tokens_out
    return dict(tokens)


def step_latencies(usage: list[Usage]) -> dict[str, Any]:
    """Latences des appels LLM par étape ; celles du CRAG par domaine d'analyste (nœuds
    `crag_<étape>:<domaine>:<clause>`)."""
    by_domain: Counter[str] = Counter()
    for u in usage:
        if u.node.startswith("crag_"):
            by_domain[u.node.split(":")[1]] += u.latency_ms
    return {
        "extraction_ms": sum(
            u.latency_ms for u in usage if u.node == "extract_clauses"
        ),
        "explication_ms": sum(u.latency_ms for u in usage if u.node == "explain"),
        "analystes_llm_ms": dict(by_domain),
    }


def analysts_wall_ms(steps: list[tuple[str, tuple[str, ...]]]) -> int | None:
    """Durée réelle de l'étape des analystes, lancés en parallèle : du checkpoint qui les
    lance au suivant. `steps` : (horodatage du checkpoint, nœuds suivants), dans l'ordre
    chronologique ; None si aucun analyste n'a tourné."""
    for (start, following), (end, _) in pairwise(steps):
        if "analyst" in following:
            elapsed = datetime.fromisoformat(end) - datetime.fromisoformat(start)
            return round(elapsed.total_seconds() * 1000)
    return None


def extraction_refusals(
    steps: list[tuple[tuple[str, ...], list[str] | None]],
    failure_report: dict[str, Any] | None,
) -> list[list[str]]:
    """Motifs de refus de chaque extraction refusée, dans l'ordre : le retour ciblé posé
    avant chaque nouvelle extraction, puis les problèmes du dernier essai s'il a été
    escaladé. `steps` : (nœuds suivants, retour ciblé dans l'état), dans l'ordre
    chronologique des checkpoints."""
    refusals = [
        feedback
        for following, feedback in steps
        if "extract_clauses" in following and feedback
    ]
    if failure_report and failure_report.get("stage") == "extraction":
        refusals.append(failure_report["problems"])
    return refusals


def summarize(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Résumé d'une série : par contrat, classement, issues (stabilité), essais avec un
    écart d'extraction, coût et durée ; au total, classement et coût."""
    by_contract: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_contract.setdefault(line["contrat"], []).append(line)
    contracts = {
        cid: {
            "essais": len(runs),
            "classement": dict(Counter(r["classement"] for r in runs)),
            "issues": dict(Counter(r["issue"] for r in runs)),
            "essais_avec_ecart_d_extraction": sum(
                bool(r["ecarts_extraction"]) for r in runs
            ),
            "extractions_refusees": sum(
                len(r.get("refus_extraction", [])) for r in runs
            ),
            "cout_median_usd": median(r["cout_usd"] for r in runs),
            "duree_mediane_s": median(r["duree_ms"] for r in runs) / 1000,
            "duree_max_s": max(r["duree_ms"] for r in runs) / 1000,
        }
        for cid, runs in by_contract.items()
    }
    return {
        "essais": len(lines),
        "classement": dict(Counter(line["classement"] for line in lines)),
        "cout_total_usd": sum(line["cout_usd"] for line in lines),
        "contrats": contracts,
    }

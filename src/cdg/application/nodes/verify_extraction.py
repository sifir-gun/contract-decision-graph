"""verify_extraction : écrit `route` (analysts, extract_clauses ou human_review).

Vérifie par code, sans LLM, que chaque citation d'une clause présente figure mot pour
mot dans le texte masqué de l'état, et que les `REQUIRED_KINDS` sont rendus une fois
chacun. Sinon : nouvel essai avec retour ciblé, puis ESCALADE après le dernier.
"""

import re
import unicodedata
from collections import Counter

from cdg.application.failures import escalate
from cdg.domain.config import DecisionConfig
from cdg.domain.state import CATEGORY_KINDS, REQUIRED_KINDS, ContractState

# typographie équivalente : apostrophes, guillemets, tirets, espaces insécables
_TYPOGRAPHY = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": "-",
        "‑": "-",
    }
)


def normalize(text: str) -> str:
    """NFKC, typographie unifiée, espaces réduits ; la casse est conservée."""
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)
    return re.sub(r"\s+", " ", text).strip()


def problems_of(raw_text: str, clauses: list) -> list[str]:
    text = normalize(raw_text)
    counts = Counter(c.kind for c in clauses)
    problems = [f"clause manquante: {k}" for k in REQUIRED_KINDS if counts[k] == 0]
    problems += [f"clause en double: {k}" for k in counts if counts[k] > 1]
    problems += [f"type de clause inconnu: {k}" for k in counts if k not in REQUIRED_KINDS]
    problems += [
        f"catégorie manquante: {c.kind}"
        for c in clauses
        if c.kind in CATEGORY_KINDS and c.present and c.category is None
    ]
    problems += [
        f"catégorie inattendue: {c.kind}"
        for c in clauses
        if c.kind not in CATEGORY_KINDS and c.category is not None
    ]
    problems += [
        f"citation introuvable: {c.kind}"
        for c in clauses
        if c.present and normalize(c.quote) not in text
    ]
    return problems


def verify_extraction(state: ContractState, decision_config: DecisionConfig) -> dict:
    failures = state.get("failures", [])
    if failures:  # extraction en échec (garde de l'orchestrateur) : rien à vérifier
        return escalate(failures)
    problems = problems_of(state["raw_text"], state["clauses"])
    if not problems:
        return {"route": "analysts"}
    attempts = state["extraction_attempts"]  # incrémenté par extract_clauses
    if attempts < decision_config.extraction.max_attempts:
        return {"route": "extract_clauses", "extraction_feedback": problems}
    return {
        "route": "human_review",
        "proposed_decision": "ESCALADE",
        "failure_report": {"stage": "extraction", "attempts": attempts, "problems": problems},
    }

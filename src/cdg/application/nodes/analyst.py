"""analyst : règles du domaine en Python pur, puis CRAG sur les seules clauses qui portent
un constat, puis justification par le corpus. Reçoit un AnalystInput via Send."""

from typing import Any

from cdg.application.deps import Crag, RetrievalResult
from cdg.application.state import AnalystInput
from cdg.domain.config import DecisionConfig
from cdg.domain.justification import justify
from cdg.domain.rules import RULES


def analyst(inp: AnalystInput, crag: Crag, decision_config: DecisionConfig) -> dict[str, Any]:
    domain, clauses = inp["domain"], inp["clauses"]
    assessment = RULES[domain](clauses, decision_config)
    to_justify = set(assessment.kinds_to_justify())
    retrieval = RetrievalResult.model_validate(
        crag(domain, [c for c in clauses if c.kind in to_justify], inp["analysis_date"])
    )
    verdict = justify(assessment, retrieval.trace, retrieval.findings)
    return {"verdicts": [verdict], "usage": retrieval.usage}

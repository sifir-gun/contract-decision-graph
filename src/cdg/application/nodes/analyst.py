"""analyst : CRAG du domaine puis règles en Python pur. Reçoit un AnalystInput via Send."""

from cdg.application.deps import Crag, RetrievalResult
from cdg.domain.config import DecisionConfig
from cdg.domain.rules import RULES
from cdg.domain.state import AgentVerdict, AnalystInput


def analyst(inp: AnalystInput, crag: Crag, decision_config: DecisionConfig) -> dict:
    domain, clauses = inp["domain"], inp["clauses"]
    retrieval = RetrievalResult.model_validate(crag(domain, clauses))
    verdict = RULES[domain](clauses, retrieval.status, decision_config)
    verdict = AgentVerdict.model_validate(
        verdict.model_dump() | {"evidence_ids": retrieval.evidence_ids}
    )
    return {"verdicts": [verdict], "usage": retrieval.usage}

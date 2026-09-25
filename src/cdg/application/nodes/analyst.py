"""analyst : CRAG du domaine puis règles en Python pur. Reçoit un AnalystInput via Send."""

from cdg.application.deps import Crag, RetrievalResult
from cdg.application.state import AnalystInput
from cdg.domain.config import DecisionConfig
from cdg.domain.models import AgentVerdict
from cdg.domain.rules import RULES


def analyst(inp: AnalystInput, crag: Crag, decision_config: DecisionConfig) -> dict:
    domain, clauses = inp["domain"], inp["clauses"]
    retrieval = RetrievalResult.model_validate(crag(domain, clauses, inp["analysis_date"]))
    verdict = RULES[domain](clauses, retrieval.status, decision_config)
    verdict = AgentVerdict.model_validate(
        verdict.model_dump()
        | {
            "findings": [*verdict.findings, *retrieval.findings],  # règles, puis CRAG
            "evidence_ids": retrieval.evidence_ids,
            "retrieval": retrieval.trace.model_dump() if retrieval.trace else None,
        }
    )
    return {"verdicts": [verdict], "usage": retrieval.usage}

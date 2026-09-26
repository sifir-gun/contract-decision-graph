"""Justification des constats par le corpus : statut de récupération et verdict du domaine.

Le corpus sert à justifier un constat ; une clause qui ne déclenche aucune règle n'a rien à
justifier. D'où, après les règles et le CRAG (sur les seules clauses qui portent un constat) :
- une clause dont le constat bloque ou pénalise exige au moins une référence en vigueur ;
  sans elle, le domaine est INSUFFISANT, avec un constat qui la nomme. Une clause à justifier
  absente du résumé du CRAG compte comme non justifiée : l'issue la plus prudente l'emporte ;
- un constat d'information sans référence est signalé, sans effet sur le statut ;
- une clause sans constat n'exige aucune référence.
"""

from cdg.domain.models import AgentVerdict, RetrievalTrace
from cdg.domain.rules import Assessment


def justify(
    assessment: Assessment, trace: RetrievalTrace, crag_findings: list[str]
) -> AgentVerdict:
    """Verdict du domaine : constats des règles, puis du CRAG, puis de la justification."""
    retained = {c.kind: c.retained for c in trace.clauses}
    required = assessment.kinds_requiring_reference()
    lacking = [kind for kind in required if not retained.get(kind)]
    unsupported = [
        kind
        for kind in assessment.kinds_to_justify()
        if kind not in required and not retained.get(kind)
    ]
    findings = [f.text for f in assessment.findings] + crag_findings
    findings += [
        "référentiel insuffisant : aucune référence en vigueur pour justifier le constat de "
        f"la clause {kind}"
        for kind in lacking
    ]
    findings += [
        f"constat de la clause {kind} sans référence en vigueur : information seule, "
        "sans effet sur le statut"
        for kind in unsupported
    ]
    return AgentVerdict(
        domain=assessment.domain,
        score=assessment.score,
        hard_block=assessment.hard_block,
        findings=findings,
        evidence_ids=list(dict.fromkeys(ref for c in trace.clauses for ref in c.retained)),
        retrieval_status="INSUFFISANT" if lacking else "OK",
        retrieval=trace,
    )

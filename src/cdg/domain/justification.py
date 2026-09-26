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

LACKING = (
    "référentiel insuffisant : aucune référence en vigueur pour justifier le constat de "
    "la clause {kind}"
)
UNSUPPORTED = (
    "constat de la clause {kind} sans référence en vigueur : information seule, sans "
    "effet sur le statut"
)


def justify(assessment: Assessment, trace: RetrievalTrace) -> AgentVerdict:
    """Verdict du domaine : constats des règles, puis du CRAG (portés par son résumé),
    puis de la justification, chacun rattaché à sa clause (`finding_kinds`)."""
    retained = {c.kind: c.retained for c in trace.clauses}
    required = assessment.kinds_requiring_reference()
    lacking = [kind for kind in required if not retained.get(kind)]
    unsupported = [
        kind
        for kind in assessment.kinds_to_justify()
        if kind not in required and not retained.get(kind)
    ]
    # chaque constat avec sa clause ; ceux du CRAG sont propres à la recherche
    attached: list[tuple[str | None, str]] = [
        (f.kind, f.text) for f in assessment.findings
    ]
    attached += [(None, text) for text in trace.findings]
    attached += [(kind, LACKING.format(kind=kind)) for kind in lacking]
    attached += [(kind, UNSUPPORTED.format(kind=kind)) for kind in unsupported]
    return AgentVerdict(
        domain=assessment.domain,
        score=assessment.score,
        hard_block=assessment.hard_block,
        findings=[text for _, text in attached],
        finding_kinds=[kind for kind, _ in attached],
        evidence_ids=list(
            dict.fromkeys(ref for c in trace.clauses for ref in c.retained)
        ),
        retrieval_status="INSUFFISANT" if lacking else "OK",
        retrieval=trace,
    )

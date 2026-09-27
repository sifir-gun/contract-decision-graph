"""Références du mode démonstration (port `Crag`), sans recherche vectorielle ni juge LLM.

Pour chaque clause qui porte un constat, les extraits du corpus dont la source déclare ce
type de clause (manifeste, en-tête des fiches) sont tenus pour pertinents ; l'étape
`generate` du CRAG, la vraie, en tire les références retenues à la date d'analyse et
signale les versions expirées. Là où le juge du CRAG trie les extraits trouvés, la
démonstration retient toutes les sources déclarées : ses références sont plus nombreuses.
"""

from datetime import date

from cdg.application import crag, ingestion
from cdg.application.deps import RetrievalResult
from cdg.domain.corpus import by_domain
from cdg.domain.models import Clause, Domain
from cdg.ports.retriever import Passage


class DeclaredCrag:
    def __init__(self, max_words: int):
        self._passages = [
            Passage(
                id=index,
                domain=domain,
                source_id=meta["source_id"],
                reference=meta["reference"],
                text=meta["text"],
                distance=0.0,
                valid_until=meta["valid_until"],
                note=meta.get("note"),
                kinds=kinds,
            )
            for index, (meta, _, declared) in enumerate(
                ingestion.pending_chunks(max_words)
            )
            for domain, kinds in by_domain(declared)
        ]

    def __call__(
        self, domain: Domain, clauses: list[Clause], analysis_date: date
    ) -> RetrievalResult:
        results = []
        for clause in clauses:
            relevant = [
                p
                for p in self._passages
                if p.domain == domain and clause.kind in p.kinds
            ]
            state: crag.CragState = {
                "domain": domain,
                "clause": clause,
                "analysis_date": analysis_date,
                "queries": [],
                "attempts": 0,
                "relevant": relevant,
                "usage": [],
            }
            results.append(crag.generate(state)["result"])
        return crag.combine(results)

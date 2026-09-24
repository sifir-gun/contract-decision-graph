"""Mode stub-j2 : dépendances provisoires de la CLI avant le J3. AUCUNE ANALYSE RÉELLE.

- extraction : clauses synthétiques déjà extraites, lues dans un fichier JSON
  (`--clauses`) ; le texte du contrat n'est pas analysé ;
- CRAG sans corpus : répond toujours INSUFFISANT, sans référence, donc aucune
  réponse inventée.

Remplacés au J3 par l'extraction LLM et le CRAG réel.
"""

import json
from pathlib import Path

from cdg.deps import Deps, ExtractionResult, RetrievalResult
from cdg.state import Clause, Domain

MODE = "stub-j2"


class JsonClausesExtractor:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            # ValueError et non TypeError : contenu de fichier invalide, comportement testé
            message = f"{self.path} : une liste de clauses JSON est attendue"
            raise ValueError(message)  # noqa: TRY004
        return ExtractionResult(clauses=[Clause.model_validate(c) for c in data], usage=[])


def _no_extraction(raw_text: str, feedback: list[str]) -> ExtractionResult:
    raise RuntimeError("extraction indisponible sans --clauses (mode stub-j2)")


def no_corpus_crag(domain: Domain, clauses: list[Clause]) -> RetrievalResult:
    return RetrievalResult(status="INSUFFISANT", evidence_ids=[], usage=[])


def deps(clauses_path: Path | str | None) -> Deps:
    extractor = JsonClausesExtractor(clauses_path) if clauses_path else _no_extraction
    return Deps(extractor=extractor, crag=no_corpus_crag)

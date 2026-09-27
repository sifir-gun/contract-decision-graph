"""Extraction simulée du mode démonstration (port `Extractor`) : pour un contrat du jeu,
les clauses qu'une extraction correcte rend (`data/contracts/attendus.yaml`), sans LLM.

Le contrat est reconnu à son texte masqué, masqué avec ses parties déclarées comme le fait
`run_contract` : tout autre texte est refusé, explicitement. La vérification de
l'extraction, elle, tourne pour de vrai sur ces clauses.
"""

from collections.abc import Iterable

from cdg.application.demo_set import DemoContract
from cdg.application.deps import ExtractionResult
from cdg.domain import masking
from cdg.domain.models import Usage

SIMULATED = "simulation"  # modèle de la consommation affichée : aucun appel


class DemoExtractionError(Exception):
    """Texte hors du jeu de démonstration : pas d'extraction simulée."""


class ExpectedExtractor:
    def __init__(self, contracts: Iterable[DemoContract]):
        self._by_text = {
            masking.mask(c.text(), c.parties).text: list(c.clauses) for c in contracts
        }

    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult:
        clauses = self._by_text.get(raw_text)
        if clauses is None:
            raise DemoExtractionError(
                "démonstration : extraction simulée pour les contrats du jeu seulement, "
                "masqués avec leurs parties déclarées"
            )
        usage = Usage(
            node="extract_clauses",
            model=SIMULATED,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
        )
        return ExtractionResult(clauses=clauses, usage=[usage])

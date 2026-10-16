"""Extraction simulée du mode démonstration (port `Extractor`) : pour un contrat du jeu,
les clauses qu'une extraction correcte rend (`data/contracts/attendus.yaml`), sans LLM.

Le contrat est reconnu à son texte masqué, masqué avec ses parties déclarées comme le fait
`run_contract` : tout autre texte est refusé, explicitement. Chaque texte est indexé avec le
profil de son contrat : le profil que passe le port doit être le sien. La vérification de
l'extraction, elle, tourne pour de vrai sur ces clauses.
"""

from collections.abc import Iterable

from cdg.application.demo_set import DemoContract
from cdg.application.deps import ExtractionResult
from cdg.domain import masking
from cdg.domain.models import Usage
from cdg.domain.profiles import Profile

SIMULATED = "simulation"  # modèle de la consommation affichée : aucun appel


class DemoExtractionError(Exception):
    """Texte hors du jeu de démonstration, ou d'un autre profil que celui demandé : pas
    d'extraction simulée."""


class ExpectedExtractor:
    def __init__(self, contracts: Iterable[DemoContract]):
        # texte masqué -> (profil du contrat, clauses attendues)
        self._by_text = {
            masking.mask(c.text(), c.parties).text: (c.profile, list(c.clauses))
            for c in contracts
        }

    def __call__(
        self, raw_text: str, feedback: list[str], profile: Profile
    ) -> ExtractionResult:
        expected = self._by_text.get(raw_text)
        if expected is None:
            raise DemoExtractionError(
                "démonstration : extraction simulée pour les contrats du jeu seulement, "
                "masqués avec leurs parties déclarées"
            )
        declared, clauses = expected
        if declared != profile:
            raise DemoExtractionError(
                f"démonstration : ce contrat du jeu est du profil {declared}, "
                f"l'analyse demande le profil {profile}"
            )
        usage = Usage(
            node="extract_clauses",
            model=SIMULATED,
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
        )
        return ExtractionResult(clauses=clauses, usage=[usage])

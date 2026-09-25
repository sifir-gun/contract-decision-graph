"""Port LLM : sortie structurée validée par un modèle Pydantic, consommation mesurée.

Adaptateurs : `adapters/llm/` (Mistral, Anthropic), choisis par la configuration.
"""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from cdg.domain.models import Usage

Tier = Literal["main", "light"]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMOutputError(Exception):
    """Réponse du modèle inexploitable : non structurée, ou consommation absente."""


class LLMTransientError(Exception):
    """Erreur passagère du fournisseur : limite de débit (429), erreur serveur (5xx) ou
    délai dépassé, connexion refusée ou impossible. L'adaptateur la lève à la place de
    l'erreur du SDK (en cause) ; seule elle est reprise sur l'extraction (RetryPolicy,
    section `extraction_retry`)."""


class LLMQuotaError(Exception):
    """Quota nul : le fournisseur répond 429 et la limite du compte pour ce modèle vaut 0.
    Pas une erreur passagère, jamais reprise : il faut vérifier l'offre du compte."""


class LLMProvider(Protocol):
    name: str

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[SchemaT], node: str
    ) -> tuple[SchemaT, Usage]: ...

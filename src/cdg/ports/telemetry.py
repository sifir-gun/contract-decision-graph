"""Port de télémétrie (ADR 008) : traces et métriques d'une opération du service, de ses
étapes (les nœuds du graphe) et de ses appels au LLM.

Adaptateur : `adapters/otel/` (OpenTelemetry), seul à importer la bibliothèque. Sans
destination : `application.observation.NoTelemetry`, qui ne fait rien.

Le port ne reçoit que des données écrites par le code : nom d'opération, identifiant du
contrat, acteur (dont l'adaptateur ne tire que le canal, et le `sub` sur réglage), nom de
nœud, numéro de tentative, domaine, statut final (état et décisions, jamais un motif),
consommation (modèle, tokens, latence). Jamais un texte de contrat, un prompt, une
réponse du LLM, ni le message d'une exception.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol

from cdg.domain.authorization import Actor
from cdg.domain.models import Usage

Operation = Literal["analyse", "revue", "expiration", "relance", "reprise"]
# rappel de fin d'une opération : le statut du moteur (seuls `statut`,
# `proposed_decision`, `final_decision` et la présence de `reject_reason` sont lus)
Finish = Callable[[Mapping[str, Any]], None]


class LLMCall(Protocol):
    def done(self, usage: Usage) -> None:
        """Consommation de l'appel, s'il a abouti."""
        ...


class Telemetry(Protocol):
    def operation(
        self, name: Operation, *, contract_id: str | None, actor: Actor | None
    ) -> AbstractContextManager[Finish]:
        """Une trace par opération ; le rappel rendu reçoit le statut final."""
        ...

    def step(
        self,
        node: str,
        *,
        attempt: int,
        domain: str | None,
        passthrough: tuple[type[BaseException], ...] = (),
    ) -> AbstractContextManager[None]:
        """Une étape par nœud ; `passthrough` : exceptions de contrôle (interruption du
        graphe), qui ferment l'étape sans la marquer en échec."""
        ...

    def llm_call(
        self, *, provider: str, tier: str, node: str
    ) -> AbstractContextManager[LLMCall]: ...

    def close(self) -> None:
        """Envoie ce qui reste, en un temps borné ; à l'arrêt du processus."""
        ...

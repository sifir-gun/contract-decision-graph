"""Reprises d'une analyse interrompue (phase Kubernetes, ADR 005).

Un processus arrêté ou mort au milieu d'une analyse la laisse « en cours » ; un réplica la
reprend depuis son dernier checkpoint. Une analyse qui fait planter le processus serait
reprise à chaque redémarrage, avec un appel au LLM à chaque fois : au-delà du maximum
configuré (`interrupted.max_resumes`), plus de reprise, ESCALADE vers la revue humaine.

Fonctions pures ; le compteur durable est un port (`ports/resumes.py`).
"""

from collections.abc import Iterable

from cdg.domain.models import NodeFailure

INTERRUPTED = "AnalyseInterrompue"


def exhausted(resume: int, limit: int) -> bool:
    """`resume` : rang de la reprise demandée, 1 à la première. Au-delà de `limit`, elle
    n'est plus faite."""
    if resume < 1:
        raise ValueError(f"rang de reprise invalide : {resume} (entier ≥ 1)")
    return resume > limit


def failure(pending: Iterable[str], resume: int, limit: int) -> NodeFailure:
    """Échec qui accompagne l'escalade : l'étape où l'analyse s'arrêtait, et le nombre de
    reprises déjà faites, toutes interrompues à leur tour."""
    nodes = ", ".join(sorted(set(pending)))
    return NodeFailure(
        node=nodes,
        error=INTERRUPTED,
        message=(
            f"analyse interrompue après {resume - 1} reprises (maximum : {limit}), "
            f"pendant {nodes} : plus de reprise automatique, revue humaine"
        ),
        attempts=resume,  # la première exécution et chaque reprise, toutes interrompues
    )

"""Reprises d'une analyse interrompue (phase Kubernetes, ADR 005).

Un processus arrêté ou mort au milieu d'une analyse la laisse « en cours » ; un réplica la
reprend depuis son dernier checkpoint. Une analyse qui fait planter le processus serait
reprise à chaque redémarrage, avec un appel au LLM à chaque fois : au-delà du maximum
configuré (`interrupted.max_resumes`), plus de reprise, ESCALADE vers la revue humaine.
Une analyse dont la configuration a changé depuis son début n'est jamais reprise sous
l'autre : ESCALADE aussi ; les deux causes, cumulées, sont citées toutes deux.

Fonctions pures ; le compteur durable est un port (`ports/resumes.py`).
"""

from collections.abc import Iterable

from cdg.domain.models import NodeFailure

INTERRUPTED = "AnalyseInterrompue"
CONFIG_CHANGED = "ConfigurationModifiee"


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


def config_change(
    analysed_with: str | None, current: str
) -> dict[str, str | None] | None:
    """Marqueur posé dans l'état à l'escalade : empreintes de la configuration du début de
    l'analyse et de celle du processus qui reprend ; None si c'est la même."""
    if analysed_with == current:
        return None
    return {"analyse": analysed_with, "reprise": current}


def config_failure(
    pending: Iterable[str], resume: int, analysed_with: str | None, current: str
) -> NodeFailure:
    """Échec qui accompagne l'escalade d'une analyse dont la configuration a changé : elle
    n'est jamais reprise sous l'autre ; une empreinte absente ne prouve pas que c'est la
    même."""
    nodes = ", ".join(sorted(set(pending)))
    return NodeFailure(
        node=nodes,
        error=CONFIG_CHANGED,
        message=(
            "configuration modifiée depuis le début de l'analyse (analyse : "
            f"{analysed_with or 'inconnue'}, courante : {current}), pendant {nodes} : "
            "reprise refusée, revue humaine"
        ),
        attempts=resume,
    )


def escalation_failures(
    pending: Iterable[str],
    resume: int,
    limit: int,
    analysed_with: str | None,
    current: str,
) -> list[NodeFailure]:
    """Causes d'une escalade à la reprise `resume` (rang 1 à la première) ; vide : la
    reprise se fait. Configuration modifiée et maximum atteint sont cités tous deux."""
    nodes = sorted(set(pending))
    found = []
    if analysed_with != current:
        found.append(config_failure(nodes, resume, analysed_with, current))
    if exhausted(resume, limit):
        found.append(failure(nodes, resume, limit))
    return found

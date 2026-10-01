"""Reprises d'une analyse interrompue (phase Kubernetes, ADR 005).

Un processus arrêté ou mort au milieu d'une analyse la laisse « en cours » ; un réplica la
reprend depuis son dernier checkpoint. Une analyse qui fait planter le processus serait
reprise à chaque redémarrage, avec un appel au LLM à chaque fois : au-delà du maximum
configuré (`interrupted.max_resumes`), plus de reprise, ESCALADE vers la revue humaine.
Une analyse dont la configuration a changé depuis son début n'est jamais reprise sous
l'autre : ESCALADE aussi ; les deux causes, cumulées, sont citées toutes deux.

Fonctions pures ; le compteur durable est un port (`ports/resumes.py`).
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from cdg.domain.models import NodeFailure

INTERRUPTED = "AnalyseInterrompue"
CONFIG_CHANGED = "ConfigurationModifiee"
RESUME_ERRORS = (CONFIG_CHANGED, INTERRUPTED)  # causes d'une escalade de reprise


class ResumeEscalation(BaseModel):
    """Cause d'une escalade écrite par la reprise, posée dans l'état puis scellée : rang
    de la reprise, maximum en vigueur, empreintes de la configuration du début de
    l'analyse et de celle du processus qui reprenait. Le rejeu la vérifie."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reprises: int
    maximum: int
    analyse: str | None
    reprise: str


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


def escalation_record(
    resume: int, limit: int, analysed_with: str | None, current: str
) -> dict[str, Any]:
    """Cause de l'escalade, en JSON, posée dans l'état et scellée."""
    return ResumeEscalation(
        reprises=resume, maximum=limit, analyse=analysed_with, reprise=current
    ).model_dump(mode="json")


def escalation_fault(
    failures: Sequence[Mapping[str, Any]],
    recorded: Mapping[str, Any] | None,
    decision_config_hash: str,
    analysis_max_resumes: int,
) -> str | None:
    """Défaut d'une escalade écrite par la reprise, ou None si elle porte une cause
    valide : enregistrée, cohérente avec le rapport d'échec (`failures`, ceux de cause de
    reprise), avec l'empreinte de configuration de la décision et, sans changement de
    configuration, avec le maximum de reprises de cette configuration. Une escalade de
    reprise sans cause valide reste une anomalie."""
    if recorded is None:
        return "escalade de reprise sans cause enregistrée"
    try:
        cause = ResumeEscalation.model_validate(recorded)
        is_exhausted = exhausted(cause.reprises, cause.maximum)
    except (ValidationError, ValueError):
        return "cause de l'escalade de reprise mal formée"
    changed = cause.analyse != cause.reprise
    expected = set()
    if changed:
        if cause.analyse != decision_config_hash:
            return (
                f"configuration d'analyse enregistrée ({cause.analyse}) différente de "
                f"celle de la décision ({decision_config_hash})"
            )
        expected.add(CONFIG_CHANGED)
    if is_exhausted:
        if not changed and cause.maximum != analysis_max_resumes:
            return (
                f"maximum de reprises enregistré ({cause.maximum}) différent de celui de "
                f"la configuration de l'analyse ({analysis_max_resumes})"
            )
        expected.add(INTERRUPTED)
    if not expected:
        return (
            f"escalade de reprise sans cause valide : reprise {cause.reprises}, maximum "
            f"{cause.maximum}, même configuration"
        )
    found = {f["error"] for f in failures}
    if found != expected:
        return (
            f"causes du rapport ({', '.join(sorted(found))}) incohérentes avec la "
            f"reprise enregistrée ({', '.join(sorted(expected))})"
        )
    if any(f.get("attempts") != cause.reprises for f in failures):
        return "rang de reprise du rapport différent de celui enregistré"
    return None

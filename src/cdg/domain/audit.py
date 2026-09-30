"""Piste d'audit : forme canonique, empreintes, chaînage, vérification et rejeu.

Fonctions pures, sans base ni horloge : le magasin (port `AuditStore`) fournit la tête de
chaîne et insère ; l'horodatage vient de l'appelant.

- `decision_hash` : empreinte de la seule partie décision de l'enregistrement (clauses,
  verdicts avec les résumés du CRAG, décisions proposée et finale, décision humaine,
  rapport d'échec, motif de rejet, date d'analyse, `config_hash`, constats du contrat). Sans identifiants de
  contrat ni de thread, horodatage, consommation, explication ni modèles : mêmes clauses,
  mêmes références et même configuration donnent la même empreinte (critère 6). Le
  rapport d'échec y porte le fait (budget dépassé), pas la mesure (tokens), et ses
  échecs de nœuds y sont rangés par domaine, comme les verdicts.
- `chain_hash` : SHA-256 de `prev_hash` puis de l'enregistrement complet ; le premier
  maillon part de `GENESIS`.
- `replay` : recalcule règles, justification et décision à partir de l'enregistrement
  scellé et des références figées, sans LLM ni CRAG.
- Formats : v2 (PR D2) porte sa version, l'acteur de l'analyse et celui de la décision
  humaine (jamais de nom ni d'e-mail), la version du code de l'analyse et celle du
  scellement (hors de `decision_hash`, chaînées) ; v1, sans version, relecteur nommé,
  reste vérifiable (tout est recalculé sur le JSON stocké) et rejouable par ses modèles,
  figés.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig
from cdg.domain.decision import decide
from cdg.domain.explanation import Explanation
from cdg.domain.justification import justify
from cdg.domain.models import (
    DOMAINS,
    AgentVerdict,
    Clause,
    Decision,
    HumanDecision,
    HumanReview,
    NodeFailure,
    Usage,
)
from cdg.domain.numeric import rounded
from cdg.domain.rules import RULES
from cdg.domain.version import CodeVersion

GENESIS = "0" * 64  # prev_hash du premier maillon
CONFIG_CHANGED = "configuration modifiée entre l'analyse et le scellement"
CODE_CHANGED = "code modifié entre l'analyse et le scellement"
_HASH = re.compile(r"[0-9a-f]{64}")

# nœuds exécutés après decision_gate : leur consommation et leurs échecs n'ont pas pesé
# sur la décision ; le rejeu les écarte
AFTER_GATE_NODES = ("explain",)


# --- Forme canonique et empreintes -----------------------------------------------------


def _plain(obj: Any) -> Any:
    """Valeur JSON, flottants arrondis ; tout autre type est une erreur, jamais `str`."""
    if isinstance(obj, BaseModel):
        return _plain(obj.model_dump(mode="json"))
    if isinstance(obj, Mapping):
        keys = [k for k in obj if not isinstance(k, str)]
        if keys:
            raise TypeError(f"forme canonique : clé non textuelle {keys[0]!r}")
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_plain(v) for v in obj]
    if isinstance(obj, bool | int | str) or obj is None:
        return obj
    if isinstance(obj, float):
        return rounded(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    raise TypeError(f"forme canonique : type non pris en charge {type(obj).__name__}")


def canonical(obj: Any) -> bytes:
    """JSON canonique : clés triées, sans espaces, UTF-8, flottants par `rounded`."""
    return json.dumps(
        _plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def is_hash(value: str) -> bool:
    """Empreinte SHA-256 en hexadécimal minuscule, 64 caractères."""
    return _HASH.fullmatch(value) is not None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def configuration_hash(data: Mapping[str, Any]) -> str:
    """Empreinte d'une configuration sous sa forme validée (JSON), par la sérialisation
    canonique : ni l'ordre des clés ni la mise en forme n'y changent rien. Toujours
    recalculée ainsi, jamais sur un texte relu (JSONB ne garde ni l'un ni l'autre)."""
    return _sha256(canonical(data))


def config_hash(config: DecisionConfig) -> str:
    """Empreinte de la configuration validée, sous forme canonique : un commentaire ou une
    mise en forme du fichier n'y change rien."""
    return configuration_hash(config.model_dump(mode="json"))


def models_of(config: DecisionConfig) -> dict[str, str]:
    """Identifiants des modèles, scellés avec la décision."""
    return {
        "llm_provider": config.llm.provider,
        "llm_main": config.llm.model("main"),
        "llm_light": config.llm.model("light"),
        "embedding": config.embedding.model,
    }


def analysis_context(config: DecisionConfig, code: CodeVersion) -> dict[str, Any]:
    """Contexte d'analyse, placé dans l'état initial par `run_contract` avant tout nœud :
    empreinte de la configuration qui produira la décision et sa forme validée (archivée
    au scellement), modèles qui analyseront, et version du code qui analyse (en JSON).
    C'est lui qui est scellé, même si un autre processus scelle."""
    return {
        "config_hash": config_hash(config),
        "analysis_config": config.model_dump(mode="json"),
        "models": models_of(config),
        "code_version": code.model_dump(mode="json"),
    }


def configurations_to_archive(
    state: Mapping[str, Any], sealing: DecisionConfig
) -> dict[str, dict[str, Any]]:
    """Configurations archivées avec l'enregistrement, par empreinte : celle de l'analyse
    (contexte posé par `run_contract`), dont l'empreinte est celle de la partie décision,
    et celle du processus qui scelle, si elle diffère (expiration après un changement de
    configuration). Une analyse antérieure à l'archive n'a pas sa configuration dans
    l'état : seule celle du processus qui scelle est archivée, et verify signale
    l'enregistrement si ce n'est pas celle de sa décision."""
    archived = {config_hash(sealing): sealing.model_dump(mode="json")}
    analysed = state.get("analysis_config")
    if analysed is not None:
        key = configuration_hash(analysed)
        if key != state["config_hash"]:
            raise ValueError(
                f"configuration de l'analyse différente de son empreinte ({key}, "
                f"{state['config_hash']} dans l'état) : état incohérent"
            )
        archived[key] = dict(analysed)
    return archived


def decision_hash(record: Mapping[str, Any]) -> str:
    """Empreinte de la partie décision d'un enregistrement (dict JSON)."""
    return _sha256(canonical(record["decision"]))


def chain_hash(record: Mapping[str, Any], prev_hash: str) -> str:
    """Empreinte chaînée : `prev_hash`, puis l'enregistrement complet (dict JSON)."""
    return _sha256(prev_hash.encode() + canonical(record))


# --- Enregistrement scellé ---------------------------------------------------------------


def _failure_order(failure: Mapping[str, Any]) -> tuple[int, str, str, str]:
    domain = failure.get("domain")
    rank = DOMAINS.index(domain) if domain in DOMAINS else len(DOMAINS)
    return (rank, failure["node"], failure["error"], failure["message"])


def decision_report(report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Rapport d'échec tel qu'il entre dans la partie décision : le fait, pas la mesure.

    Le nombre de tokens d'un budget dépassé en sort : deux dépassements de consommations
    différentes sont la même décision ; la mesure reste dans l'enregistrement scellé, hors
    de `decision_hash`. Les échecs de nœuds sont rangés par domaine, comme les verdicts :
    l'ordre d'arrivée des branches parallèles varie."""
    if report is None:
        return None
    fact = dict(report)
    if fact.get("stage") == "budget":
        fact.pop("tokens", None)
    budget = fact.get("budget")
    if isinstance(budget, Mapping):
        fact["budget"] = {k: v for k, v in budget.items() if k != "tokens"}
    failures = fact.get("failures")
    if isinstance(failures, list):
        fact["failures"] = sorted(failures, key=_failure_order)
    return fact


class _DecisionPart(BaseModel):
    """Partie décision de l'enregistrement, seule hachée par `decision_hash` ; commune
    aux deux formats, qui ne diffèrent que par la décision humaine."""

    model_config = ConfigDict(extra="forbid")

    analysis_date: date | None
    clauses: list[Clause]
    verdicts: list[AgentVerdict]  # dans l'ordre de DOMAINS
    proposed_decision: Decision | None
    margin: float | None
    final_decision: Decision | None
    failure_report: dict[str, Any] | None
    reject_reason: str | None
    config_hash: str
    # constats du contrat (tentative d'instruction, J4) : ils imposent la revue humaine ;
    # le texte n'est pas scellé, le rejeu les reprend tels quels
    input_findings: list[str] = []

    @field_validator("verdicts")
    @classmethod
    def _ordre_des_domaines(cls, verdicts: list[AgentVerdict]) -> list[AgentVerdict]:
        domains = [v.domain for v in verdicts]
        if domains != [d for d in DOMAINS if d in domains]:
            raise ValueError(
                f"verdicts hors de l'ordre des domaines ou répétés : {domains}"
            )
        return verdicts


class DecisionRecordV1(_DecisionPart):
    """Format v1, figé : relecteur nommé."""

    human: HumanDecision | None


class DecisionRecord(_DecisionPart):
    """Format v2 : l'acteur de la décision humaine."""

    human: HumanReview | None


class _RecordPart(BaseModel):
    """Enregistrement complet, haché par `chain_hash` ; commun aux deux formats."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str
    thread_id: str
    failure_report: dict[str, Any] | None  # complet, mesures comprises
    sealing_config_hash: str  # configuration du processus qui scelle
    sealing_findings: list[str]  # constats du scellement (configuration modifiée…)
    explanation: Explanation | None  # source, essais et motifs ; None après un rejet
    failures: list[NodeFailure]  # tous les échecs de nœud, dans l'ordre de l'état
    usage: list[Usage]
    models: dict[str, str]
    sealed_at: datetime

    @field_validator("sealed_at")
    @classmethod
    def _avec_fuseau(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("horodatage sans fuseau horaire : refusé")
        return value


class AuditRecordV1(_RecordPart):
    """Format v1, figé : sans version ni acteur de l'analyse."""

    decision: DecisionRecordV1  # config_hash : celui de l'analyse


class AuditRecord(_RecordPart):
    """Format v2 (PR D2) : version, acteur de l'analyse, acteur de la décision, version
    du code de l'analyse et du scellement."""

    version: Literal[2] = 2
    analyse_par: Actor | None  # None : analyse antérieure aux rôles
    # None : analyse antérieure au scellement de la version du code
    code_version: CodeVersion | None
    sealing_code_version: CodeVersion  # processus qui scelle
    decision: DecisionRecord  # config_hash : celui de l'analyse


# modèles de la partie décision, par version d'enregistrement ; sans version : v1
FORMATS: dict[int, type[DecisionRecordV1] | type[DecisionRecord]] = {
    1: DecisionRecordV1,
    2: DecisionRecord,
}


def build_record(
    state: Mapping[str, Any],
    *,
    thread_id: str,
    sealing_config_hash: str,
    sealing_code_version: CodeVersion,
    sealed_at: datetime,
) -> AuditRecord:
    """Enregistrement d'un contrat à partir de son état final. Une clé absente de l'état
    (rejet, escalade avant les analystes) est scellée vide ou nulle ; le contexte
    d'analyse (config_hash, modèles), lui, est exigé. Une configuration ou un code de
    scellement différents de ceux de l'analyse sont scellés aussi, chacun avec un
    constat."""
    missing = [k for k in ("config_hash", "models") if k not in state]
    if missing:
        raise ValueError(
            f"contexte d'analyse absent de l'état ({', '.join(missing)}) : un contrat "
            "se lance par run_contract, qui le pose avant tout nœud"
        )
    analysed_with = state["config_hash"]
    human = state.get("human")
    if isinstance(human, HumanDecision):
        raise TypeError(
            "décision humaine au format v1 (relecteur nommé) : le scellement v2 exige "
            "l'acteur qui tranche"
        )
    by_domain = {v.domain: v for v in state.get("verdicts", [])}
    analysed_by = state.get("code_version")
    code = None if analysed_by is None else CodeVersion.model_validate(analysed_by)
    findings = [] if sealing_config_hash == analysed_with else [CONFIG_CHANGED]
    if code is not None and code != sealing_code_version:
        findings.append(CODE_CHANGED)
    return AuditRecord(
        contract_id=state["contract_id"],
        thread_id=thread_id,
        analyse_par=state.get("analyse_par"),
        code_version=code,
        sealing_code_version=sealing_code_version,
        decision=DecisionRecord(
            analysis_date=state.get("analysis_date"),
            clauses=state.get("clauses", []),
            verdicts=[by_domain[d] for d in DOMAINS if d in by_domain],
            proposed_decision=state.get("proposed_decision"),
            margin=state.get("margin"),
            human=human,
            final_decision=state.get("final_decision"),
            failure_report=decision_report(state.get("failure_report")),
            reject_reason=state.get("reject_reason"),
            config_hash=analysed_with,
            input_findings=state.get("input_findings", []),
        ),
        failure_report=state.get("failure_report"),
        sealing_config_hash=sealing_config_hash,
        sealing_findings=findings,
        explanation=state.get("explanation"),
        failures=state.get("failures", []),
        usage=state.get("usage", []),
        models=state["models"],
        sealed_at=sealed_at,
    )


class AuditEntry(BaseModel):
    """Enregistrement scellé, prêt à l'ajout dans le journal."""

    contract_id: str
    thread_id: str
    record: dict[str, Any]
    config_hash: str
    decision_hash: str
    prev_hash: str
    chain_hash: str


class StoredAuditEntry(AuditEntry):
    id: int
    created_at: datetime


def seal(record: AuditRecord, prev_hash: str | None) -> AuditEntry:
    """Scelle un enregistrement à la suite de `prev_hash` (None : journal vide)."""
    prev = GENESIS if prev_hash is None else prev_hash
    if not is_hash(prev):
        raise ValueError(f"prev_hash invalide : {prev!r} (64 caractères hexadécimaux)")
    data = record.model_dump(mode="json")
    return AuditEntry(
        contract_id=record.contract_id,
        thread_id=record.thread_id,
        record=data,
        config_hash=record.decision.config_hash,
        decision_hash=decision_hash(data),
        prev_hash=prev,
        chain_hash=chain_hash(data, prev),
    )


# --- Vérification de la chaîne -----------------------------------------------------------


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    count: int
    head: str  # chain_hash du dernier maillon ; GENESIS pour un journal vide
    broken_id: int | None = None  # premier enregistrement fautif
    reason: str | None = None
    archived: int = 0  # configurations archivées, chacune redonnant son empreinte
    v1_exempted: int = 0  # enregistrements v1, antérieurs à l'archive : exemptés
    archive_fault: bool = (
        False  # défaut de l'archive des configurations, pas de la chaîne
    )


def _fault(entry: StoredAuditEntry, expected_prev: str) -> str | None:
    """Premier défaut d'un maillon, ou None. Recalcule tout à partir de l'enregistrement
    stocké tel quel : jamais d'un modèle relu, qui pourrait l'avoir complété."""
    if entry.prev_hash != expected_prev:
        return f"prev_hash {entry.prev_hash} : attendu {expected_prev} (maillon rompu)"
    try:
        decision = entry.record["decision"]
        columns = {
            "config_hash": decision["config_hash"],
            "contract_id": entry.record["contract_id"],
            "thread_id": entry.record["thread_id"],
        }
        recomputed = decision_hash(entry.record)
        chained = chain_hash(entry.record, entry.prev_hash)
    except (KeyError, TypeError, ValueError) as exc:
        return f"enregistrement mal formé : {type(exc).__name__} {exc}"
    for column, value in columns.items():
        if getattr(entry, column) != value:
            return f"{column} de la colonne différent de celui de l'enregistrement"
    if recomputed != entry.decision_hash:
        return (
            "decision_hash ne correspond pas à la partie décision de l'enregistrement"
        )
    if chained != entry.chain_hash:
        return "chain_hash ne correspond pas à l'enregistrement et à son prev_hash"
    return None


def verify_chain(
    entries: Sequence[StoredAuditEntry], expect_head: str | None = None
) -> ChainReport:
    """Vérifie le journal, du plus ancien au plus récent ; s'arrête au premier défaut.

    La chaîne seule ne voit pas une troncature de la fin du journal : la tête restante
    reste valide. `expect_head`, une tête conservée hors de la base, la détecte ; elle
    n'est comparée qu'à une chaîne intacte (défaut sans maillon fautif)."""
    head = GENESIS
    for entry in entries:
        reason = _fault(entry, head)
        if reason is not None:
            return ChainReport(False, len(entries), head, entry.id, reason)
        head = entry.chain_hash
    if expect_head is not None and head != expect_head:
        reason = (
            f"tête de chaîne {head} : attendue {expect_head} (fin du journal tronquée, "
            "ou autre journal)"
        )
        return ChainReport(False, len(entries), head, None, reason)
    return ChainReport(True, len(entries), head)


def verify_journal(
    entries: Sequence[StoredAuditEntry],
    configurations: Mapping[str, Mapping[str, Any]],
    expect_head: str | None = None,
) -> ChainReport:
    """La chaîne (`verify_chain`), puis l'archive des configurations : chaque
    configuration archivée redonne son empreinte, recalculée par la forme canonique,
    jamais sur le texte relu ; chaque enregistrement v2 a la configuration de sa décision.
    Les v1, antérieurs à l'archive, en sont exemptés, et comptés : non rejouables par
    elle."""
    chain = verify_chain(entries, expect_head)
    if not chain.ok:
        return chain
    for key, data in sorted(configurations.items()):
        recomputed = configuration_hash(data)
        if recomputed != key:
            return replace(
                chain,
                ok=False,
                archive_fault=True,
                reason=f"configuration archivée altérée : clé {key}, empreinte "
                f"recalculée {recomputed}",
            )
    exempted = 0
    for entry in entries:
        try:
            version = record_version(entry.record)
        except ReplayError as exc:
            return replace(chain, ok=False, broken_id=entry.id, reason=str(exc))
        if version == 1:
            exempted += 1
        elif entry.config_hash not in configurations:
            return replace(
                chain,
                ok=False,
                archive_fault=True,
                broken_id=entry.id,
                reason=f"configuration {entry.config_hash} de la décision non archivée",
            )
    return replace(chain, archived=len(configurations), v1_exempted=exempted)


# --- Rejeu --------------------------------------------------------------------------------


class ReplayError(Exception):
    """Rejeu impossible : configuration différente, références non figées, ou version
    d'enregistrement inconnue."""


def record_version(record: Mapping[str, Any]) -> int:
    """Version d'un enregistrement stocké ; sans version : v1."""
    version = record.get("version", 1)
    if version not in FORMATS:
        raise ReplayError(f"version d'enregistrement inconnue : {version!r}")
    return int(version)


@dataclass(frozen=True)
class ReplayReport:
    sealed_hash: str
    replayed_hash: str
    recomputed: bool  # False : rien à recalculer (rejet, escalade avant le gate)

    @property
    def identical(self) -> bool:
        return self.sealed_hash == self.replayed_hash


def _after_gate(node: str) -> bool:
    return node.split(":")[0] in AFTER_GATE_NODES


def replay(record: Mapping[str, Any], config: DecisionConfig) -> ReplayReport:
    """Recalcule la partie décision : règles sur les clauses scellées, justification sur les
    références figées (résumés du CRAG), puis décision du gate avec la consommation et les
    échecs d'avant le gate. La décision humaine est reprise telle quelle."""
    sealed = FORMATS[record_version(record)].model_validate(record["decision"])
    if sealed.config_hash != config_hash(config):
        raise ReplayError(
            "configuration différente de celle du scellement : rejeu impossible "
            f"({sealed.config_hash} scellée)"
        )
    failures = [
        NodeFailure.model_validate(f)
        for f in record["failures"]
        if not _after_gate(f["node"])
    ]
    gate_failed = any(f.node == "decision_gate" for f in failures)
    if not sealed.verdicts or gate_failed:  # le gate n'a rien décidé à partir d'eux
        replayed, recomputed = sealed, False
    else:
        usage = [
            Usage.model_validate(u)
            for u in record["usage"]
            if not _after_gate(u["node"])
        ]
        verdicts = []
        for v in sealed.verdicts:
            if v.retrieval is None:
                raise ReplayError(
                    f"verdict {v.domain} sans résumé du CRAG : non rejouable"
                )
            verdicts.append(
                justify(RULES[v.domain](sealed.clauses, config), v.retrieval)
            )
        outcome = decide(
            verdicts, failures, usage, config, input_findings=sealed.input_findings
        )
        final = sealed.human.decision if sealed.human is not None else outcome.final
        replayed = sealed.model_copy(
            update={
                "verdicts": verdicts,
                "proposed_decision": outcome.proposed,
                "margin": outcome.margin,
                "failure_report": decision_report(outcome.failure_report),
                "final_decision": final,
            }
        )
        recomputed = True
    return ReplayReport(
        sealed_hash=decision_hash(record),
        replayed_hash=_sha256(canonical(replayed.model_dump(mode="json"))),
        recomputed=recomputed,
    )

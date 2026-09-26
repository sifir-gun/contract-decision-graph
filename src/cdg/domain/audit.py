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
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

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
    NodeFailure,
    Usage,
)
from cdg.domain.numeric import rounded
from cdg.domain.rules import RULES

GENESIS = "0" * 64  # prev_hash du premier maillon
CONFIG_CHANGED = "configuration modifiée entre l'analyse et le scellement"
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


def config_hash(config: DecisionConfig) -> str:
    """Empreinte de la configuration validée, sous forme canonique : un commentaire ou une
    mise en forme du fichier n'y change rien."""
    return _sha256(canonical(config.model_dump(mode="json")))


def models_of(config: DecisionConfig) -> dict[str, str]:
    """Identifiants des modèles, scellés avec la décision."""
    return {
        "llm_provider": config.llm.provider,
        "llm_main": config.llm.model("main"),
        "llm_light": config.llm.model("light"),
        "embedding": config.embedding.model,
    }


def analysis_context(config: DecisionConfig) -> dict[str, Any]:
    """Contexte d'analyse, placé dans l'état initial par `run_contract` avant tout nœud :
    empreinte de la configuration qui produira la décision, et modèles qui analyseront.
    C'est lui qui est scellé, même si un autre processus scelle."""
    return {"config_hash": config_hash(config), "models": models_of(config)}


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


class DecisionRecord(BaseModel):
    """Partie décision de l'enregistrement, seule hachée par `decision_hash`."""

    model_config = ConfigDict(extra="forbid")

    analysis_date: date | None
    clauses: list[Clause]
    verdicts: list[AgentVerdict]  # dans l'ordre de DOMAINS
    proposed_decision: Decision | None
    margin: float | None
    human: HumanDecision | None
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


class AuditRecord(BaseModel):
    """Enregistrement complet, haché par `chain_hash`."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str
    thread_id: str
    decision: DecisionRecord  # config_hash : celui de l'analyse
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


def build_record(
    state: Mapping[str, Any],
    *,
    thread_id: str,
    sealing_config_hash: str,
    sealed_at: datetime,
) -> AuditRecord:
    """Enregistrement d'un contrat à partir de son état final. Une clé absente de l'état
    (rejet, escalade avant les analystes) est scellée vide ou nulle ; le contexte
    d'analyse (config_hash, modèles), lui, est exigé. Une configuration de scellement
    différente de celle de l'analyse est scellée aussi, avec un constat."""
    missing = [k for k in ("config_hash", "models") if k not in state]
    if missing:
        raise ValueError(
            f"contexte d'analyse absent de l'état ({', '.join(missing)}) : un contrat "
            "se lance par run_contract, qui le pose avant tout nœud"
        )
    analysed_with = state["config_hash"]
    by_domain = {v.domain: v for v in state.get("verdicts", [])}
    return AuditRecord(
        contract_id=state["contract_id"],
        thread_id=thread_id,
        decision=DecisionRecord(
            analysis_date=state.get("analysis_date"),
            clauses=state.get("clauses", []),
            verdicts=[by_domain[d] for d in DOMAINS if d in by_domain],
            proposed_decision=state.get("proposed_decision"),
            margin=state.get("margin"),
            human=state.get("human"),
            final_decision=state.get("final_decision"),
            failure_report=decision_report(state.get("failure_report")),
            reject_reason=state.get("reject_reason"),
            config_hash=analysed_with,
            input_findings=state.get("input_findings", []),
        ),
        failure_report=state.get("failure_report"),
        sealing_config_hash=sealing_config_hash,
        sealing_findings=[]
        if sealing_config_hash == analysed_with
        else [CONFIG_CHANGED],
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


# --- Rejeu --------------------------------------------------------------------------------


class ReplayError(Exception):
    """Rejeu impossible : configuration différente, ou références non figées."""


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
    sealed = DecisionRecord.model_validate(record["decision"])
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

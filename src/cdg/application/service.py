"""Service des contrats : ce que font la CLI et l'interface web, par les mêmes fonctions.

Chaque méthode correspond à une commande de la CLI et à une action de l'interface ; un
test vérifie que les deux portes appellent la même (`tests/test_parite.py`) :

| Méthode     | CLI       | Interface web                         |
| ----------- | --------- | ------------------------------------- |
| `analyse`   | `run`     | nouvelle analyse                      |
| `decide`    | `resume`  | revue humaine                         |
| `contracts` | `list`    | liste des contrats                    |
| `dossier`   | `show`    | dossier d'un contrat                  |
| `history`   | `history` | parcours, dans le dossier             |
| `expire`    | `expire`  | administration                        |
| `journal`   | `journal` | journal d'audit                       |
| `verify`    | `verify`  | vérifier la chaîne                    |
| `replay`    | `replay`  | rejouer, dans le dossier              |

Le service ne décide rien : le graphe (port `ContractEngine`) rend les verdicts et
applique la politique de revue ; le domaine vérifie la chaîne et rejoue. Le texte d'un
contrat est masqué avant le graphe (`run_contract`) : le service ne le garde pas.

Les actions qui modifient un état (`analyse`, `decide`, `expire`) passent l'une après
l'autre, sous un verrou unique : l'interface web sert ses requêtes dans des threads, et
`run_contract` vérifie qu'un thread n'existe pas avant de le créer. Les lectures restent
concurrentes. Le verrou ne vaut que pour un processus (`docs/adr-004-interface-web.md`).
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from cdg.application import ingestion
from cdg.domain import audit
from cdg.domain.config import DecisionConfig
from cdg.domain.identifiers import check_contract_id
from cdg.domain.models import Clause, Usage
from cdg.ports.audit_store import AuditStore
from cdg.ports.engine import ContractEngine

# état d'un contrat, lu dans son statut
WAITING, DONE, REJECTED, RUNNING = "en_attente", "termine", "rejete", "en_cours"


def state_label(status: Mapping[str, Any]) -> str:
    if status["statut"] == "suspendu":
        return WAITING
    if status["statut"] == "en_cours":
        return RUNNING
    return REJECTED if status.get("reject_reason") else DONE


def extraction_refusals(
    history: Sequence[Mapping[str, Any]], failure_report: Mapping[str, Any] | None
) -> list[list[str]]:
    """Motifs de chaque extraction refusée, dans l'ordre : le retour ciblé posé avant
    chaque nouvelle extraction, puis les problèmes de la dernière si elle a été
    escaladée."""
    refusals = [
        list(step["extraction_feedback"])
        for step in history
        if "extract_clauses" in step["next"] and step["extraction_feedback"]
    ]
    if failure_report is not None and failure_report.get("stage") == "extraction":
        refusals.append(list(failure_report["problems"]))
    return refusals


def usage_by_node(usage: Sequence[Usage]) -> list[dict[str, Any]]:
    """Consommation et latence par nœud, dans l'ordre du premier appel."""
    rows: dict[str, dict[str, Any]] = {}
    for u in usage:
        row = rows.setdefault(
            u.node,
            {
                "node": u.node,
                "models": [],
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
            },
        )
        if u.model not in row["models"]:
            row["models"].append(u.model)
        row["calls"] += 1
        row["tokens_in"] += u.tokens_in
        row["tokens_out"] += u.tokens_out
        row["latency_ms"] += u.latency_ms
    return list(rows.values())


def _retained(verdicts: Sequence[Mapping[str, Any]]) -> list[str]:
    references = [
        reference
        for v in verdicts
        for clause in (v.get("retrieval") or {}).get("clauses", [])
        for reference in clause["retained"]
    ]
    return list(dict.fromkeys(references))


@dataclass(frozen=True)
class ContractService:
    engine: ContractEngine
    audit_store: Callable[[], AuditStore]
    config: DecisionConfig
    today: Callable[[], date]  # date d'analyse par défaut : jour légal en France
    now: Callable[[], datetime]  # horloge de l'expiration
    # verrou unique des modifications : analyse, décision humaine, expiration
    _writes: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def analyse(
        self,
        raw_text: str,
        *,
        contract_id: str,
        parties: Sequence[str] = (),
        analysis_date: date | None = None,
    ) -> dict[str, Any]:
        check_contract_id(
            contract_id
        )  # à la création seulement : l'existant reste lisible
        on = analysis_date if analysis_date is not None else self.today()
        with self._writes:
            return self.engine.run(contract_id, raw_text, parties, on)

    def decide(self, thread_id: str, answer: Mapping[str, Any]) -> dict[str, Any]:
        """Réponse humaine brute : validée par la politique dans le graphe, redemandée
        avec son motif si elle est mal formée ou refusée."""
        with self._writes:
            return self.engine.resume(thread_id, dict(answer))

    def contracts(self, *, pending_only: bool = False) -> list[dict[str, Any]]:
        """Contrats du checkpointer, du plus récemment modifié au plus ancien ; le graphe
        est ouvert une seule fois pour toute la liste."""
        rows = []
        for status in self.engine.overview():
            row = {
                "thread_id": status["thread_id"],
                "etat": state_label(status),
                "analysis_date": status["analysis_date"],
                "proposed_decision": status["proposed_decision"],
                "final_decision": status["final_decision"],
                "started_at": status["started_at"],
                "updated_at": status["updated_at"],
            }
            if not pending_only or row["etat"] == WAITING:
                rows.append(row)
        return sorted(rows, key=lambda r: str(r["updated_at"]), reverse=True)

    def dossier(self, thread_id: str) -> dict[str, Any]:
        """Tout ce qu'on sait d'un contrat : statut, texte masqué, clauses, verdicts et
        texte des références retenues, alertes, parcours, consommation, empreintes."""
        status = self.engine.status(thread_id)
        values = self.engine.values(thread_id)
        history = self.engine.history(thread_id)
        known = ingestion.reference_texts()
        request = status["demande"]
        clauses: list[Clause] = values.get("clauses", [])
        usage: list[Usage] = values.get("usage", [])
        return {
            "thread_id": thread_id,
            "etat": state_label(status),
            "status": status,
            "texte_masque": values.get("raw_text", ""),
            "clauses": [c.model_dump() for c in clauses],
            "verdicts": status["verdicts"],
            # None : référence absente des fichiers du corpus (renommée depuis)
            "references": {r: known.get(r) for r in _retained(status["verdicts"])},
            "consommation": usage_by_node(usage),
            "parcours": history,
            "extractions_refusees": extraction_refusals(
                history, status["failure_report"]
            ),
            # la politique refuse de toute façon une levée non permise ; l'interface
            # ne propose la case que si elle est recevable
            "levee_possible": request is not None
            and self.config.human_policy.allow_block_override
            and any(v["hard_block"] for v in request["verdicts"]),
        }

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self.engine.history(thread_id)

    def expire(self, older_than: timedelta) -> tuple[datetime, list[dict[str, Any]]]:
        with self._writes:
            now = self.now()  # après l'attente du verrou : l'heure de l'expiration
            return now, self.engine.expire(older_than, now)

    def journal(self) -> list[dict[str, Any]]:
        """Enregistrements scellés, du plus ancien au plus récent."""
        return [
            {
                "id": e.id,
                "contract_id": e.contract_id,
                "thread_id": e.thread_id,
                "proposed_decision": e.record["decision"]["proposed_decision"],
                "final_decision": e.record["decision"]["final_decision"],
                "sealed_at": e.record["sealed_at"],
                "decision_hash": e.decision_hash,
                "prev_hash": e.prev_hash,
                "chain_hash": e.chain_hash,
            }
            for e in self.audit_store().entries()
        ]

    def verify(self, expect_head: str | None = None) -> audit.ChainReport:
        return audit.verify_chain(self.audit_store().entries(), expect_head)

    def replay(self, thread_id: str) -> dict[str, Any]:
        """Rejoue la décision scellée d'un thread, sans LLM ni corpus (critère 6)."""
        sealed = [e for e in self.audit_store().entries() if e.thread_id == thread_id]
        if not sealed:
            raise audit.ReplayError(
                f"aucun enregistrement scellé pour le thread {thread_id}"
            )
        report = audit.replay(sealed[0].record, self.config)
        return {
            "thread_id": thread_id,
            "empreinte_scellee": report.sealed_hash,
            "empreinte_rejouee": report.replayed_hash,
            "identique": report.identical,
            "recalcule": report.recomputed,
        }

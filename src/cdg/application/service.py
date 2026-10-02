"""Service des contrats : ce que font la CLI, l'interface web et le serveur MCP, par les
mêmes fonctions.

Chaque méthode correspond à une commande de la CLI, à une action de l'interface et, pour
quatre d'entre elles, à un outil du serveur MCP ; un test vérifie que les portes appellent
la même (`tests/test_parite.py`). Le serveur MCP n'a aucun outil de décision (ADR 007) :

| Méthode        | CLI            | Interface web             | Serveur MCP         |
| -------------- | -------------- | ------------------------- | ------------------- |
| `analyse`      | `run`          | nouvelle analyse          | `analyser_contrat`  |
| `decide`       | `resume`       | revue humaine             | jamais              |
| `contracts`    | `list`         | liste des contrats        | `lister_contrats`   |
| `dossier`      | `show`         | dossier d'un contrat      | `consulter_dossier` |
| `history`      | `history`      | parcours, dans le dossier |                     |
| `expire`       | `expire`       | administration            | jamais              |
| `journal`      | `journal`      | journal d'audit           |                     |
| `verify`       | `verify`       | vérifier la chaîne        | `verifier_journal`  |
| `replay`       | `replay`       | rejouer, dans le dossier  |                     |
| `config_check` | `config-check` | administration            |                     |

Le service ne décide rien : le graphe (port `ContractEngine`) rend les verdicts et
applique la politique de revue ; le domaine vérifie la chaîne et rejoue. Le texte d'un
contrat est masqué avant le graphe (`run_contract`) : le service ne le garde pas.

Les actions qui modifient un état (`analyse`, `decide`, `expire`) passent l'une après
l'autre, sous un verrou unique : l'interface web sert ses requêtes dans des threads, le
serveur MCP ses outils aussi, et `run_contract` vérifie qu'un thread n'existe pas avant
de le créer. Les lectures restent concurrentes. Le verrou ne vaut que pour un processus (`docs/adr-004-interface-web.md`).
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from cdg.application import ingestion
from cdg.domain import audit, authorization, policy
from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig
from cdg.domain.identifiers import check_contract_id
from cdg.domain.models import Clause, Usage
from cdg.domain.version import CodeVersion
from cdg.ports.audit_store import AuditStore
from cdg.ports.engine import ContractEngine, ThreadError

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


class FourEyesRefused(Exception):
    """Revue refusée par les quatre yeux, ou décision et expiration refusées à un canal
    qui ne décide jamais (serveur MCP), avant le graphe (premier contrôle ; la politique
    du graphe fait le second)."""


@dataclass(frozen=True)
class ContractService:
    engine: ContractEngine
    audit_store: Callable[[], AuditStore]
    config: DecisionConfig
    today: Callable[[], date]  # date d'analyse par défaut : jour légal en France
    now: Callable[[], datetime]  # horloge de l'expiration
    code_version: CodeVersion  # du processus : rejeu fidèle ou réévaluation
    # verrou unique des modifications : analyse, décision humaine, expiration
    _writes: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def analyse(
        self,
        raw_text: str,
        *,
        contract_id: str,
        actor: Actor,
        parties: Sequence[str] = (),
        analysis_date: date | None = None,
    ) -> dict[str, Any]:
        check_contract_id(
            contract_id
        )  # à la création seulement : l'existant reste lisible
        on = analysis_date if analysis_date is not None else self.today()
        with self._writes:
            return self.engine.run(contract_id, raw_text, parties, on, actor)

    def decide(self, thread_id: str, answer: Mapping[str, Any]) -> dict[str, Any]:
        """Réponse humaine brute : validée par la politique dans le graphe, redemandée
        avec son motif si elle est mal formée ou refusée."""
        with self._writes:
            self._four_eyes(thread_id, answer)
            return self.engine.resume(thread_id, dict(answer))

    def _four_eyes(self, thread_id: str, answer: Mapping[str, Any]) -> None:
        """Premier contrôle des quatre yeux, pour les deux portes : l'acteur de l'analyse,
        lu dans l'état, et celui de la réponse. Une réponse mal formée passe : la
        politique du graphe la refuse et la redemande."""
        if answer.get("source", "humain") != "humain":
            return
        try:
            reviewer = Actor.model_validate(answer.get("acteur"))
        except ValidationError:
            return
        analyst = self.engine.status(thread_id).get("analyse_par")
        refused = authorization.four_eyes(
            None if analyst is None else Actor.model_validate(analyst), reviewer
        )
        if refused:
            raise FourEyesRefused(refused)

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
            # ne propose la case que si elle est recevable (après un changement de
            # configuration, celle de l'analyse doit l'autoriser aussi)
            "levee_possible": request is not None
            and self.config.human_policy.allow_block_override
            and policy.analysis_allows_override(values, self.config)
            and any(v["hard_block"] for v in request["verdicts"]),
            # relances de ce contrat sous la configuration actuelle
            "relances": [
                s["thread_id"]
                for s in self.engine.overview()
                if s.get("relance_de") == thread_id
            ],
        }

    def relaunch(
        self, thread_id: str, *, actor: Actor, contract_id: str | None = None
    ) -> dict[str, Any]:
        """Relance, sous la configuration actuelle, l'analyse d'un contrat escaladé pour
        changement de configuration et encore en attente : un nouveau contrat (un thread
        par contrat ; par défaut `<id>-relance`), sur le texte masqué conservé (l'original
        ne l'est jamais), à la même date d'analyse. Le contrat escaladé reste en attente,
        jusqu'à sa décision ou son expiration."""
        status = self.engine.status(thread_id)
        if not status.get("configuration_changee"):
            raise ThreadError(
                f"{thread_id} : relance réservée à un contrat escaladé pour changement "
                "de configuration pendant son analyse"
            )
        if status["demande"] is None:
            raise ThreadError(
                f"{thread_id} : relance d'un contrat en attente seulement"
            )
        target = contract_id if contract_id is not None else f"{thread_id}-relance"
        check_contract_id(target)
        values = self.engine.values(thread_id)
        with self._writes:
            status = self.engine.run(
                target,
                values["raw_text"],
                (),
                values["analysis_date"],
                actor,
                relaunch_of=thread_id,
            )
        return {
            **status,
            # pour le relecteur : seule la configuration a changé
            "relance": {
                "de": thread_id,
                "date_analyse": values["analysis_date"].isoformat(),
                "configuration": audit.config_hash(self.config),
                "note": "même texte masqué, même date d'analyse : seule la "
                "configuration change",
            },
        }

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self.engine.history(thread_id)

    def expire(
        self, older_than: timedelta, actor: Actor
    ) -> tuple[datetime, list[dict[str, Any]]]:
        refused = authorization.decision_refused(actor)  # premier contrôle
        if refused:
            raise FourEyesRefused(refused)
        with self._writes:
            now = self.now()  # après l'attente du verrou : l'heure de l'expiration
            return now, self.engine.expire(older_than, now, actor)

    def config_check(self) -> dict[str, Any]:
        """Contrats en attente d'une revue analysés sous une autre configuration que la
        courante : `resume` les refuse. À trancher ou à expirer avant de changer de
        configuration, ou à relancer après (ADR 005, « Changement de configuration »)."""
        current = audit.config_hash(self.config)
        waiting = [
            status
            for status in self.engine.overview()
            if state_label(status) == WAITING and status["config_hash"] != current
        ]

        def row(status: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "thread_id": status["thread_id"],
                "config_hash": status["config_hash"],
                "analysis_date": status["analysis_date"],
            }

        return {
            "configuration": current,
            "a_trancher": [row(s) for s in waiting if not s["configuration_changee"]],
            # escaladés car la configuration avait changé : resume les accepte
            "escalades_configuration": [
                row(s) for s in waiting if s["configuration_changee"]
            ],
        }

    def resume_interrupted(self) -> list[dict[str, Any]]:
        """Reprise des analyses interrompues : une modification, sous le verrou du
        service. Lancée en arrière-plan par l'interface en mode réel (ADR 005)."""
        with self._writes:
            return self.engine.resume_interrupted()

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
        """Chaîne du journal, puis archive des configurations (une lecture de chacun)."""
        store = self.audit_store()
        return audit.verify_journal(
            store.entries(), store.configurations(), expect_head
        )

    def replay(self, thread_id: str) -> dict[str, Any]:
        """Rejoue la décision scellée d'un thread, sans LLM ni corpus (critère 6), sur la
        configuration archivée de sa décision, ou, à défaut, sur la configuration courante
        si elle a la même empreinte (la sortie le dit). Rejeu fidèle si le même code est
        prouvé (une différence est alors une anomalie), réévaluation sinon."""
        store = self.audit_store()
        sealed = [e for e in store.entries() if e.thread_id == thread_id]
        if not sealed:
            raise audit.ReplayError(
                f"aucun enregistrement scellé pour le thread {thread_id}"
            )
        record, wanted = sealed[0].record, sealed[0].config_hash
        configuration = store.configurations().get(wanted)
        source = "archivee"
        if configuration is None:
            current = self.config.model_dump(mode="json")
            if audit.configuration_hash(current) != wanted:
                raise audit.ReplayError(
                    f"configuration {wanted} non archivée (enregistrement antérieur à "
                    "l'archive), et différente de la configuration courante : non "
                    "rejouable"
                )
            configuration, source = current, "courante, même empreinte"
        report = audit.replay(record, configuration)
        sense, why = audit.replay_sense(record, self.code_version)
        return {
            "thread_id": thread_id,
            "empreinte_scellee": report.sealed_hash,
            "empreinte_rejouee": report.replayed_hash,
            "identique": report.identical,
            "recalcule": report.recomputed,
            "sens": sense,
            "motif_du_sens": why,
            "configuration": source,
            # escalade de reprise sans cause valide : défaut du journal, quel que soit
            # le sens du rejeu
            "defaut": report.fault,
            # rejeu fidèle différent, ou défaut : anomalie ; réévaluation différente :
            # signalée
            "anomalie": report.fault is not None
            or (sense == audit.FAITHFUL and not report.identical),
        }

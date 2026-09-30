"""audit_seal : scelle le contrat dans le journal d'audit, puis écrit les empreintes dans
l'état. Scelle aussi les rejets et les escalades.

Le calcul reste dans le domaine (`domain/audit.py`) ; le journal passe par le port
`AuditStore`, qui fournit la tête de chaîne et insère. Rejoué après un arrêt, le nœud rend
l'enregistrement déjà scellé (même thread, même décision) : un seul enregistrement par
contrat. L'empreinte scellée est celle de l'analyse (état initial, `run_contract`) ; celle
de la configuration du processus qui scelle l'accompagne, comme la version de son code.
Les constats du scellement (code modifié depuis l'analyse…) vont aussi dans l'état : la
sortie de la revue les montre au réviseur. Les configurations de la décision (celle de
l'analyse, et celle du processus qui scelle si elle diffère) sont archivées dans la même
transaction que l'enregistrement.
"""

from typing import Any

from cdg.application.deps import Clock
from cdg.application.state import ContractState
from cdg.domain import audit
from cdg.domain.config import DecisionConfig
from cdg.domain.version import CodeVersion
from cdg.ports.audit_store import AuditStore


def audit_seal(
    state: ContractState,
    audit_store: AuditStore,
    clock: Clock,
    decision_config: DecisionConfig,
    code_version: CodeVersion,
    thread_id: str,
) -> dict[str, Any]:
    record = audit.build_record(
        state,
        thread_id=thread_id,
        sealing_config_hash=audit.config_hash(decision_config),
        sealing_code_version=code_version,
        sealed_at=clock(),
    )
    stored = audit_store.append(
        lambda head: audit.seal(record, head),
        audit.configurations_to_archive(state, decision_config),
    )
    return {
        "config_hash": stored.config_hash,
        "decision_hash": stored.decision_hash,
        "chain_hash": stored.chain_hash,
        "sealing_findings": stored.record["sealing_findings"],
    }

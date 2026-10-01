"""Vrai journal d'audit du poste, en lecture seule (PR D2, ADR 005).

Exclu par défaut et en CI (`--journal-reel` pour le lancer, avec PostgreSQL) : il lit la
table `audit_decisions` du poste, sans rien écrire, et n'en copie aucune donnée dans le
dépôt (le jeu v1 des tests automatiques est synthétique, `tests/test_audit_v2.py`).

Il vérifie ce que promettent la PR D2 et l'archivage des configurations : la chaîne
entière reste vérifiable, et l'archive conforme (aucun enregistrement v2 sans sa
configuration archivée) ; chaque enregistrement est relu par les modèles de sa version et
reproduit à l'identique ; le rejeu part de la configuration archivée, ou, à défaut, de la
configuration courante de même empreinte (toujours une réévaluation pour un v1). Il
n'affiche que des comptes.
"""

from collections import Counter

import pytest

from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.domain import audit
from cdg.domain.config import load_config

pytestmark = [pytest.mark.journal_reel, pytest.mark.pg]
CONFIG = load_config()


def test_vrai_journal_verifiable_relu_et_rejoue_a_l_identique(pg, capsys):
    store = PostgresAuditStore(pg.app)  # SELECT seulement
    entries, archived = store.entries(), store.configurations()
    assert entries, "vrai journal vide : rien à vérifier"
    report = audit.verify_journal(entries, archived)
    assert report.ok, f"enregistrement {report.broken_id} : {report.reason}"
    versions: Counter[int] = Counter()
    replay: Counter[str] = Counter()
    current = CONFIG.model_dump(mode="json")
    for entry in entries:
        version = audit.record_version(entry.record)
        versions[version] += 1
        decision = audit.FORMATS[version].model_validate(entry.record["decision"])
        assert audit.canonical(decision.model_dump(mode="json")) == audit.canonical(
            entry.record["decision"]
        ), f"enregistrement {entry.id} : relecture différente du stocké"
        if entry.config_hash in archived:
            configuration, source = archived[entry.config_hash], "archivée"
        elif audit.configuration_hash(current) == entry.config_hash:
            configuration, source = current, "courante"
        else:
            replay["configuration non archivée, non rejouable"] += 1
            continue
        try:
            replayed = audit.replay(entry.record, configuration)
        except audit.ReplayError:
            replay[f"non rejouable (configuration {source})"] += 1
            continue
        assert replayed.identical, f"enregistrement {entry.id} : rejeu différent"
        replay[f"rejoué à l'identique (configuration {source})"] += 1
    v2_without = sum(
        1
        for e in entries
        if audit.record_version(e.record) == 2 and e.config_hash not in archived
    )
    assert v2_without == 0
    with capsys.disabled():
        print(
            f"\nvrai journal : {len(entries)} enregistrements, versions "
            f"{dict(versions)}, v2 sans configuration archivée : {v2_without}, "
            f"configurations archivées : {len(archived)}, rejeu {dict(replay)}"
        )

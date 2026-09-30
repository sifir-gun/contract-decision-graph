"""Vrai journal d'audit du poste, en lecture seule (PR D2, ADR 005).

Exclu par défaut et en CI (`--journal-reel` pour le lancer, avec PostgreSQL) : il lit la
table `audit_decisions` du poste, sans rien écrire, et n'en copie aucune donnée dans le
dépôt (le jeu v1 des tests automatiques est synthétique, `tests/test_audit_v2.py`).

Il vérifie ce que la PR D2 promet aux enregistrements d'avant elle : la chaîne entière reste
vérifiable ; chaque enregistrement est relu par les modèles de sa version et reproduit à
l'identique (rien n'est ajouté ni perdu par les nouveaux modèles) ; ceux scellés sous la
configuration courante se rejouent à l'identique. Il n'affiche que des comptes.
"""

from collections import Counter

import pytest

from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.domain import audit
from cdg.domain.config import load_config

pytestmark = [pytest.mark.journal_reel, pytest.mark.pg]
CONFIG = load_config()


def test_vrai_journal_verifiable_relu_et_rejoue_a_l_identique(pg, capsys):
    entries = PostgresAuditStore(pg.app).entries()  # SELECT seulement
    assert entries, "vrai journal vide : rien à vérifier"
    report = audit.verify_chain(entries)
    assert report.ok, f"maillon {report.broken_id} : {report.reason}"
    versions: Counter[int] = Counter()
    replay: Counter[str] = Counter()
    current = audit.config_hash(CONFIG)
    for entry in entries:
        version = audit.record_version(entry.record)
        versions[version] += 1
        decision = audit.FORMATS[version].model_validate(entry.record["decision"])
        assert audit.canonical(decision.model_dump(mode="json")) == audit.canonical(
            entry.record["decision"]
        ), f"enregistrement {entry.id} : relecture différente du stocké"
        if entry.config_hash != current:
            replay["autre configuration"] += 1
            continue
        try:
            replayed = audit.replay(entry.record, CONFIG)
        except audit.ReplayError:
            replay["non rejouable (références non figées)"] += 1
            continue
        assert replayed.identical, f"enregistrement {entry.id} : rejeu différent"
        replay["rejoué à l'identique"] += 1
    with capsys.disabled():
        print(
            f"\nvrai journal : {len(entries)} enregistrements, versions "
            f"{dict(versions)}, rejeu {dict(replay)}"
        )

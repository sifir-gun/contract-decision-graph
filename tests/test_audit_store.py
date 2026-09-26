"""Journal d'audit : contrat du port `AuditStore` (PostgreSQL et doublure en mémoire), puis
verrou, index uniques et droits d'`app_role` sur PostgreSQL.

Chaque test PostgreSQL travaille sur un journal jetable (fixture `journal`), de même
structure et mêmes droits qu'`audit_decisions`.
"""

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from doubles import MemoryAuditStore
from psycopg import sql
from pydantic import BaseModel

from cdg import cli
from cdg.adapters.postgres import audit_store as audit_store_module
from cdg.adapters.postgres import migrations
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.domain import audit
from cdg.domain.config import DecisionConfig, load_config
from cdg.ports.audit_store import AuditStoreError

CONFIG = load_config()
SEALED_AT = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


def record(contract_id, reason="texte trop court", sealed_at=SEALED_AT):
    """Enregistrement minimal : un rejet, scellé comme les autres."""
    return audit.build_record(
        {
            "contract_id": contract_id,
            **audit.analysis_context(CONFIG),
            "reject_reason": reason,
        },
        thread_id=contract_id,
        sealing_config_hash=audit.config_hash(CONFIG),
        sealed_at=sealed_at,
    )


class Sealer:
    """Fonction de scellement passée à `append` ; garde les têtes de chaîne reçues."""

    def __init__(self, record_):
        self.record, self.heads = record_, []

    def __call__(self, head):
        self.heads.append(head)
        return audit.seal(self.record, head)


@pytest.fixture(params=["memoire", pytest.param("postgres", marks=pytest.mark.pg)])
def store(request):
    if request.param == "memoire":
        return MemoryAuditStore()
    pg = request.getfixturevalue("pg")
    return PostgresAuditStore(pg.app, table=request.getfixturevalue("journal"))


# --- Contrat du port, sur les deux implémentations -----------------------------------------


def test_premier_ajout_depuis_la_genese(store):
    sealer = Sealer(record("c-1"))
    stored = store.append(sealer)
    assert sealer.heads == [None] and stored.prev_hash == audit.GENESIS
    assert stored.id >= 1 and stored.created_at.tzinfo is not None
    assert store.entries() == [stored]


def test_ajouts_chaines_puis_verifies(store):
    sealers = [Sealer(record(f"c-{i}")) for i in range(1, 4)]
    stored = [store.append(s) for s in sealers]
    assert [s.heads for s in sealers] == [
        [None],
        [stored[0].chain_hash],
        [stored[1].chain_hash],
    ]
    entries = store.entries()
    assert [e.id for e in entries] == sorted(e.id for e in stored)
    # l'enregistrement relu tel que stocké (JSONB) redonne les mêmes empreintes
    report = audit.verify_chain(entries)
    assert (report.ok, report.count) == (True, 3)


def test_ajout_rejoue_pour_un_meme_thread_idempotent(store):
    first = store.append(Sealer(record("c-1")))
    # audit_seal rejoué après un arrêt : autre horodatage, même décision
    later = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
    again = store.append(Sealer(record("c-1", sealed_at=later)))
    assert again == first and len(store.entries()) == 1


def test_meme_thread_autre_decision_refusee(store):
    store.append(Sealer(record("c-1")))
    with pytest.raises(AuditStoreError, match="c-1"):
        store.append(Sealer(record("c-1", reason="texte en anglais")))
    assert len(store.entries()) == 1


# --- PostgreSQL : verrou, index uniques, droits ----------------------------------------------


@pytest.mark.pg
def test_ajouts_concurrents_sous_app_role_chaine_lineaire(pg, journal):
    store = PostgresAuditStore(pg.app, table=journal)
    with ThreadPoolExecutor(max_workers=8) as pool:
        stored = list(
            pool.map(lambda i: store.append(Sealer(record(f"c-{i}"))), range(8))
        )
    entries = store.entries()
    assert len(entries) == 8 and len({e.prev_hash for e in entries}) == 8
    assert audit.verify_chain(entries).ok
    assert {e.id for e in stored} == {e.id for e in entries}


@pytest.mark.pg
def test_verrou_consultatif_permis_a_app_role(pg):
    with psycopg.connect(pg.app) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(1)")


def _insert_copy(pg, journal, **changes):
    """Insère, en administrateur, une copie du dernier maillon avec des colonnes changées."""
    columns = [
        "contract_id",
        "thread_id",
        "record",
        "config_hash",
        "decision_hash",
        "prev_hash",
        "chain_hash",
    ]
    table = sql.Identifier(journal)
    with psycopg.connect(pg.admin) as conn:
        row = conn.execute(
            sql.SQL("SELECT {} FROM {} ORDER BY id DESC LIMIT 1").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)), table
            )
        ).fetchone()
        values = {**dict(zip(columns, row, strict=True)), **changes}
        values["record"] = psycopg.types.json.Jsonb(values["record"])
        conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                table,
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            ),
            [values[c] for c in columns],
        )


@pytest.mark.pg
def test_fourche_refusee_par_la_base(pg, journal):
    PostgresAuditStore(pg.app, table=journal).append(Sealer(record("c-1")))
    # même prev_hash qu'un maillon existant : une seconde branche serait une fourche
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_copy(pg, journal, thread_id="c-2", chain_hash="f" * 64)


@pytest.mark.pg
def test_un_seul_enregistrement_par_thread_en_base(pg, journal):
    PostgresAuditStore(pg.app, table=journal).append(Sealer(record("c-1")))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_copy(pg, journal, prev_hash="e" * 64, chain_hash="f" * 64)


@pytest.mark.pg
@pytest.mark.parametrize(
    "statement", ["UPDATE {} SET contract_id = 'x'", "DELETE FROM {}"]
)
def test_app_role_ni_modification_ni_suppression(pg, journal, statement):
    PostgresAuditStore(pg.app, table=journal).append(Sealer(record("c-1")))
    with (
        psycopg.connect(pg.app) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(sql.SQL(statement).format(sql.Identifier(journal)))


@pytest.mark.pg
def test_index_uniques_du_vrai_journal(pg):
    migrations.apply(pg.admin)  # idempotente
    with psycopg.connect(pg.admin) as conn:
        rows = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'audit_decisions'"
        ).fetchall()
    unique = [r[0] for r in rows if r[0].startswith("CREATE UNIQUE INDEX")]
    assert any("(thread_id)" in d for d in unique)
    assert any("(prev_hash)" in d for d in unique)


# --- Nom de table : réservé aux tests, jamais hors de psycopg.sql.Identifier -------------

SOURCE = Path(audit_store_module.__file__)


def _names(node) -> set[str]:
    """Noms et attributs lus dans un sous-arbre (table, self._table…)."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _is_sql(call, name) -> bool:
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == name
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "sql"
    )


def test_nom_de_table_seulement_par_sql_identifier():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    # le texte SQL est constant : jamais formaté ni concaténé comme une chaîne
    for call in calls:
        if _is_sql(call, "SQL"):
            assert all(
                isinstance(a, ast.Constant) and isinstance(a.value, str)
                for a in call.args
            ), ast.unparse(call)
    executed = [
        c.args[0]
        for c in calls
        if isinstance(c.func, ast.Attribute) and c.func.attr == "execute" and c.args
    ]
    assert executed, "aucune requête trouvée : test à revoir"
    for query in executed:
        for node in ast.walk(query):
            assert not isinstance(node, ast.JoinedStr), ast.unparse(query)
            assert not (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Mod)
                and isinstance(node.left, ast.Constant)
            ), ast.unparse(query)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "format"
            ):
                assert _is_sql(node.func.value, "SQL"), ast.unparse(query)
        # le nom de la table n'entre dans une requête que déjà passé par Identifier
        assert not {"table", "_table"} & _names(query), ast.unparse(query)
    identifiers = [c for c in calls if _is_sql(c, "Identifier")]
    assert any(ast.unparse(c) == "sql.Identifier(table)" for c in identifiers)


@pytest.mark.pg
def test_nom_de_table_hostile_reste_un_identifiant(pg):
    hostile = 'audit_decisions"; DROP TABLE audit_decisions; --'
    store = PostgresAuditStore(
        pg.admin, table=hostile
    )  # administrateur : pourrait tout
    with pytest.raises(psycopg.errors.UndefinedTable):
        store.entries()
    with pytest.raises(psycopg.errors.UndefinedTable):
        store.append(Sealer(record("c-1")))
    with psycopg.connect(pg.admin) as conn:
        assert conn.execute("SELECT to_regclass('audit_decisions')").fetchone()[0]


def test_nom_de_table_ni_dans_la_cli_ni_dans_la_configuration():
    # CLI : aucune option, dans aucune sous-commande, ne désigne une table
    parser = cli.build_parser()
    subparsers = [
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    ]
    options = [
        option
        for group in subparsers
        for command in group.choices.values()
        for action in command._actions
        for option in [action.dest, *action.option_strings]
    ]
    assert options and not [o for o in options if "table" in o.lower()]

    # configuration : aucun champ, à aucun niveau, ne désigne une table
    def fields(model, prefix=""):
        for name, info in model.model_fields.items():
            yield prefix + name
            annotation = info.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                yield from fields(annotation, f"{prefix}{name}.")

    assert not [f for f in fields(DecisionConfig) if "table" in f.lower()]

    # code applicatif : l'adaptateur n'est jamais construit avec un nom de table
    for path in (Path(cli.__file__).parent).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                "PostgresAuditStore"
            ):
                assert not [k for k in node.keywords if k.arg == "table"], path

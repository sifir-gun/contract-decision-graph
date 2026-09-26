"""CLI phase 1 : `uv run python -m cdg.cli <commande>`.

Racine de composition : lit .env et la configuration, instancie les adaptateurs
(fournisseur LLM, embedding local, corpus PostgreSQL) et lance le graphe.
Sortie JSON sur stdout ; une erreur est rendue en JSON sur stderr, code 1.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

from cdg import settings
from cdg.adapters import fastembed
from cdg.adapters.langgraph import checkpointer, orchestrator
from cdg.adapters.llm import build_provider
from cdg.adapters.postgres import conninfo, migrations, rag_store
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.application import ingestion
from cdg.application.deps import Deps
from cdg.application.extraction import LLMExtractor
from cdg.domain import expiry
from cdg.domain.config import DecisionConfig, load_config
from cdg.domain.models import Decision
from cdg.ports.audit_store import AuditStore

# date d'analyse : jour légal en France, où s'appliquent les textes du corpus
LEGAL_TIMEZONE = ZoneInfo("Europe/Paris")


def today() -> date:
    return datetime.now(LEGAL_TIMEZONE).date()


def open_audit_store() -> AuditStore:
    """Journal d'audit réel, avec le rôle applicatif. Les tests le remplacent par un
    journal jetable : aucune option de la CLI ni de la configuration n'en change la table."""
    return PostgresAuditStore(conninfo.app_conninfo())


def now() -> datetime:
    """Horloge des scellements : heure UTC, avec fuseau."""
    return datetime.now(UTC)


def build_deps(config: DecisionConfig) -> Deps:
    """Dépendances réelles d'une analyse : fournisseur LLM, embedding local, corpus,
    journal d'audit.

    Le fournisseur d'abord : une clé d'API absente échoue avant tout chargement de
    modèle et avant la création du thread.
    """
    llm = build_provider(config.llm)
    embedder = fastembed.FastembedEmbedder(
        config.embedding, settings.embedding_cache_dir()
    )
    retriever = rag_store.PgvectorRetriever(conninfo.app_conninfo(), embedder)
    return Deps(
        extractor=LLMExtractor(llm),
        crag=orchestrator.crag_runner(retriever, llm, config),
        audit_store=open_audit_store(),
        clock=now,
    )


def _not_needed(what: str):
    def fail(*args):
        raise RuntimeError(
            f"{what} indisponible : cette commande n'analyse pas de contrat"
        )

    return fail


def review_deps(config: DecisionConfig) -> Deps:
    """resume, history, expire : le graphe ne repasse ni par l'extraction ni par le CRAG ;
    aucun modèle chargé, aucune clé d'API exigée, un appel échouerait explicitement. Le
    contrat repris est scellé dans le journal réel."""
    return Deps(
        extractor=_not_needed("extraction"),
        crag=_not_needed("CRAG"),
        audit_store=open_audit_store(),
        clock=now,
    )


def _setup_db(args: argparse.Namespace) -> dict:
    admin = conninfo.admin_conninfo()
    checkpointer.setup_database(admin)
    applied = migrations.apply(admin)
    rag_store.check_dimension(admin, load_config().embedding.dimension)
    return {
        "setup_db": "ok",
        "role": settings.APP_ROLE,
        "tables": list(checkpointer.CHECKPOINT_TABLES),
        "droits": ["SELECT", "INSERT", "UPDATE"],
        "migrations": applied,
        "corpus": {"table": "rag_chunks", "droits": ["SELECT"]},
        "journal": {"table": "audit_decisions", "droits": ["SELECT", "INSERT"]},
    }


def _fetch_embedding_model(args: argparse.Namespace) -> dict:
    config = load_config().embedding
    cache_dir = settings.embedding_cache_dir()
    fastembed.fetch_model(config, cache_dir)
    return {
        "fetch_embedding_model": "ok",
        "model": config.model,
        "cache_dir": str(cache_dir),
    }


def _ingest(args: argparse.Namespace) -> dict:
    config = load_config()
    embedder = fastembed.FastembedEmbedder(
        config.embedding, settings.embedding_cache_dir()
    )
    rows = ingestion.rows(embedder, config.corpus.chunk_max_words)
    summary = rag_store.sync(conninfo.admin_conninfo(), rows, embedder.model)
    return {"ingest": "ok", "model": embedder.model, **summary}


def _graph(config: DecisionConfig, deps: Deps | None = None):
    deps = review_deps(config) if deps is None else deps
    return orchestrator.open_graph(config, deps, conninfo.app_conninfo())


def _run(args: argparse.Namespace) -> dict:
    contract = Path(args.contract)
    raw_text = contract.read_text(encoding="utf-8")
    config = load_config()
    deps = build_deps(config)
    with _graph(config, deps) as graph:
        return orchestrator.run_contract(
            graph,
            args.contract_id or contract.stem,
            raw_text,
            parties=args.party,
            analysis_date=args.analysis_date or today(),
            config=config,
        )


def _resume(args: argparse.Namespace) -> dict:
    answer = {
        "decision": args.decision,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "overrides_block": args.overrides_block,
    }
    config = load_config()
    with _graph(config) as graph:
        return orchestrator.resume_thread(graph, args.thread_id, answer, config=config)


def _history(args: argparse.Namespace) -> dict:
    with _graph(load_config()) as graph:
        checkpoints = orchestrator.thread_history(graph, args.thread_id)
    return {"thread_id": args.thread_id, "checkpoints": checkpoints}


def _expire(args: argparse.Namespace) -> dict:
    older_than = expiry.parse_duration(args.older_than)
    now = datetime.now(UTC)
    with _graph(load_config()) as graph:
        expired = orchestrator.expire_threads(graph, older_than, now)
    return {
        "older_than": args.older_than,
        "now": now.isoformat(),
        "expired": expired,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdg", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "setup-db",
        help="crée les tables du checkpointer et donne à app_role SELECT, INSERT, "
        "UPDATE (identifiants administrateur de .env, à lancer une fois)",
    ).set_defaults(handler=_setup_db)

    sub.add_parser(
        "fetch-embedding-model",
        help="télécharge les poids du modèle d'embedding dans EMBEDDING_CACHE_DIR "
        "(réseau, environ 2,2 Go, une seule fois)",
    ).set_defaults(handler=_fetch_embedding_model)

    sub.add_parser(
        "ingest",
        help="nettoie, découpe et indexe le corpus (data/corpus) dans rag_chunks, "
        "identifiants administrateur ; rejouable, supprime les extraits disparus",
    ).set_defaults(handler=_ingest)

    run_notice = (
        "Masque le contrat, puis extrait ses clauses et interroge le juge du CRAG par le "
        "fournisseur LLM de la configuration (appels payants, clé dans .env) ; corpus "
        "indexé par ingest, modèle d'embedding local."
    )
    run = sub.add_parser(
        "run", help="analyse un contrat (appels LLM payants)", description=run_notice
    )
    run.add_argument("contract", help="fichier texte du contrat (synthétique)")
    run.add_argument(
        "--party",
        action="append",
        default=[],
        help="nom d'une partie, masqué avant analyse (option répétable)",
    )
    run.add_argument(
        "--contract-id",
        help="identifiant du contrat et du thread (défaut : nom du fichier)",
    )
    run.add_argument(
        "--analysis-date",
        type=date.fromisoformat,
        help="date à laquelle les versions des textes sont jugées, AAAA-MM-JJ "
        "(défaut : aujourd'hui, heure de Paris)",
    )
    run.set_defaults(handler=_run)

    resume = sub.add_parser("resume", help="reprend un thread en attente d'un humain")
    resume.add_argument("thread_id")
    resume.add_argument("--decision", required=True, choices=get_args(Decision))
    resume.add_argument("--reviewer", required=True)
    resume.add_argument("--reason", required=True)
    resume.add_argument(
        "--overrides-block",
        action="store_true",
        help="lève un blocage dur (motif obligatoire)",
    )
    resume.set_defaults(handler=_resume)

    history = sub.add_parser(
        "history", help="checkpoints d'un thread, du plus ancien au plus récent"
    )
    history.add_argument("thread_id")
    history.set_defaults(handler=_history)

    expire = sub.add_parser(
        "expire",
        help="NO_GO système (motif timeout) pour les threads en attente "
        "d'un humain depuis plus que le délai ; jamais d'approbation",
    )
    expire.add_argument("--older-than", required=True, help="délai : 24h, 30m, 2d…")
    expire.set_defaults(handler=_expire)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings.load_env()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], dict] = args.handler
    try:
        result = handler(args)
    # toute erreur est rendue en JSON structuré, code 1 : jamais de trace brute ni de repli
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"erreur": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

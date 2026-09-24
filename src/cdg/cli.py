"""CLI phase 1 : `uv run python -m cdg.cli <commande>`.

Sortie JSON sur stdout ; une erreur est rendue en JSON sur stderr, code 1.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from cdg import expiry, orchestrator, rag_store, settings, stub_j2
from cdg.config import load_config
from cdg.state import Decision

STUB_NOTICE = (
    "MODE stub-j2, AUCUNE ANALYSE RÉELLE avant le J3 : les clauses sont lues "
    "telles quelles dans --clauses (texte du contrat non analysé) et le CRAG, "
    "sans corpus, répond toujours INSUFFISANT, donc aucun verdict n'est étayé."
)


def _setup_db(args: argparse.Namespace) -> dict:
    orchestrator.setup_database(settings.admin_conninfo())
    rag_store.setup(settings.admin_conninfo(), load_config().embedding.dimension)
    return {
        "setup_db": "ok",
        "role": settings.APP_ROLE,
        "tables": list(orchestrator.CHECKPOINT_TABLES),
        "droits": ["SELECT", "INSERT", "UPDATE"],
        "corpus": {"table": "rag_chunks", "droits": ["SELECT"]},
    }


def _graph(clauses_path: str | None = None):
    return orchestrator.open_graph(
        load_config(), stub_j2.deps(clauses_path), settings.app_conninfo()
    )


def _run(args: argparse.Namespace) -> dict:
    contract = Path(args.contract)
    raw_text = contract.read_text(encoding="utf-8")
    if not Path(args.clauses).is_file():
        raise FileNotFoundError(f"fichier de clauses introuvable : {args.clauses}")
    with _graph(args.clauses) as graph:
        status = orchestrator.run_contract(
            graph, args.contract_id or contract.stem, raw_text, parties=args.party
        )
    return {"mode": stub_j2.MODE, **status}


def _resume(args: argparse.Namespace) -> dict:
    answer = {
        "decision": args.decision,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "overrides_block": args.overrides_block,
    }
    with _graph() as graph:
        status = orchestrator.resume_thread(graph, args.thread_id, answer)
    return {"mode": stub_j2.MODE, **status}


def _history(args: argparse.Namespace) -> dict:
    with _graph() as graph:
        checkpoints = orchestrator.thread_history(graph, args.thread_id)
    return {"mode": stub_j2.MODE, "thread_id": args.thread_id, "checkpoints": checkpoints}


def _expire(args: argparse.Namespace) -> dict:
    older_than = expiry.parse_duration(args.older_than)
    now = datetime.now(UTC)
    with _graph() as graph:
        expired = orchestrator.expire_threads(graph, older_than, now)
    return {
        "mode": stub_j2.MODE,
        "older_than": args.older_than,
        "now": now.isoformat(),
        "expired": expired,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdg", description=__doc__.splitlines()[0], epilog=STUB_NOTICE
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "setup-db",
        help="crée les tables du checkpointer et donne à app_role SELECT, INSERT, "
        "UPDATE (identifiants administrateur de .env, à lancer une fois)",
    ).set_defaults(handler=_setup_db)

    run = sub.add_parser("run", help="analyse un contrat (mode stub-j2)", description=STUB_NOTICE)
    run.add_argument("contract", help="fichier texte du contrat (synthétique)")
    run.add_argument(
        "--clauses", required=True, help="clauses déjà extraites, liste JSON (mode stub-j2)"
    )
    run.add_argument(
        "--party",
        action="append",
        default=[],
        help="nom d'une partie, masqué avant analyse (option répétable)",
    )
    run.add_argument(
        "--contract-id", help="identifiant du contrat et du thread (défaut : nom du fichier)"
    )
    run.set_defaults(handler=_run)

    resume = sub.add_parser("resume", help="reprend un thread en attente d'un humain")
    resume.add_argument("thread_id")
    resume.add_argument("--decision", required=True, choices=get_args(Decision))
    resume.add_argument("--reviewer", required=True)
    resume.add_argument("--reason", required=True)
    resume.add_argument(
        "--overrides-block", action="store_true", help="lève un blocage dur (motif obligatoire)"
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
            json.dumps({"erreur": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

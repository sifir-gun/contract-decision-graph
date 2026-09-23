"""CLI phase 1 : `uv run python -m cdg.cli <commande>`.

Sortie JSON sur stdout ; une erreur est rendue en JSON sur stderr, code 1.
"""

import argparse
import json
import sys
from collections.abc import Callable

from cdg import orchestrator, settings


def _setup_db(args: argparse.Namespace) -> dict:
    orchestrator.setup_database(settings.admin_conninfo())
    return {"setup_db": "ok", "role": settings.APP_ROLE,
            "tables": list(orchestrator.CHECKPOINT_TABLES),
            "droits": ["SELECT", "INSERT", "UPDATE"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdg", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "setup-db",
        help="crée les tables du checkpointer et donne à app_role SELECT, INSERT, "
             "UPDATE (identifiants administrateur de .env, à lancer une fois)",
    ).set_defaults(handler=_setup_db)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings.load_env()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], dict] = args.handler
    try:
        result = handler(args)
    except Exception as exc:   # rendu structuré, jamais de trace brute ni de repli
        print(json.dumps({"erreur": type(exc).__name__, "detail": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

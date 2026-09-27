"""CLI : `uv run python -m cdg.cli <commande>`.

Racine de composition : lit .env et la configuration, instancie les adaptateurs
(fournisseur LLM, embedding local, corpus PostgreSQL) et le service des contrats
(`application/service.py`), commun à la CLI et à l'interface web.
Sortie JSON sur stdout ; une erreur est rendue en JSON sur stderr, code 1.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import get_args
from zoneinfo import ZoneInfo

from cdg import settings
from cdg.adapters import fastembed
from cdg.adapters.demo.audit_store import MemoryAuditStore
from cdg.adapters.demo.extraction import ExpectedExtractor
from cdg.adapters.demo.references import DeclaredCrag
from cdg.adapters.langgraph import checkpointer, orchestrator
from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine, memory_opener
from cdg.adapters.llm import API_KEY_VARS, build_provider
from cdg.adapters.postgres import conninfo, migrations, rag_store
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.adapters.web import app as web_app
from cdg.adapters.web import security as web_security
from cdg.adapters.web import server as web_server
from cdg.application import demo_set, ingestion
from cdg.application.deps import Deps, Explainer, TemplateOnly
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.application.service import ContractService
from cdg.domain import audit, expiry
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
        explainer=LLMExplainer(llm),
    )


def _not_needed(what: str):
    def fail(*args):
        raise RuntimeError(
            f"{what} indisponible : cette commande n'analyse pas de contrat"
        )

    return fail


# expire : décision système, toujours expliquée par le gabarit (un expire planifié reste
# sans clé d'API) ; history n'exécute aucun nœud
EXPIRE_EXPLAINER = TemplateOnly(
    "décision système (expire) : explication par le gabarit, sans LLM"
)
HISTORY_EXPLAINER = TemplateOnly("history : aucun nœud exécuté")


def resume_explainer(config: DecisionConfig) -> Explainer | TemplateOnly:
    """resume : le LLM si la clé d'API du fournisseur est présente, sinon le gabarit, avec
    le motif scellé. La clé n'est pas exigée pour reprendre un contrat."""
    var = API_KEY_VARS[config.llm.provider]
    if not os.environ.get(var):
        return TemplateOnly(f"clé d'API absente ({var}) : explication par le gabarit")
    return LLMExplainer(build_provider(config.llm))


def review_deps(config: DecisionConfig, explainer: Explainer | TemplateOnly) -> Deps:
    """resume, history, expire : le graphe ne repasse ni par l'extraction ni par le CRAG ;
    aucun modèle d'embedding chargé, un appel échouerait explicitement. Seule
    l'explication peut appeler le LLM (resume avec clé). Le contrat repris est scellé dans
    le journal réel."""
    return Deps(
        extractor=_not_needed("extraction"),
        crag=_not_needed("CRAG"),
        audit_store=open_audit_store(),
        clock=now,
        explainer=explainer,
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


def _graph(config: DecisionConfig, deps: Deps):
    return orchestrator.open_graph(config, deps, conninfo.app_conninfo())


def build_service(config: DecisionConfig) -> ContractService:
    """Service des contrats sur la base PostgreSQL. Chaque opération construit ses
    dépendances à l'appel, comme chaque commande : le fournisseur LLM pour run, qui échoue
    d'abord si la clé manque ; l'explication seule pour resume ; rien pour la lecture."""
    engine = LangGraphEngine(
        config,
        lambda deps: _graph(config, deps),
        EngineDeps(
            run=lambda: build_deps(config),
            resume=lambda: review_deps(config, resume_explainer(config)),
            expire=lambda: review_deps(config, EXPIRE_EXPLAINER),
            read=lambda: review_deps(config, HISTORY_EXPLAINER),
        ),
    )
    return ContractService(
        engine=engine,
        audit_store=lambda: open_audit_store(),
        config=config,
        today=lambda: today(),
        now=lambda: now(),
    )


# démonstration : explication toujours par le gabarit, motif scellé en mémoire
DEMO_EXPLAINER = TemplateOnly(
    "démonstration : explication par le gabarit, sans LLM ; extraction simulée"
)


def demo_service(config: DecisionConfig) -> ContractService:
    """Service du mode démonstration de l'interface : graphe réel, checkpointer et journal
    d'audit en mémoire, extraction simulée à partir des attendus du jeu, références par
    rattachement déclaré, explication par le gabarit. Ni clé d'API, ni base, ni coût.
    Date d'analyse par défaut : celle des attendus, pour que le jeu rende ses issues
    documentées quel que soit le jour (versions des textes du corpus)."""
    expected_on, contracts = demo_set.load()
    store = MemoryAuditStore()
    deps = Deps(
        extractor=ExpectedExtractor(contracts.values()),
        crag=DeclaredCrag(config.corpus.chunk_max_words),
        audit_store=store,
        clock=now,
        explainer=DEMO_EXPLAINER,
    )
    engine = LangGraphEngine(
        config,
        memory_opener(config),
        EngineDeps(
            run=lambda: deps,
            resume=lambda: deps,
            expire=lambda: deps,
            read=lambda: deps,
        ),
    )
    return ContractService(
        engine=engine,
        audit_store=lambda: store,
        config=config,
        today=lambda: expected_on,
        now=lambda: now(),
    )


def _run(args: argparse.Namespace) -> dict:
    contract = Path(args.contract)
    raw_text = contract.read_text(encoding="utf-8")
    return build_service(load_config()).analyse(
        raw_text,
        contract_id=args.contract_id or contract.stem,
        parties=args.party,
        analysis_date=args.analysis_date,
    )


def _resume(args: argparse.Namespace) -> dict:
    answer = {
        "decision": args.decision,
        "reviewer": args.reviewer,
        "reason": args.reason,
        "overrides_block": args.overrides_block,
    }
    return build_service(load_config()).decide(args.thread_id, answer)


class ChaineRompue(Exception):
    """Journal d'audit non conforme : code 1, avec le rapport de vérification."""

    def __init__(self, report: audit.ChainReport):
        super().__init__(report.reason)
        self.payload = {
            "enregistrements": report.count,
            "maillon_fautif": report.broken_id,
            "raison": report.reason,
            "tete": report.head,
        }


def _verify(args: argparse.Namespace) -> dict:
    """Vérifie la chaîne du journal d'audit (rôle applicatif, lecture seule)."""
    report = build_service(load_config()).verify(args.expect_head)
    if not report.ok:
        raise ChaineRompue(report)
    return {"verify": "ok", "enregistrements": report.count, "tete": report.head}


def _journal(args: argparse.Namespace) -> dict:
    return {"enregistrements": build_service(load_config()).journal()}


def _replay(args: argparse.Namespace) -> dict:
    return build_service(load_config()).replay(args.thread_id)


def _list(args: argparse.Namespace) -> dict:
    service = build_service(load_config())
    return {"contrats": service.contracts(pending_only=args.en_attente)}


def _show(args: argparse.Namespace) -> dict:
    return build_service(load_config()).dossier(args.thread_id)


def _head(value: str) -> str:
    if not audit.is_hash(value):
        raise argparse.ArgumentTypeError(
            f"empreinte invalide : {value!r} (64 caractères hexadécimaux minuscules)"
        )
    return value


def _history(args: argparse.Namespace) -> dict:
    checkpoints = build_service(load_config()).history(args.thread_id)
    return {"thread_id": args.thread_id, "checkpoints": checkpoints}


def _expire(args: argparse.Namespace) -> dict:
    older_than = expiry.parse_duration(args.older_than)
    at, expired = build_service(load_config()).expire(older_than)
    return {
        "older_than": args.older_than,
        "now": at.isoformat(),
        "expired": expired,
    }


def _web(args: argparse.Namespace) -> dict:
    """Interface web, sur le même service que les autres commandes ; jusqu'à Ctrl+C."""
    warning = web_security.bind_warning(
        args.host, allow_non_local=args.ecoute_non_locale
    )
    config = load_config()
    service = demo_service(config) if args.demo else build_service(config)
    app = web_app.create_app(
        service, demo=args.demo, hosts=web_security.allowed_hosts(args.host)
    )
    if warning:
        print(warning, file=sys.stderr)
    shown = f"[{args.host}]" if ":" in args.host else args.host
    mode = "démonstration, en mémoire" if args.demo else "réel"
    print(
        f"Interface ({mode}) : http://{shown}:{args.port} ; Ctrl+C pour l'arrêter.",
        file=sys.stderr,
    )
    web_server.serve(app, args.host, args.port)
    return {"web": "arrêtée"}


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
        "Masque le contrat, puis extrait ses clauses, interroge le juge du CRAG et rédige "
        "l'explication par le fournisseur LLM de la configuration (appels payants, clé "
        "dans .env) ; corpus indexé par ingest, modèle d'embedding local."
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
        help="identifiant du contrat et du thread (défaut : nom du fichier) ; "
        "lettres, chiffres, points, tirets et soulignés, 100 caractères au plus, "
        "en commençant par une lettre ou un chiffre",
    )
    run.add_argument(
        "--analysis-date",
        type=date.fromisoformat,
        help="date à laquelle les versions des textes sont jugées, AAAA-MM-JJ "
        "(défaut : aujourd'hui, heure de Paris)",
    )
    run.set_defaults(handler=_run)

    resume = sub.add_parser(
        "resume",
        help="reprend un thread en attente d'un humain ; explication par le LLM si la "
        "clé d'API est présente (appel payant), sinon par le gabarit",
    )
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
        "d'un humain depuis plus que le délai ; jamais d'approbation ; explication "
        "par le gabarit, sans LLM",
    )
    expire.add_argument("--older-than", required=True, help="délai : 24h, 30m, 2d…")
    expire.set_defaults(handler=_expire)

    verify = sub.add_parser(
        "verify",
        help="vérifie la chaîne du journal d'audit (lecture seule) ; code 1 et premier "
        "maillon fautif si un enregistrement a été modifié, supprimé ou déplacé",
    )
    verify.add_argument(
        "--expect-head",
        type=_head,
        help="empreinte de la tête de chaîne conservée hors de la base : échoue si la "
        "tête diffère (fin du journal tronquée)",
    )
    verify.set_defaults(handler=_verify)

    sub.add_parser(
        "journal",
        help="enregistrements scellés du journal d'audit, du plus ancien au plus récent "
        "(lecture seule)",
    ).set_defaults(handler=_journal)

    replay = sub.add_parser(
        "replay",
        help="rejoue la décision scellée d'un thread, sans LLM ni corpus : règles, "
        "justification et décision recalculées, empreintes comparées",
    )
    replay.add_argument("thread_id")
    replay.set_defaults(handler=_replay)

    listing = sub.add_parser(
        "list",
        help="contrats du checkpointer, du plus récemment modifié au plus ancien",
    )
    listing.add_argument(
        "--en-attente",
        action="store_true",
        help="seulement les contrats en attente d'une revue humaine",
    )
    listing.set_defaults(handler=_list)

    show = sub.add_parser(
        "show",
        help="dossier d'un contrat : statut, texte masqué, clauses, verdicts et "
        "références, alertes, parcours, consommation, empreintes",
    )
    show.add_argument("thread_id")
    show.set_defaults(handler=_show)

    web = sub.add_parser(
        "web",
        help="interface web : mêmes actions que la CLI, par le même service ; écoute "
        "locale par défaut, sans authentification",
    )
    web.add_argument("--host", default="127.0.0.1", help="adresse d'écoute")
    web.add_argument("--port", type=int, default=8000, help="port d'écoute")
    web.add_argument(
        "--demo",
        action="store_true",
        help="démonstration : sans clé d'API, sans coût, sans base ; extraction "
        "simulée pour les contrats du jeu, rien n'est scellé dans le vrai journal",
    )
    web.add_argument(
        "--ecoute-non-locale",
        action="store_true",
        help="autorise une adresse non locale : l'interface n'a pas "
        "d'authentification, avertissement affiché",
    )
    web.set_defaults(handler=_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings.load_env()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], dict] = args.handler
    try:
        result = handler(args)
    # toute erreur est rendue en JSON structuré, code 1 : jamais de trace brute ni de repli
    except Exception as exc:  # noqa: BLE001
        error = {"erreur": type(exc).__name__, "detail": str(exc)}
        payload = getattr(exc, "payload", None)  # rapport structuré, s'il y en a un
        if isinstance(payload, dict):
            error |= payload
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

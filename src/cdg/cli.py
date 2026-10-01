"""CLI : `uv run python -m cdg.cli <commande>`.

Racine de composition : lit .env et la configuration, instancie les adaptateurs
(fournisseur LLM, embedding local, corpus PostgreSQL) et le service des contrats
(`application/service.py`), commun à la CLI et à l'interface web.
Sortie JSON sur stdout ; une erreur est rendue en JSON sur stderr, code 1.
"""

import argparse
import atexit
import functools
import json
import logging
import logging.config
import os
import sys
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO, get_args
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from cdg import settings
from cdg.adapters import fastembed, journaux, oidc
from cdg.adapters.demo.audit_store import MemoryAuditStore
from cdg.adapters.demo.extraction import ExpectedExtractor
from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.demo.references import DeclaredCrag
from cdg.adapters.demo.resumes import LocalResumeCounter
from cdg.adapters.langgraph import checkpointer, orchestrator
from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine, memory_opener
from cdg.adapters.llm import API_KEY_VARS, build_provider
from cdg.adapters.postgres import connexions, conninfo, migrations, rag_store
from cdg.adapters.postgres.audit_store import PostgresAuditStore
from cdg.adapters.postgres.locks import PostgresContractLocks
from cdg.adapters.postgres.resumes import PostgresResumeCounter
from cdg.adapters.web import acces, sante
from cdg.adapters.web import app as web_app
from cdg.adapters.web import security as web_security
from cdg.adapters.web import server as web_server
from cdg.application import demo_set, ingestion
from cdg.application.deps import Deps, Explainer, TemplateOnly
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.application.service import ContractService
from cdg.domain import audit, authorization, expiry
from cdg.domain.authorization import Actor
from cdg.domain.config import DecisionConfig, load_config
from cdg.domain.models import Decision
from cdg.domain.version import UNKNOWN as UNKNOWN_COMMIT
from cdg.domain.version import CodeVersion
from cdg.ports.audit_store import AuditStore

# date d'analyse : jour légal en France, où s'appliquent les textes du corpus
LEGAL_TIMEZONE = ZoneInfo("Europe/Paris")


def today() -> date:
    return datetime.now(LEGAL_TIMEZONE).date()


# taille du pool d'app_role : option --connexions, ou CDG_CONNEXIONS (main)
POOL = {"size": connexions.DEFAULT_SIZE}
# fils de calcul et taille des lots de l'embedder : options --fils-embedding et
# --lot-embedding, ou CDG_FILS_EMBEDDING et CDG_LOT_EMBEDDING (main)
EMBEDDER_THREADS: dict[str, int | None] = {"threads": None, "batch_size": None}


@functools.cache
def app_pool() -> connexions.Source:
    """Pool d'app_role du processus : ouvert au premier usage, fermé à la sortie. Le
    checkpointer, le journal, la recherche et les verrous y prennent leurs connexions."""
    # la fonction, relue à chaque nouvelle connexion : rotation du mot de passe sans
    # redémarrage (ADR 005)
    pool = connexions.open_pool(conninfo.app_conninfo, max_size=POOL["size"])
    atexit.register(pool.close)
    return pool


# délai laissé aux requêtes en cours à l'ordre d'arrêt ; le chart aligne dessus le délai
# de grâce du pod (pause avant l'arrêt + ce délai + marge)
DEFAULT_GRACE_SECONDS = 60
# intervalle de la reprise des analyses interrompues (mode réel)
DEFAULT_RESUME_SECONDS = 60
_EMBEDDERS: dict[str, fastembed.FastembedEmbedder] = {}
_EMBEDDERS_GUARD = threading.Lock()
log = logging.getLogger(__name__)


def process_embedder(config: DecisionConfig) -> fastembed.FastembedEmbedder:
    """Embedder du processus : poids chargés une fois, puis réutilisés par chaque analyse
    (ONNX Runtime accepte les appels simultanés sur une même session)."""
    key = config.embedding.model_dump_json()
    with _EMBEDDERS_GUARD:
        if key not in _EMBEDDERS:
            _EMBEDDERS[key] = fastembed.FastembedEmbedder(
                config.embedding,
                settings.embedding_cache_dir(),
                threads=EMBEDDER_THREADS["threads"],
                batch_size=EMBEDDER_THREADS["batch_size"],
            )
        return _EMBEDDERS[key]


def open_audit_store() -> AuditStore:
    """Journal d'audit réel, avec le rôle applicatif. Les tests le remplacent par un
    journal jetable : aucune option de la CLI ni de la configuration n'en change la table."""
    return PostgresAuditStore(app_pool())


def now() -> datetime:
    """Horloge des scellements : heure UTC, avec fuseau."""
    return datetime.now(UTC)


COMMIT_VAR = "CDG_COMMIT"  # posée par l'image, à sa construction (Dockerfile)
IMAGE_VAR = "CDG_EMPREINTE_IMAGE"  # posée par le chart, au lancement


class VersionError(Exception):
    """Version du code mal fournie : le programme s'arrête, jamais de repli."""


def code_version() -> CodeVersion:
    """Version du code qui décide, scellée avec chaque enregistrement : le commit, fourni
    à la construction de l'image, et l'empreinte de l'image, fournie au lancement par le
    chart. Jamais devinée : ni git, ni registre. Une valeur fournie mais mal formée
    arrête le programme. Sans commit (poste de développement) : « inconnu », scellé tel
    quel ; dans le cluster, commit connu et empreinte de l'image sont exigés."""
    try:
        version = CodeVersion(
            commit=os.environ.get(COMMIT_VAR, UNKNOWN_COMMIT),
            image=os.environ.get(IMAGE_VAR),
        )
    except ValidationError as exc:
        fields = {str(error["loc"][0]) for error in exc.errors()}
        names = [
            var
            for field, var in (("commit", COMMIT_VAR), ("image", IMAGE_VAR))
            if field in fields
        ]
        raise VersionError(
            f"{', '.join(names)} mal formée : commit complet (40 caractères "
            f"hexadécimaux) ou « {UNKNOWN_COMMIT} » ; empreinte sha256:… de l'image"
        ) from None
    if in_cluster():
        missing = []
        if version.commit == UNKNOWN_COMMIT:
            missing.append(
                f"{COMMIT_VAR} « {UNKNOWN_COMMIT} » ou absente : image construite sans "
                f"le commit de sa construction (--build-arg {COMMIT_VAR}, depuis un "
                "contexte identique au commit)"
            )
        if version.image is None:
            missing.append(
                f"{IMAGE_VAR} absente : empreinte de l'image, que le chart fournit"
            )
        if missing:
            raise VersionError(
                "dans le cluster, la version du code est exigée, scellée avec chaque "
                "décision : " + " ; ".join(missing)
            )
    return version


def build_deps(config: DecisionConfig, code: CodeVersion) -> Deps:
    """Dépendances réelles d'une analyse : fournisseur LLM, embedding local, corpus,
    journal d'audit, version du code.

    Le fournisseur d'abord : une clé d'API absente échoue avant tout chargement de
    modèle et avant la création du thread.
    """
    llm = build_provider(config.llm)
    retriever = rag_store.PgvectorRetriever(app_pool(), process_embedder(config))
    return Deps(
        extractor=LLMExtractor(llm),
        crag=orchestrator.crag_runner(retriever, llm, config),
        audit_store=open_audit_store(),
        clock=now,
        explainer=LLMExplainer(llm),
        code_version=code,
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
    if not settings.secret(var):
        return TemplateOnly(f"clé d'API absente ({var}) : explication par le gabarit")
    return LLMExplainer(build_provider(config.llm))


def review_deps(
    config: DecisionConfig, explainer: Explainer | TemplateOnly, code: CodeVersion
) -> Deps:
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
        code_version=code,
    )


def _setup_db(args: argparse.Namespace) -> dict:
    """Amorce une base vide (toutes les migrations, dans l'ordre) ou met à jour une base
    existante ; relancé, ne change rien. Le rôle applicatif d'abord, s'il manque (mot de
    passe haché côté client), car migrations et checkpointer lui donnent ses droits."""
    admin = conninfo.admin_conninfo  # relue à chaque connexion
    created = migrations.ensure_role_at(
        admin, settings.APP_ROLE, lambda: settings.require_secret("APP_DB_PASSWORD")
    )
    applied = migrations.apply(admin)
    checkpointer.setup_database(admin)
    rag_store.check_dimension(admin, load_config().embedding.dimension)
    return {
        "setup_db": "ok",
        "role": settings.APP_ROLE,
        "role_cree": created,
        "tables": list(checkpointer.CHECKPOINT_TABLES),
        "droits": ["SELECT", "INSERT", "UPDATE"],
        "migrations": applied,
        "corpus": {"table": "rag_chunks", "droits": ["SELECT"]},
        "journal": {"table": "audit_decisions", "droits": ["SELECT", "INSERT"]},
        "archive": {
            "table": "audit_decisions_configurations",
            "droits": ["SELECT", "INSERT"],
        },
        "reprises": {
            "table": "contract_resumes",
            "droits": ["SELECT", "INSERT", "UPDATE"],
        },
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
        config.embedding,
        settings.embedding_cache_dir(),
        threads=EMBEDDER_THREADS["threads"],
        batch_size=EMBEDDER_THREADS["batch_size"],
    )
    rows = ingestion.rows(embedder, config.corpus.chunk_max_words)
    summary = rag_store.sync(conninfo.admin_conninfo, rows, embedder.model)
    return {"ingest": "ok", "model": embedder.model, **summary}


def _graph(config: DecisionConfig, deps: Deps):
    return orchestrator.open_graph(config, deps, app_pool())


def build_service(config: DecisionConfig) -> ContractService:
    """Service des contrats sur la base PostgreSQL. Chaque opération construit ses
    dépendances à l'appel, comme chaque commande : le fournisseur LLM pour run, qui échoue
    d'abord si la clé manque ; l'explication seule pour resume ; rien pour la lecture.
    La version du code est lue d'abord : mal fournie, rien ne démarre."""
    code = code_version()
    engine = LangGraphEngine(
        config,
        lambda deps: _graph(config, deps),
        EngineDeps(
            run=lambda: build_deps(config, code),
            resume=lambda: review_deps(config, resume_explainer(config), code),
            expire=lambda: review_deps(config, EXPIRE_EXPLAINER, code),
            read=lambda: review_deps(config, HISTORY_EXPLAINER, code),
        ),
        PostgresContractLocks(app_pool),
        PostgresResumeCounter(app_pool),
    )
    return ContractService(
        engine=engine,
        audit_store=lambda: open_audit_store(),
        config=config,
        today=lambda: today(),
        now=lambda: now(),
        code_version=code,
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
    code = code_version()
    expected_on, contracts = demo_set.load()
    store = MemoryAuditStore()
    deps = Deps(
        extractor=ExpectedExtractor(contracts.values()),
        crag=DeclaredCrag(config.corpus.chunk_max_words),
        audit_store=store,
        clock=now,
        explainer=DEMO_EXPLAINER,
        code_version=code,
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
        LocalContractLocks(),  # démonstration : un seul processus
        LocalResumeCounter(),
    )
    return ContractService(
        engine=engine,
        audit_store=lambda: store,
        config=config,
        today=lambda: expected_on,
        now=lambda: now(),
        code_version=code,
    )


class AccesRefuse(Exception):
    """Décision par la CLI refusée : dans le cluster, en accès d'urgence seulement."""


URGENCY_MAX_CHARS = 200
# sortie du processus principal du conteneur : celle que Kubernetes collecte ; celle d'un
# processus lancé par kubectl exec part vers le terminal de l'opérateur
CONTAINER_LOG = Path("/proc/1/fd/1")


def in_cluster() -> bool:
    """Dans un pod : le kubelet pose toujours cette variable, quel que soit le chart."""
    return "KUBERNETES_SERVICE_HOST" in os.environ


def _cli_actor(args: argparse.Namespace, command: str, *, decides: bool) -> Actor:
    """Acteur de la CLI : un opérateur non nominatif. Dans le cluster, une décision
    (resume, expire) n'est admise qu'en accès d'urgence (--urgence MOTIF) : tracé au
    journal des accès, et scellé avec la décision."""
    urgency = getattr(args, "urgence", None)
    if urgency is not None and not (0 < len(urgency.strip()) <= URGENCY_MAX_CHARS):
        raise AccesRefuse(
            f"--urgence : un motif, {URGENCY_MAX_CHARS} caractères au plus, sans donnée "
            "personnelle"
        )
    if decides and in_cluster() and urgency is None:
        raise AccesRefuse(
            f"{command} : dans le cluster, une décision par la CLI n'est admise qu'en "
            "accès d'urgence (--urgence MOTIF, tracé au journal des accès et scellé) ; "
            "la voie normale est l'interface authentifiée"
        )
    try:
        actor = Actor(
            canal="cli",
            authentifie=False,
            operateur=args.operateur,
            urgence=urgency is not None,
        )
    except ValidationError:
        raise AccesRefuse(
            "--operateur : identifiant non nominatif (minuscules, chiffres, tirets), "
            "jamais un nom ni une adresse"
        ) from None
    if urgency is not None:
        _trace_urgency(args.operateur, command, urgency.strip())
    return actor


def _trace_urgency(operator: str, command: str, reason: str) -> None:
    """Accès d'urgence au journal des accès : sur la sortie du processus, et, dans le
    cluster, sur celle du processus principal du conteneur, collectée ; si elle est
    inaccessible, l'accès est refusé, jamais laissé sans trace."""
    log = logging.getLogger("cdg.acces")
    handler: logging.StreamHandler[TextIO] | None = None
    if in_cluster():
        try:
            stream = CONTAINER_LOG.open("a", encoding="utf-8")
        except OSError as exc:
            raise AccesRefuse(
                "accès d'urgence refusé : trace impossible dans le journal du conteneur "
                f"({CONTAINER_LOG}, {type(exc).__name__})"
            ) from None
        handler = logging.StreamHandler(stream)
        handler.setFormatter(journaux.JsonFormatter())
        log.addHandler(handler)
    try:
        acces.event("acces_urgence", operateur=operator, commande=command, motif=reason)
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()
            handler.stream.close()


def _run(args: argparse.Namespace) -> dict:
    contract = Path(args.contract)
    actor = _cli_actor(args, "run", decides=False)
    raw_text = contract.read_text(encoding="utf-8")
    return build_service(load_config()).analyse(
        raw_text,
        contract_id=args.contract_id or contract.stem,
        actor=actor,
        parties=args.party,
        analysis_date=args.analysis_date,
    )


def _relaunch(args: argparse.Namespace) -> dict:
    """Relance, sous la configuration actuelle, un contrat escaladé pour changement de
    configuration : une analyse (appels LLM payants), pas une décision."""
    actor = _cli_actor(args, "relaunch", decides=False)
    return build_service(load_config()).relaunch(
        args.thread_id, actor=actor, contract_id=args.identifiant
    )


def _resume(args: argparse.Namespace) -> dict:
    actor = _cli_actor(args, "resume", decides=True)
    answer = {
        "decision": args.decision,
        "acteur": actor.model_dump(mode="json"),
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


class ArchiveNonConforme(ChaineRompue):
    """Archive des configurations non conforme (configuration d'un enregistrement v2
    absente, ou configuration archivée altérée) : code 1, avec le rapport."""


def _verify(args: argparse.Namespace) -> dict:
    """Vérifie la chaîne du journal d'audit, puis l'archive des configurations (rôle
    applicatif, lecture seule)."""
    report = build_service(load_config()).verify(args.expect_head)
    if not report.ok:
        raise (ArchiveNonConforme if report.archive_fault else ChaineRompue)(report)
    return {
        "verify": "ok",
        "enregistrements": report.count,
        "tete": report.head,
        "configurations_archivees": report.archived,
        "v1_sans_archive": report.v1_exempted,
    }


def _journal(args: argparse.Namespace) -> dict:
    return {"enregistrements": build_service(load_config()).journal()}


class RejeuAnomalie(Exception):
    """Rejeu fidèle (même code, même configuration) différent de la décision scellée :
    anomalie, code 1, avec le rapport du rejeu."""

    def __init__(self, result: dict):
        super().__init__(
            "rejeu fidèle différent de la décision scellée : journal altéré ou calcul "
            "non déterministe"
        )
        self.payload = result


def _replay(args: argparse.Namespace) -> dict:
    """Rejoue une décision scellée : une différence est une anomalie en rejeu fidèle
    (code 1), signalée sans erreur en réévaluation."""
    result = build_service(load_config()).replay(args.thread_id)
    if result["anomalie"]:
        raise RejeuAnomalie(result)
    return result


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
    actor = _cli_actor(args, "expire", decides=True)
    at, expired = build_service(load_config()).expire(older_than, actor)
    return {
        "older_than": args.older_than,
        "now": at.isoformat(),
        "expired": expired,
    }


def _tell(args: argparse.Namespace, level: int, message: str) -> None:
    """Message à l'opérateur : sur la sortie d'erreur au terminal (journaux texte), dans le
    journal en json, où un collecteur lit la sortie ligne à ligne (Kubernetes)."""
    if args.journaux == "json":
        log.log(level, message)
    else:
        print(message, file=sys.stderr)


def _authentication(args: argparse.Namespace) -> web_app.Authentication | None:
    """`--identite en-tetes` : derrière oauth2-proxy, dans le même pod, seul chemin vers
    l'interface ; elle n'écoute donc que sur 127.0.0.1, et vérifie le jeton d'identité
    de chaque requête. Toute option manquante ou contradictoire arrête le lancement."""
    if args.identite == "aucune":
        if in_cluster():
            raise web_security.WebConfigError(
                "dans le cluster, l'interface exige --identite en-tetes : jamais "
                "d'interface locale, non authentifiée, derrière un réseau"
            )
        return None
    if args.host != "127.0.0.1":
        raise web_security.WebConfigError(
            f"--identite en-tetes : l'interface n'écoute que sur 127.0.0.1 (reçu --host "
            f"{args.host}) ; oauth2-proxy, dans le même pod, est le seul chemin vers elle"
        )
    if args.ecoute_non_locale:
        raise web_security.WebConfigError(
            "--identite en-tetes et --ecoute-non-locale sont incompatibles : l'interface "
            "n'écoute que sur 127.0.0.1"
        )
    for option, value in (
        ("--oidc-emetteur", args.oidc_emetteur),
        ("--oidc-audience", args.oidc_audience),
        ("--adresse-publique", args.adresse_publique),
        # plusieurs réplicas : un formulaire servi par l'un est envoyé à l'autre
        ("--cles-csrf", args.cles_csrf),
        # rôles tirés des groupes du jeton (PR D2)
        ("--groupes-analyste", args.groupes_analyste),
        ("--groupes-relecteur", args.groupes_relecteur),
    ):
        if not value:
            raise web_security.WebConfigError(f"--identite en-tetes exige {option}")
    public = urlsplit(args.adresse_publique)
    if public.scheme != "https" or not public.netloc or public.path not in ("", "/"):
        raise web_security.WebConfigError(
            f"--adresse-publique : https://hôte attendu, sans chemin (reçu "
            f"{args.adresse_publique})"
        )
    roles = {
        "analyste": _names(args.groupes_analyste),
        "relecteur": _names(args.groupes_relecteur),
    }
    try:
        authorization.check_mapping(roles)
    except authorization.AuthorizationConfigError as exc:
        raise web_security.WebConfigError(f"--groupes-… : {exc}") from None
    return web_app.Authentication(
        verifier=oidc.OidcVerifier(
            args.oidc_emetteur, args.oidc_audience, ca_file=args.oidc_ca
        ),
        public_origin=f"https://{public.netloc}",
        client_id=args.oidc_audience,
        roles=roles,
        second_factor=authorization.SecondFactor(
            amr=_names(args.second_facteur_amr), acr=_names(args.second_facteur_acr)
        ),
        provider_logout=args.deconnexion_fournisseur,
    )


def _names(value: str | None) -> tuple[str, ...]:
    """Liste séparée par des virgules, sans vides."""
    return tuple(name.strip() for name in (value or "").split(",") if name.strip())


def _csrf_keys(
    args: argparse.Namespace,
) -> Callable[[], web_security.CsrfKeys] | None:
    """Clés CSRF partagées entre réplicas (Secret monté), lues au lancement (une clé
    absente ou trop courte l'arrête), puis relues à chaque usage : une rotation
    s'applique sans redémarrage. Sans --cles-csrf : une clé tirée par le processus."""
    if not args.cles_csrf:
        return None
    folder = Path(args.cles_csrf)
    web_security.read_csrf_keys(folder)
    return lambda: web_security.read_csrf_keys(folder)


def _web(args: argparse.Namespace) -> dict:
    """Interface web, sur le même service que les autres commandes ; jusqu'à Ctrl+C."""
    authentication = _authentication(args)  # avant tout : une option fausse arrête là
    csrf_keys = _csrf_keys(args)
    warning = web_security.bind_warning(
        args.host, allow_non_local=args.ecoute_non_locale
    )
    config = load_config()
    service = demo_service(config) if args.demo else build_service(config)
    # ordre d'arrêt reçu : l'interface refuse toute modification, les sondes ne sont
    # plus prêtes, la reprise des analyses interrompues s'arrête
    stopping = threading.Event()
    app = web_app.create_app(
        service,
        demo=args.demo,
        hosts=web_security.allowed_hosts(args.host),
        draining=stopping.is_set,
        authentication=authentication,
        csrf_keys=csrf_keys,
    )
    shown = f"[{args.host}]" if ":" in args.host else args.host
    mode = "démonstration, en mémoire" if args.demo else "réel"

    def opened() -> None:
        """Port à l'écoute : l'interface s'annonce, puis la reprise des analyses
        interrompues commence. Sur un port déjà pris, ni annonce ni reprise."""
        if warning:
            _tell(args, logging.WARNING, warning)
        if authentication is not None and not authentication.second_factor.required:
            _tell(
                args,
                logging.WARNING,
                "AVERTISSEMENT : second facteur non exigé pour trancher et expirer "
                "(--second-facteur-amr, --second-facteur-acr) : prérequis de production "
                "(ADR 005)",
            )
        _tell(
            args,
            logging.INFO,
            f"Interface ({mode}) : http://{shown}:{args.port} ; Ctrl+C pour l'arrêter.",
        )
        if not args.demo:  # démonstration : tout en mémoire, rien à reprendre
            threading.Thread(
                target=resume_periodically,
                args=(service, args.reprise_intervalle, stopping),
                name="reprise",
                daemon=True,
            ).start()

    probes = _probes(args, config, stopping)
    web_server.serve(
        app,
        args.host,
        args.port,
        log_config=journaux.config(args.journaux),
        probes=probes,
        on_exit=stopping.set,
        grace_seconds=args.delai_arret,
        on_started=opened,
    )
    return {"web": "arrêtée"}


class ConfigChangeBlocked(Exception):
    """Des contrats en attente ont été analysés sous une autre configuration."""

    def __init__(self, check: dict[str, Any]):
        names = ", ".join(c["thread_id"] for c in check["a_trancher"])
        super().__init__(
            f"contrats en attente sous une autre configuration : {names} ; les trancher "
            "(resume, sous leur configuration) ou les expirer (expire) avant le "
            "changement, ou garder l'ancienne configuration (les contrats escaladés "
            "pour changement de configuration, eux, se tranchent sous l'actuelle)"
        )
        self.payload = check


def _config_check(args: argparse.Namespace) -> dict[str, Any]:
    check = build_service(load_config()).config_check()
    if check["a_trancher"]:
        raise ConfigChangeBlocked(check)
    return check


def resume_periodically(
    service: ContractService, interval: float, stopping: threading.Event
) -> None:
    """Reprend les analyses interrompues au démarrage, puis toutes les `interval`
    secondes, jusqu'à l'ordre d'arrêt. Une reprise qui échoue est journalisée, par son
    type, et retentée au tour suivant."""
    while not stopping.is_set():
        try:
            resumed = service.resume_interrupted()
            if resumed:
                log.info("analyses interrompues reprises : %d", len(resumed))
        # retentée au tour suivant ; le journal dit laquelle a échoué, par son type
        except Exception as exc:  # noqa: BLE001
            log.error(
                "reprise des analyses interrompues en échec (%s)", type(exc).__name__
            )
        stopping.wait(interval)


def _probes(
    args: argparse.Namespace, config: DecisionConfig, stopping: threading.Event
) -> web_server.Probes | None:
    """En mode réel, le modèle d'embedding se charge en arrière-plan dès le lancement ;
    les sondes, si un port leur est donné, disent quand il est chargé et si la base
    répond. En démonstration, ni modèle ni base : prêtes aussitôt."""
    started = threading.Event()
    if args.demo:
        started.set()
    else:
        threading.Thread(
            target=_warm_up, args=(config, started), name="modele", daemon=True
        ).start()
    if args.port_sante is None:
        return None
    checks = sante.Checks(
        started=started.is_set,
        database=(lambda: True) if args.demo else lambda: connexions.ping(app_pool()),
        draining=stopping.is_set,
    )
    return web_server.Probes(
        sante.create_health_app(checks), args.hote_sante, args.port_sante
    )


def _warm_up(config: DecisionConfig, started: threading.Event) -> None:
    try:
        process_embedder(config)
    # un modèle qui ne se charge pas : la sonde de démarrage échoue, le journal dit pourquoi
    except Exception as exc:  # noqa: BLE001
        log.error("modèle d'embedding non chargé (%s)", type(exc).__name__)
        return
    started.set()


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"entier ≥ 1 attendu : {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdg", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--journaux",
        choices=journaux.FORMATS,
        default=os.environ.get("CDG_JOURNAUX", "texte"),
        help="format des journaux sur la sortie standard : texte (défaut) ou json "
        "(Kubernetes) ; défaut aussi lu dans CDG_JOURNAUX",
    )
    parser.add_argument(
        "--connexions",
        type=_positive,
        default=os.environ.get("CDG_CONNEXIONS", str(connexions.DEFAULT_SIZE)),
        help="taille maximale du pool de connexions d'app_role à PostgreSQL (défaut "
        f"{connexions.DEFAULT_SIZE}, ou CDG_CONNEXIONS) ; budget dans l'ADR 005",
    )
    parser.add_argument(
        "--fils-embedding",
        type=_positive,
        default=os.environ.get("CDG_FILS_EMBEDDING"),
        help="fils de calcul de l'embedder (ou CDG_FILS_EMBEDDING) : la limite CPU du pod "
        "(mesure dans l'ADR 005) ; par défaut, un par cœur visible (onnxruntime)",
    )
    parser.add_argument(
        "--lot-embedding",
        type=_positive,
        default=os.environ.get("CDG_LOT_EMBEDDING"),
        help="textes embarqués à la fois (ou CDG_LOT_EMBEDDING) : borne le pic mémoire de "
        "l'ingestion (mesure dans l'ADR 005) ; par défaut, celui de fastembed (256)",
    )
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

    OPERATOR_HELP = (
        "qui agit : identifiant d'opérateur non nominatif (minuscules, chiffres, "
        "tirets), scellé ; jamais un nom ni une adresse"
    )
    URGENCY_HELP = (
        "accès d'urgence, avec son motif (sans donnée personnelle) : seul moyen de "
        "décider par la CLI dans le cluster ; tracé au journal des accès et scellé"
    )
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
    run.add_argument("--operateur", required=True, help=OPERATOR_HELP)
    run.set_defaults(handler=_run)

    resume = sub.add_parser(
        "resume",
        help="reprend un thread en attente d'un humain ; explication par le LLM si la "
        "clé d'API est présente (appel payant), sinon par le gabarit",
    )
    resume.add_argument("thread_id")
    resume.add_argument("--decision", required=True, choices=get_args(Decision))
    resume.add_argument("--operateur", required=True, help=OPERATOR_HELP)
    resume.add_argument("--reason", required=True)
    resume.add_argument("--urgence", metavar="MOTIF", help=URGENCY_HELP)
    resume.add_argument(
        "--overrides-block",
        action="store_true",
        help="lève un blocage dur (motif obligatoire)",
    )
    resume.set_defaults(handler=_resume)

    relaunch = sub.add_parser(
        "relaunch",
        help="relance, sous la configuration actuelle, l'analyse d'un contrat escaladé "
        "pour changement de configuration (nouveau contrat, appels LLM payants) ; le "
        "contrat escaladé reste en attente",
    )
    relaunch.add_argument("thread_id")
    relaunch.add_argument(
        "--identifiant",
        help="identifiant du nouveau contrat (défaut : <thread_id>-relance)",
    )
    relaunch.add_argument("--operateur", required=True, help=OPERATOR_HELP)
    relaunch.set_defaults(handler=_relaunch)

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
    expire.add_argument("--operateur", required=True, help=OPERATOR_HELP)
    expire.add_argument("--urgence", metavar="MOTIF", help=URGENCY_HELP)
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

    sub.add_parser(
        "config-check",
        help="échoue (code 1) si des contrats en attente ont été analysés sous une autre "
        "configuration que la courante : resume les refuserait. Avant un changement de "
        "configuration (tâche Helm)",
    ).set_defaults(handler=_config_check)

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
    web.add_argument(
        "--delai-arret",
        type=_positive,
        default=DEFAULT_GRACE_SECONDS,
        help="secondes laissées aux requêtes en cours à l'ordre d'arrêt (défaut "
        f"{DEFAULT_GRACE_SECONDS}) ; le délai de grâce du pod doit le dépasser",
    )
    web.add_argument(
        "--reprise-intervalle",
        type=_positive,
        default=DEFAULT_RESUME_SECONDS,
        help="secondes entre deux reprises des analyses interrompues (mode réel, "
        f"défaut {DEFAULT_RESUME_SECONDS}) ; la première au lancement",
    )
    web.add_argument(
        "--port-sante",
        type=_positive,
        default=None,
        help="port des sondes de santé de Kubernetes (/sante/vie, /sante/demarrage, "
        "/sante/pret) ; aucune sonde par défaut",
    )
    web.add_argument(
        "--hote-sante",
        default="127.0.0.1",
        help="adresse d'écoute des sondes (0.0.0.0 dans un pod : le kubelet appelle "
        "l'adresse du pod) ; elles ne servent aucune donnée",
    )
    web.add_argument(
        "--identite",
        choices=("aucune", "en-tetes"),
        default="aucune",
        help="en-tetes : derrière oauth2-proxy (chart), jeton d'identité signé exigé et "
        "vérifié à chaque requête ; l'interface n'écoute alors que sur 127.0.0.1",
    )
    web.add_argument("--oidc-emetteur", help="émetteur OIDC attendu (https)")
    web.add_argument("--oidc-audience", help="audience attendue : le client OIDC")
    web.add_argument(
        "--oidc-ca",
        help="autorités de confiance du fournisseur, en fichier (défaut : du système)",
    )
    web.add_argument(
        "--adresse-publique",
        help="adresse publique de l'interface, https://hôte : origine des formulaires, "
        "retour après la fin de session du fournisseur",
    )
    web.add_argument(
        "--groupes-analyste",
        help="groupes du jeton qui donnent le rôle analyste (séparés par des "
        "virgules) ; exigé avec --identite en-tetes",
    )
    web.add_argument(
        "--groupes-relecteur",
        help="groupes du jeton qui donnent le rôle relecteur (séparés par des "
        "virgules) ; exigé avec --identite en-tetes",
    )
    web.add_argument(
        "--second-facteur-amr",
        help="valeurs amr acceptées comme preuve d'un second facteur, pour trancher et "
        "expirer (séparées par des virgules) ; aucune : non exigé, avec un avertissement",
    )
    web.add_argument(
        "--second-facteur-acr",
        help="valeurs acr acceptées comme preuve d'un second facteur (séparées par des "
        "virgules)",
    )
    web.add_argument(
        "--cles-csrf",
        help="dossier des clés CSRF partagées entre réplicas (Secret monté) : courante, "
        "et precedente pendant une rotation ; exigé avec --identite en-tetes",
    )
    web.add_argument(
        "--deconnexion-fournisseur",
        action="store_true",
        help="déconnexion : fermer aussi la session chez le fournisseur, s'il publie "
        "une fin de session (end_session_endpoint)",
    )
    web.set_defaults(handler=_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    settings.load_env()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], dict] = args.handler
    POOL["size"] = args.connexions
    EMBEDDER_THREADS["threads"] = args.fils_embedding
    EMBEDDER_THREADS["batch_size"] = args.lot_embedding
    try:
        # CDG_JOURNAUX n'est pas contrôlé par argparse : config() refuse un format inconnu
        logging.config.dictConfig(journaux.config(args.journaux))
        result = handler(args)
    # toute erreur est rendue en JSON structuré, code 1 : jamais de trace brute ni de repli
    except Exception as exc:  # noqa: BLE001
        error = {"erreur": type(exc).__name__, "detail": str(exc)}
        payload = getattr(exc, "payload", None)  # rapport structuré, s'il y en a un
        if isinstance(payload, dict):
            error |= payload
        print(json.dumps(error, ensure_ascii=False, default=str), file=sys.stderr)
        return 1
    # en json, le résultat tient sur une ligne, comme chaque entrée du journal
    indent = None if args.journaux == "json" else 2
    print(json.dumps(result, ensure_ascii=False, indent=indent, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

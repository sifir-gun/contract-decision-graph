"""Environnement : .env (sans écraser les variables exportées), variables obligatoires,
secrets.

Secrets (clés d'API, mots de passe, utilisateur administrateur de PostgreSQL), par ordre
de priorité :
1. le fichier `SECRETS_DIR/<NOM>`, s'il existe : c'est ainsi que le chart les monte, en
   lecture seule (CIS 5.4.1 : des fichiers plutôt que des variables d'environnement).
   Espaces et fins de ligne retirés au début et à la fin ; un fichier présent mais vide,
   illisible ou mal encodé est une erreur, jamais un repli sur la variable ;
2. sinon, la variable d'environnement du même nom (CLI, poste local, .env).
Aucun message ne reprend la valeur d'un secret.

Les chaînes de connexion PostgreSQL sont dans `adapters/postgres/conninfo.py`.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
APP_ROLE = "app_role"  # créé par migrations/001_audit.sql
# secrets montés par le chart, un fichier par secret, en lecture seule (ADR 005)
SECRETS_DIR = Path("/run/secrets/cdg")
SECRETS = (
    "MISTRAL_API_KEY",
    "ANTHROPIC_API_KEY",
    "APP_DB_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    # destination des traces (ADR 008), Langfuse : clé publique et clé secrète
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)


# traçage par un tiers (ADR 008), refusé au démarrage : LangSmith (langsmith, qui vient avec
# langchain-core ; *_TRACING_V2 l'emporte sur *_TRACING, et LANGCHAIN_TRACING_V2=true sur
# le LANGSMITH_TRACING=false de l'image), son mode OpenTelemetry, et la télémétrie du SDK
# Mistral, qui tracerait prompts et réponses (global : vers le traceur global ; dedicated :
# vers api.mistral.ai)
TRACING_VARS = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGSMITH_TRACING_MODE",
    "LANGSMITH_OTEL_ENABLED",
    "LANGSMITH_OTEL_ONLY",
    "MISTRAL_SDK_TELEMETRY",
)


def third_party_tracing(environ: Mapping[str, str]) -> list[str]:
    """Variables qui activeraient un traçage par un tiers, dans l'ordre de TRACING_VARS :
    toute valeur autre que vide ou « false » (sans tenir compte de la casse)."""
    return [
        name
        for name in TRACING_VARS
        if environ.get(name, "").strip().lower() not in ("", "false")
    ]


class SettingsError(Exception):
    """Variable d'environnement ou secret obligatoire absent, vide ou inexploitable."""


def load_env(path: Path | str = DEFAULT_ENV_PATH) -> bool:
    # override=False : une variable déjà exportée garde la priorité sur .env
    return load_dotenv(path, override=False)


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SettingsError(
            f"variable d'environnement absente ou vide : {name} (voir .env.example)"
        )
    return value


def secret(name: str) -> str | None:
    """Le secret `name` : son fichier monté s'il existe, sinon sa variable d'environnement,
    sinon None (voir l'ordre de priorité en tête du module)."""
    if name not in SECRETS:
        raise ValueError(f"secret inconnu : {name}")
    path = SECRETS_DIR / name
    if not path.exists():
        return os.environ.get(name) or None
    try:
        value = path.read_text(encoding="utf-8").strip()
    # le type seulement, et sans chaîner l'exception : son message pourrait citer la valeur
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(
            f"secret illisible : {path} ({type(exc).__name__})"
        ) from None
    if not value:
        raise SettingsError(f"secret vide : {path}")
    return value


def require_secret(name: str) -> str:
    value = secret(name)
    if value is None:
        raise SettingsError(
            f"secret absent : ni fichier {SECRETS_DIR / name}, ni variable "
            f"d'environnement {name} (voir .env.example)"
        )
    return value


def embedding_cache_dir() -> Path:
    """Cache persistant des poids d'embedding ; un chemin relatif part de la racine du dépôt."""
    path = Path(require("EMBEDDING_CACHE_DIR"))
    return path if path.is_absolute() else DEFAULT_ENV_PATH.parent / path

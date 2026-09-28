#!/usr/bin/env bash
# Vérifications de la CI, en local, avant chaque push : mêmes commandes, même périmètre que
# .github/workflows/ci.yml (lint, types, audit, tests, image), dans l'ordre des jobs.
# tests/test_ci.py échoue si les commandes de ce script divergent de celles du workflow.
#
# Écarts assumés avec la CI, propres à l'environnement :
# - une seule synchronisation, tous groupes (la CI en fait une par job) ;
# - la base est celle de docker compose (docker compose up -d), déjà migrée : setup-db
#   applique les migrations idempotentes, sans le script d'init du conteneur de service ;
# - RUNNER_TEMP, fourni par GitHub Actions, est ici un dossier temporaire, supprimé à la fin ;
# - l'image est construite pour l'architecture du poste (arm64 sur un Mac récent), celle
#   de la CI pour amd64 : les bases figées sont des index multi-architecture.
# Payant : non (tests llm exclus). Réseau : pip-audit interroge la base de failles de PyPI ;
# docker build tire les bases figées et les paquets de uv.lock (puis les garde en cache) ;
# la vérification des bases interroge les registres, Sigstore et l'API de GitHub (gh
# authentifié).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${RUNNER_TEMP:-}" ]]; then
    RUNNER_TEMP="$(mktemp -d)"
    trap 'rm -rf "$RUNNER_TEMP"' EXIT # l'image sauvegardée pèse plusieurs centaines de Mo
fi

echo "==> dépendances, strictement depuis uv.lock (tous groupes)"
uv sync --locked --all-groups

echo "==> lint (ruff)"
uv run --no-sync ruff format --check
uv run --no-sync ruff check

echo "==> types (mypy)"
uv run --no-sync mypy

echo "==> audit (pip-audit)"
uv export --locked --all-groups --no-emit-project --format requirements-txt --output-file "$RUNNER_TEMP/requirements-audit.txt"
uv run --no-sync pip-audit --require-hashes --disable-pip --strict --requirement "$RUNNER_TEMP/requirements-audit.txt"

echo "==> échéances du corpus (60 jours)"
uv run --no-sync python scripts/echeances_corpus.py --jours 60

echo "==> tests (PostgreSQL + pgvector)"
uv run --no-sync python -m cdg.cli setup-db
uv run --no-sync pytest --cov --cov-report=term

echo "==> image (signatures des bases, construction, vérifications, inventaire, scan)"
uv run --no-sync python scripts/chaine.py bases
docker build --tag cdg:verification .
uv run --no-sync pytest -m image --image cdg:verification
uv run --no-sync python scripts/chaine.py inventaire cdg:verification --dossier "$RUNNER_TEMP/chaine"
uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine"

echo "==> vérifications de la CI : toutes passées"

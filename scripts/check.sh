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
# - helm vient de Homebrew (brew install helm), à la version de la CI, que scripts/chart.py
#   contrôle ; la CI l'installe depuis l'archive officielle vérifiée par son empreinte ;
# - k3d et kubectl aussi (brew install k3d kubernetes-cli) ; le cluster prend le profil
#   local réduit (scripts/cluster.py), et reste en place si un scénario échoue, pour le
#   diagnostic (scripts/cluster.py detruire) ;
# - `--sans-cluster` saute le cluster, en l'annonçant : le profil local monte à 7 Go sur
#   les 8 de la VM Docker du poste, qui a redémarré le 29/09 pendant une installation ;
#   seul le job cluster de la CI le vérifie alors ;
# - l'image est construite pour l'architecture du poste (arm64 sur un Mac récent), celle
#   de la CI pour amd64 : les bases figées sont des index multi-architecture.
# Payant : non (tests llm exclus). Réseau : pip-audit interroge la base de failles de PyPI ;
# docker build tire les bases figées et les paquets de uv.lock (puis les garde en cache) ;
# la vérification des bases interroge les registres, Sigstore et l'API de GitHub (gh
# authentifié).
set -euo pipefail
cd "$(dirname "$0")/.."
SANS_CLUSTER=false
for option in "$@"; do
    case "$option" in
        --sans-cluster) SANS_CLUSTER=true ;;
        *)
            echo "option inconnue : $option (seule --sans-cluster existe)" >&2
            exit 2
            ;;
    esac
done
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

echo "==> chart (lint, schémas, bonnes pratiques, rendu ; helm par Homebrew)"
uv run --no-sync python scripts/chart.py verifier --dossier "$RUNNER_TEMP/chart"
uv run --no-sync pytest -m chart --chart

echo "==> image (signatures des bases, construction, vérifications, inventaire, scan)"
uv run --no-sync python scripts/chaine.py bases
docker build --tag cdg:verification .
uv run --no-sync pytest -m image --image cdg:verification
uv run --no-sync python scripts/chaine.py inventaire cdg:verification --dossier "$RUNNER_TEMP/chaine"
uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine"
uv run --no-sync python scripts/chaine.py bases --dockerfile docker/proxy-sortie/Dockerfile
docker build --file docker/proxy-sortie/Dockerfile --tag cdg-proxy:verification docker/proxy-sortie
uv run --no-sync pytest -m proxy --proxy cdg-proxy:verification --image cdg:verification
uv run --no-sync python scripts/chaine.py inventaire cdg-proxy:verification --dossier "$RUNNER_TEMP/chaine-proxy"
uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine-proxy"
uv run --no-sync python scripts/chaine.py bases --dockerfile docker/oauth2-proxy/Dockerfile
docker build --file docker/oauth2-proxy/Dockerfile --tag cdg-oauth2-proxy:verification docker/oauth2-proxy
uv run --no-sync pytest -m oauth2proxy --oauth2-proxy cdg-oauth2-proxy:verification
uv run --no-sync python scripts/chaine.py inventaire cdg-oauth2-proxy:verification --dossier "$RUNNER_TEMP/chaine-oauth2-proxy"
uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine-oauth2-proxy"

if [[ "$SANS_CLUSTER" == true ]]; then
    echo "==> cluster : NON LANCÉ (--sans-cluster) ; seul le job cluster de la CI le vérifie"
else
    echo "==> cluster (k3d, profil local réduit ; k3d et kubectl par Homebrew)"
    docker build --file docker/mistral-factice/Dockerfile --build-arg APPLICATION=cdg:verification --tag cdg-mistral-factice:verification .
    uv run --no-sync python scripts/cluster.py detruire
    uv run --no-sync python scripts/cluster.py tirer-modele
    uv run --no-sync python scripts/cluster.py creer
    uv run --no-sync python scripts/cluster.py images --dossier "$RUNNER_TEMP/cluster"
    uv run --no-sync python scripts/cluster.py installer --dossier "$RUNNER_TEMP/cluster"
    uv run --no-sync pytest -m cluster --cluster="$RUNNER_TEMP/cluster"
    uv run --no-sync python scripts/cluster.py detruire
fi

if [[ "$SANS_CLUSTER" == true ]]; then
    echo "==> vérifications de la CI : toutes passées, sauf le cluster (--sans-cluster)"
else
    echo "==> vérifications de la CI : toutes passées"
fi

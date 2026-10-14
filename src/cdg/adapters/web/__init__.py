"""Interface web : adaptateur entrant (FastAPI, Jinja2, HTMX copié dans le dépôt), au même
titre que la CLI. Lancée par `uv run python -m cdg.cli web`."""

from fastapi.telemetry import TelemetryConfig

# télémétrie propre à FastAPI (depuis 0.142) coupée dans chaque application : traces,
# mesures et journaux d'erreurs (messages et piles) de chaque requête, vers les fournisseurs
# globaux, et export installé d'après l'environnement (FASTAPI_OTEL_AUTO_CONFIGURE, refusée
# au démarrage) ; les traces du projet ne passent que par son port (ADR 008)
NO_TELEMETRY: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}

#!/usr/bin/env bash
# Applique migrations/*.sql dans l'ordre, au premier démarrage uniquement
# (docker-entrypoint-initdb.d ne s'exécute que sur un volume vide).
set -euo pipefail

if [[ -z "${APP_DB_PASSWORD:-}" ]]; then
    echo "ERREUR : APP_DB_PASSWORD absent ou vide. Aucune migration appliquée." >&2
    echo "Renseigne-le dans .env (voir .env.example), puis recrée le volume." >&2
    exit 1
fi

# le mot de passe d'app_role passe par un paramètre de la session (cdg.app_password) : la
# 001, que setup-db exécute aussi par psycopg, ne contient aucune variable psql
for migration in /migrations/*.sql; do
    echo "migration : ${migration}"
    psql -v ON_ERROR_STOP=1 \
         --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
         -v app_password="${APP_DB_PASSWORD}" <<SQL
SET cdg.app_password = :'app_password';
\i ${migration}
SQL
done

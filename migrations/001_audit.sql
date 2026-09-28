-- 001 : extension vector, journal d'audit en ajout seul, rôle applicatif.
-- Appliquée sur une base vide par docker/initdb/00_migrate.sh (Docker Compose) ou par
-- setup-db (psycopg : aucune variable psql ici). rag_chunks arrive en 002 au J3 ; droits
-- sur le checkpointer au J2.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE audit_decisions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- sans droit sur séquence
    contract_id   TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    record        JSONB NOT NULL,
    config_hash   TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    chain_hash    TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- rôle applicatif : créé seulement s'il n'existe pas. Dans le cluster, CloudNativePG le
-- gère (chart cdg-postgres) ; ailleurs, setup-db le crée avant, mot de passe haché côté
-- client. Docker Compose passe le mot de passe par cdg.app_password (00_migrate.sh).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_role') THEN
        IF coalesce(current_setting('cdg.app_password', true), '') = '' THEN
            RAISE EXCEPTION 'rôle app_role absent, et aucun mot de passe pour le créer';
        END IF;
        EXECUTE format(
            'CREATE ROLE app_role LOGIN PASSWORD %L',
            current_setting('cdg.app_password', true)
        );
    END IF;
END
$$;

-- journal en ajout seul : le rôle applicatif n'a ni UPDATE ni DELETE
GRANT SELECT, INSERT ON audit_decisions TO app_role;
REVOKE UPDATE, DELETE ON audit_decisions FROM app_role;

COMMIT;

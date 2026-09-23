-- 001 : extension vector, journal d'audit en ajout seul, rôle applicatif.
-- Appliquée par docker/initdb/00_migrate.sh : psql -v app_password=...
-- rag_chunks arrive en 002 au J3 ; droits sur le checkpointer au J2.

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

-- journal en ajout seul : le rôle applicatif n'a ni UPDATE ni DELETE
CREATE ROLE app_role LOGIN PASSWORD :'app_password';
GRANT SELECT, INSERT ON audit_decisions TO app_role;
REVOKE UPDATE, DELETE ON audit_decisions FROM app_role;

COMMIT;

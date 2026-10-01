-- 007 : archive des configurations (PR d'archivage, ADR 005). Idempotente : appliquée par
-- l'init Docker sur un volume vide, ou par `cdg.cli setup-db` sur une base existante.
-- Chaque configuration qui a produit une décision scellée, sous sa forme validée (JSON),
-- indexée par son empreinte (config_hash de l'enregistrement) : archivée au scellement,
-- dans la transaction de l'enregistrement. Le rejeu la retrouve, même quand le modèle de
-- configuration a changé depuis ; verify contrôle que chaque enregistrement v2 a la sienne.
-- JSONB ne garde ni l'ordre des clés ni la forme des nombres : l'empreinte se recalcule
-- toujours par la sérialisation canonique du domaine, jamais sur le texte relu.

CREATE TABLE IF NOT EXISTS audit_decisions_configurations (
    config_hash TEXT PRIMARY KEY CHECK (config_hash ~ '^[0-9a-f]{64}$'),
    config      JSONB NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- en ajout seul : l'application archive, sans jamais modifier ni supprimer
GRANT SELECT, INSERT ON audit_decisions_configurations TO app_role;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_decisions_configurations FROM app_role;

-- 006 : reprises des analyses interrompues (phase Kubernetes, ADR 005). Idempotente :
-- appliquée par l'init Docker sur un volume vide, ou par `cdg.cli setup-db` sur une base
-- existante.
-- Une ligne par contrat : combien de fois son analyse a été reprise après une
-- interruption (processus arrêté ou mort). Le compteur survit aux redémarrages ; au-delà
-- de interrupted.max_resumes (config/decision.yaml), plus de reprise : ESCALADE.

CREATE TABLE IF NOT EXISTS contract_resumes (
    thread_id       TEXT PRIMARY KEY,
    resumes         INTEGER NOT NULL CHECK (resumes >= 1),
    last_resumed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- l'application ajoute et incrémente, sans jamais supprimer
GRANT SELECT, INSERT, UPDATE ON contract_resumes TO app_role;
REVOKE DELETE, TRUNCATE ON contract_resumes FROM app_role;

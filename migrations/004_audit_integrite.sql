-- 004 : intégrité du journal d'audit (J4). Idempotente : appliquée par l'init Docker sur un
-- volume vide, ou par setup-db sur une base existante.
-- Un enregistrement par thread (un contrat = un thread), et une chaîne sans fourche : deux
-- maillons ne peuvent pas suivre le même prédécesseur. Le verrou consultatif de
-- l'adaptateur sérialise les ajouts ; ces index le garantissent en base.

CREATE UNIQUE INDEX IF NOT EXISTS audit_decisions_thread_id_key
    ON audit_decisions (thread_id);

CREATE UNIQUE INDEX IF NOT EXISTS audit_decisions_prev_hash_key
    ON audit_decisions (prev_hash);

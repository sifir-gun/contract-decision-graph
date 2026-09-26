-- 005 : rattachement déclaré des extraits aux types de clause (J4). Idempotente : init
-- Docker sur un volume vide, ou `cdg.cli setup-db` sur une base existante.
-- kinds : types de clause du domaine de l'extrait que sa source peut justifier (manifeste,
-- fiches). NULL : extrait indexé avant cette migration ; la recherche refuse alors de
-- répondre (erreur explicite) jusqu'au prochain `ingest`, qui remplit la colonne.

ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS kinds TEXT[];

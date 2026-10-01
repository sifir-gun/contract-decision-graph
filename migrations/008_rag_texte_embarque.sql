-- 008 : texte réellement embarqué de chaque extrait (ADR 006, PR 2). Idempotente : init
-- Docker sur un volume vide, ou `cdg.cli setup-db` sur une base existante.
-- header : en-tête écrit par le code, embarqué avant le texte (le texte stocké reste
-- celui de la source, cité tel quel).
-- embedded_hash : empreinte de ce qui détermine le vecteur (préfixe de passage du modèle,
-- en-tête, texte). Sans elle, un nouvel en-tête, texte stocké identique, ne réindexait
-- rien (défaut relevé le 01/10). NULL : extrait indexé avant cette migration, remplacé
-- au prochain `ingest`.

ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS header TEXT;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS embedded_hash TEXT;

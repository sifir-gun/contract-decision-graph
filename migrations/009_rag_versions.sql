-- 009 : versions successives d'un article (docs/spec-versions-corpus.md, ADR 009).
-- Idempotente : init Docker sur un volume vide, ou `cdg.cli setup-db` sur une base
-- existante.
-- La clé d'unicité d'un extrait comprend le début de validité de sa version : un
-- paragraphe commun à deux versions a une ligne par version. Avec l'ancienne clé, la
-- seconde était écartée en silence par `ON CONFLICT DO NOTHING`. NULLS NOT DISTINCT :
-- une source sans début de validité (EUR-Lex) reste dédoublonnée comme avant.
-- Relancer `ingest` ensuite.

CREATE UNIQUE INDEX IF NOT EXISTS rag_chunks_version_key
    ON rag_chunks (domain, content_hash, embedding_model, valid_from)
    NULLS NOT DISTINCT;

-- contrainte de la migration 002, nommée par défaut (lu dans la base le 16/10/2026)
ALTER TABLE rag_chunks
    DROP CONSTRAINT IF EXISTS rag_chunks_domain_content_hash_embedding_model_key;

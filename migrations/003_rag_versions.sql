-- 003 : métadonnées de version des extraits du corpus (J3). Idempotente : init Docker
-- sur un volume vide, ou `cdg.cli setup-db` sur une base existante.

ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS article TEXT;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS chunk_index INTEGER;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS valid_from DATE;
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS valid_until DATE;  -- fin de validité de la version
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS amendment TEXT;    -- « modification : Ordonnance … »
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS note TEXT;         -- « Conformément à … »
ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS retrieved_at DATE;

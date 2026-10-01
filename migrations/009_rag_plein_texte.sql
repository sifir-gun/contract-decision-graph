-- 009 : recherche plein texte française, accents ignorés (ADR 006, PR 2, technique 3).
-- Idempotente : init Docker sur un volume vide, ou `cdg.cli setup-db` sur une base
-- existante. unaccent : module contrib de PostgreSQL, présent dans l'image pgvector et
-- dans l'image de CloudNativePG (paquet postgresql-16 de PGDG), extension « trusted ».
-- cdg_francais : configuration française dont les mots passent par unaccent avant la
-- racinisation (french_stem) : « pénalités » et « penalite » donnent le même lexème.
-- lexemes : en-tête et texte de l'extrait, générés par PostgreSQL ; index GIN.

CREATE EXTENSION IF NOT EXISTS unaccent;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'cdg_francais') THEN
        CREATE TEXT SEARCH CONFIGURATION cdg_francais (COPY = french);
        ALTER TEXT SEARCH CONFIGURATION cdg_francais
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem;
    END IF;
END
$$;

ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS lexemes tsvector
    GENERATED ALWAYS AS (
        to_tsvector('cdg_francais'::regconfig, coalesce(header, '') || ' ' || text)
    ) STORED;

CREATE INDEX IF NOT EXISTS rag_chunks_lexemes_idx ON rag_chunks USING gin (lexemes);

-- corpus toujours en lecture seule pour l'application (SELECT, migration 002), colonne
-- générée comprise ; la configuration et le dictionnaire unaccent se lisent sans droit
-- particulier

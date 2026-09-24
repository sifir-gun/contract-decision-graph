-- 002 : corpus RAG (J3). Idempotente : appliquée par l'init Docker sur un volume vide,
-- et par `cdg.cli setup-db` sur une base existante.
-- vector(1024) = embedding.dimension de decision.yaml, contrôlé par setup-db.
-- Pas d'index HNSW : la recherche est exacte, filtrée par domaine AVANT le calcul de
-- distance (spec), ce qu'un index approché ne garantit pas.

CREATE TABLE IF NOT EXISTS rag_chunks (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domain          TEXT NOT NULL
                    CHECK (domain IN ('juridique', 'financier', 'conformite', 'operationnel')),
    source_id       TEXT NOT NULL,          -- identifiant de la source dans SOURCES.md
    reference       TEXT NOT NULL,          -- ex. « RGPD, art. 28 »
    text            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding       vector(1024) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, content_hash, embedding_model)
);

CREATE INDEX IF NOT EXISTS rag_chunks_domain_idx ON rag_chunks (domain);

-- corpus en lecture seule pour l'application ; l'ingestion passe par l'administrateur
GRANT SELECT ON rag_chunks TO app_role;

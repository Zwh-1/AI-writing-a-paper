-- Additive indexes over canonical files and source chunks; originals and citation IDs remain stable.
CREATE TABLE IF NOT EXISTS knowledge_documents(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    file_id TEXT NOT NULL REFERENCES files(id),
    blob_sha256 TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    indexed_at TEXT,
    UNIQUE(project_id,file_id)
);
CREATE TABLE IF NOT EXISTS knowledge_chunks(
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    PRIMARY KEY(document_id,source_id)
);
CREATE INDEX IF NOT EXISTS knowledge_documents_project ON knowledge_documents(project_id,state);
CREATE INDEX IF NOT EXISTS knowledge_chunks_source ON knowledge_chunks(source_id);

CREATE TABLE studio_records(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL,
 owner_id TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1,
 data TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 deleted_at TEXT
);
CREATE INDEX idx_studio_records_scope ON studio_records(project_id,kind,owner_id,updated_at);
CREATE TABLE studio_versions(
 id TEXT PRIMARY KEY,
 record_id TEXT NOT NULL REFERENCES studio_records(id),
 revision INTEGER NOT NULL,
 data TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(record_id,revision)
);
CREATE TABLE studio_external_links(
 project_id TEXT NOT NULL REFERENCES projects(id),
 provider TEXT NOT NULL,
 library_id TEXT NOT NULL,
 external_id TEXT NOT NULL,
 reference_id TEXT NOT NULL REFERENCES bibliography(id),
 remote_version TEXT NOT NULL DEFAULT '',
 data TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL,
 PRIMARY KEY(project_id,provider,library_id,external_id)
);
CREATE TABLE studio_oauth(
 token TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 callback TEXT NOT NULL,
 expires_at REAL NOT NULL,
 consumed INTEGER NOT NULL DEFAULT 0
);

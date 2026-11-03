ALTER TABLE studio_records ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;

CREATE TABLE research_record_owners(
 record_id TEXT PRIMARY KEY REFERENCES studio_records(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 file_id TEXT REFERENCES files(id),
 reference_id TEXT REFERENCES bibliography(id),
 section_id TEXT REFERENCES sections(id),
 conversation_id TEXT REFERENCES studio_records(id),
 CHECK ((file_id IS NOT NULL)+(reference_id IS NOT NULL)+(section_id IS NOT NULL)+(conversation_id IS NOT NULL)=1)
);
CREATE TABLE research_links(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 record_id TEXT NOT NULL REFERENCES studio_records(id),
 record_revision INTEGER NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('derived_from','discusses','attached_to')),
 target_type TEXT NOT NULL,
 target_id TEXT NOT NULL,
 target_version TEXT NOT NULL,
 source_json TEXT NOT NULL,
 UNIQUE(record_id,record_revision,relation,target_type,target_id)
);
CREATE INDEX idx_research_links_target ON research_links(project_id,target_type,target_id);
CREATE TABLE research_record_states(
 record_id TEXT PRIMARY KEY REFERENCES studio_records(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 state TEXT NOT NULL DEFAULT 'unknown' CHECK(state IN ('current','unknown','stale','deleted')),
 note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE asset_versions(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 asset_id TEXT NOT NULL REFERENCES artifacts(id),
 revision INTEGER NOT NULL,
 name TEXT NOT NULL,
 kind TEXT NOT NULL,
 sha256 TEXT,
 metadata TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(asset_id,revision)
);
CREATE TABLE workspace_intents(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 section_id TEXT NOT NULL REFERENCES sections(id),
 base_revision_id TEXT REFERENCES revisions(id),
 artifact_id TEXT NOT NULL REFERENCES artifacts(id),
 asset_version_id TEXT NOT NULL REFERENCES asset_versions(id),
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','consumed','dismissed')),
 consumed_revision_id TEXT REFERENCES revisions(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX idx_workspace_intents_target ON workspace_intents(project_id,section_id,state);
CREATE TABLE research_sync_conflicts(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 record_id TEXT NOT NULL,
 local_json TEXT NOT NULL,
 remote_json TEXT NOT NULL,
 local_hash TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','resolved')),
 resolution TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE research_sync_receipts(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 packet_hash TEXT NOT NULL,
 result_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(project_id,packet_hash)
);
CREATE TABLE research_sync_history(
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 record_id TEXT NOT NULL REFERENCES studio_records(id),
 branch_hash TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(record_id,branch_hash)
);

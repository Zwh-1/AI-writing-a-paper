CREATE TABLE file_usage_additions(
 file_id TEXT NOT NULL REFERENCES files(id),usage TEXT NOT NULL,
 note TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(file_id,usage)
);
CREATE TABLE file_source_profiles(file_id TEXT PRIMARY KEY REFERENCES files(id),kind TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE document_readings(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),file_id TEXT NOT NULL REFERENCES files(id),
 parse_id TEXT NOT NULL REFERENCES knowledge_parses(id),section_id TEXT REFERENCES sections(id),
 question TEXT NOT NULL,intent TEXT NOT NULL,required_topics TEXT NOT NULL,task_scope_hash TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'reading',reviewer TEXT NOT NULL DEFAULT '',review_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE INDEX document_readings_section ON document_readings(project_id,section_id);
CREATE TABLE document_reading_passages(
 id TEXT PRIMARY KEY,reading_id TEXT NOT NULL REFERENCES document_readings(id),unit_id TEXT NOT NULL REFERENCES knowledge_units(id),
 unit_hash TEXT NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,text TEXT NOT NULL,
 locator TEXT NOT NULL,source_use TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(reading_id,unit_id,char_start,char_end)
);
CREATE TABLE document_reading_findings(
 id TEXT PRIMARY KEY,reading_id TEXT NOT NULL REFERENCES document_readings(id),topic TEXT NOT NULL,
 status TEXT NOT NULL,quote TEXT NOT NULL,passage_ids TEXT NOT NULL,note TEXT NOT NULL,
 method TEXT NOT NULL,reviewer TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE citation_reading_links(citation_id TEXT NOT NULL REFERENCES citations(id),reading_id TEXT NOT NULL REFERENCES document_readings(id),reading_version TEXT NOT NULL,read_scope TEXT NOT NULL,PRIMARY KEY(citation_id,reading_id));

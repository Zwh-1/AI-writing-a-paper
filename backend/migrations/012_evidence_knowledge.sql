CREATE TABLE knowledge_parses(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),file_id TEXT NOT NULL REFERENCES files(id),
 file_hash TEXT NOT NULL,input_hash TEXT NOT NULL,engine TEXT NOT NULL,strategy TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'ready',created_at TEXT NOT NULL,UNIQUE(file_id,input_hash,engine,strategy)
);
CREATE TABLE knowledge_heads(file_id TEXT PRIMARY KEY REFERENCES files(id),parse_id TEXT NOT NULL REFERENCES knowledge_parses(id));
CREATE TABLE knowledge_units(
 id TEXT PRIMARY KEY,parse_id TEXT NOT NULL REFERENCES knowledge_parses(id),ordinal INTEGER NOT NULL,
 kind TEXT NOT NULL,text TEXT NOT NULL,content_hash TEXT NOT NULL,locator TEXT NOT NULL,
 heading_path TEXT NOT NULL,origin TEXT NOT NULL,issues TEXT NOT NULL,
 review_state TEXT NOT NULL DEFAULT 'pending',parent_id TEXT REFERENCES knowledge_units(id),
 UNIQUE(parse_id,ordinal)
);
CREATE INDEX knowledge_units_parse ON knowledge_units(parse_id,ordinal);
CREATE VIRTUAL TABLE knowledge_unit_fts USING fts5(unit_id UNINDEXED,tokens,tokenize='unicode61');
CREATE TABLE knowledge_unit_chunks(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES knowledge_units(id),char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,text TEXT NOT NULL,text_hash TEXT NOT NULL);
CREATE INDEX knowledge_unit_chunks_parent ON knowledge_unit_chunks(unit_id,char_start);
CREATE TABLE knowledge_unit_vectors(unit_id TEXT NOT NULL REFERENCES knowledge_units(id),chunk_id TEXT NOT NULL REFERENCES knowledge_unit_chunks(id),model TEXT NOT NULL,text_hash TEXT NOT NULL,dimension INTEGER NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(chunk_id,model));
CREATE TABLE knowledge_reviews(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES knowledge_units(id),action TEXT NOT NULL,note TEXT NOT NULL,reviewer TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE knowledge_disputes(id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES knowledge_units(id),kind TEXT NOT NULL,note TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open',resolution TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,resolved_at TEXT);
CREATE TABLE knowledge_claim_checks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),unit_id TEXT NOT NULL REFERENCES knowledge_units(id),unit_hash TEXT NOT NULL,claim TEXT NOT NULL,quote TEXT NOT NULL,conditions TEXT NOT NULL,support TEXT NOT NULL,method TEXT NOT NULL,reviewer TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE knowledge_selections(section_id TEXT NOT NULL REFERENCES sections(id),unit_id TEXT NOT NULL REFERENCES knowledge_units(id),PRIMARY KEY(section_id,unit_id));
CREATE TABLE citation_knowledge_links(citation_id TEXT NOT NULL REFERENCES citations(id),unit_id TEXT NOT NULL REFERENCES knowledge_units(id),unit_hash TEXT NOT NULL,scope TEXT NOT NULL,PRIMARY KEY(citation_id,unit_id));
CREATE TABLE knowledge_import_previews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),manifest TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'preview',created_at TEXT NOT NULL);
CREATE TABLE knowledge_evaluations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),cases TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);

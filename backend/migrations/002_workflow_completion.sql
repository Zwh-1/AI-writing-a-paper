-- Additive schema migration; original user files, revisions and rule packages remain unchanged.
CREATE TABLE IF NOT EXISTS rule_conditions(rule_id TEXT PRIMARY KEY REFERENCES rules(id),expression TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_rule_reviews(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),rule_id TEXT NOT NULL,package_id TEXT NOT NULL,input_fingerprint TEXT NOT NULL,result TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS section_reviews(id TEXT PRIMARY KEY,section_id TEXT NOT NULL REFERENCES sections(id),revision_id TEXT NOT NULL REFERENCES revisions(id),note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_formats(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,config TEXT NOT NULL,template_sha256 TEXT,verified INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS section_archives(section_id TEXT PRIMARY KEY REFERENCES sections(id),metadata TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retrieval_evaluations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),result TEXT NOT NULL,created_at TEXT NOT NULL);
PRAGMA user_version=2;

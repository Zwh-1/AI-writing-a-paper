CREATE TABLE IF NOT EXISTS rule_jobs(
 id TEXT PRIMARY KEY REFERENCES tasks(id),project_id TEXT NOT NULL REFERENCES projects(id),
 budget_limit REAL NOT NULL,gpt_budget REAL NOT NULL DEFAULT 0,allow_gpt INTEGER NOT NULL DEFAULT 0,
 policy TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_work_items(
 project_id TEXT NOT NULL REFERENCES projects(id),item_key TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL,part_start INTEGER NOT NULL,part_end INTEGER NOT NULL,
 version TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',result TEXT NOT NULL DEFAULT '{}',error TEXT,
 task_id TEXT REFERENCES tasks(id),attempts INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL,
 PRIMARY KEY(project_id,item_key)
);
CREATE INDEX IF NOT EXISTS idx_rule_items_source ON rule_work_items(project_id,source_id);
CREATE TABLE IF NOT EXISTS rule_job_items(
 task_id TEXT NOT NULL REFERENCES tasks(id),project_id TEXT NOT NULL,item_key TEXT NOT NULL,
 PRIMARY KEY(task_id,item_key),FOREIGN KEY(project_id,item_key) REFERENCES rule_work_items(project_id,item_key) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS rule_decisions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id),rule_id TEXT NOT NULL REFERENCES rules(id),
 version TEXT NOT NULL,source_hash TEXT NOT NULL,outcome TEXT NOT NULL,reasons TEXT NOT NULL,
 before_row TEXT NOT NULL,after_row TEXT NOT NULL,restored INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rule_decisions_rule ON rule_decisions(rule_id,id);
CREATE TABLE IF NOT EXISTS rule_page_checks(
 page_id TEXT PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,content_hash TEXT NOT NULL,
 method TEXT NOT NULL,outcome TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_verification_attempts(
 project_id TEXT NOT NULL REFERENCES projects(id),fingerprint TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(id),
 state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',error TEXT,created_at TEXT NOT NULL,
 PRIMARY KEY(project_id,fingerprint)
);

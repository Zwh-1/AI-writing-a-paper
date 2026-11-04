CREATE TABLE request_intents(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 generation_run_id TEXT NOT NULL UNIQUE REFERENCES generation_runs(id), task_attempt INTEGER NOT NULL,
 stage TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX request_intents_task ON request_intents(task_id,created_at);
CREATE TABLE request_intent_runs(
 intent_id TEXT NOT NULL REFERENCES request_intents(id), generation_run_id TEXT NOT NULL UNIQUE REFERENCES generation_runs(id),
 PRIMARY KEY(intent_id,generation_run_id)
);
CREATE TABLE provider_attempts(
 id TEXT PRIMARY KEY, intent_id TEXT NOT NULL REFERENCES request_intents(id), ordinal INTEGER NOT NULL,
 endpoint_id TEXT, identity TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
 reserved REAL NOT NULL DEFAULT 0, usage TEXT NOT NULL DEFAULT '{}', cost REAL,
 remote_id TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(intent_id,ordinal)
);
CREATE INDEX provider_attempts_intent ON provider_attempts(intent_id,ordinal);
CREATE TABLE request_execution_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL REFERENCES request_intents(id),
 attempt_id TEXT REFERENCES provider_attempts(id), event_type TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX request_execution_events_intent ON request_execution_events(intent_id,id);

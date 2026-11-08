CREATE TABLE ai_node_receipts(
    run_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    execution_epoch INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    result_blob TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    PRIMARY KEY(run_id,action_id)
);
CREATE INDEX ai_node_receipts_task ON ai_node_receipts(task_id,run_id);
CREATE TABLE ai_runtime_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL REFERENCES tasks(id),
    execution_epoch INTEGER NOT NULL,
    stage TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX ai_runtime_events_project ON ai_runtime_events(project_id,task_id,stage,id);
CREATE TABLE ai_task_protocols(
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    protocol TEXT NOT NULL
);

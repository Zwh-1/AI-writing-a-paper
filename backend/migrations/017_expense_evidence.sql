CREATE TABLE expense_evidence (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT REFERENCES generation_runs(id),
 kind TEXT NOT NULL,
 document_sha256 TEXT,
 content TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX expense_evidence_project ON expense_evidence(project_id,created_at);
CREATE INDEX expense_evidence_run ON expense_evidence(run_id,kind);
CREATE TRIGGER expense_evidence_no_update BEFORE UPDATE ON expense_evidence BEGIN SELECT RAISE(ABORT,'Expense evidence is append only'); END;
CREATE TRIGGER expense_evidence_no_delete BEFORE DELETE ON expense_evidence BEGIN SELECT RAISE(ABORT,'Expense evidence is append only'); END;

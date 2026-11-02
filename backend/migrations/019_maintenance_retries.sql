ALTER TABLE knowledge_maintenance_items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
CREATE INDEX knowledge_maintenance_retry ON knowledge_maintenance_items(project_id,status,attempts);

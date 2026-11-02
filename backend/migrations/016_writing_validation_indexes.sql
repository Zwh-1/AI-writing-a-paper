-- Accelerate repeated provenance checks without changing any historical records.
CREATE INDEX IF NOT EXISTS idx_knowledge_dependencies_downstream ON knowledge_dependencies(project_id,downstream_type,downstream_id);
CREATE INDEX IF NOT EXISTS idx_wiki_links_block_project ON wiki_links(project_id,block_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_checks_unit_created ON knowledge_claim_checks(unit_id,created_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_disputes_unit_created ON knowledge_disputes(unit_id,created_at);
CREATE INDEX IF NOT EXISTS idx_kg_assessments_claim_created ON kg_support_assessments(claim_version_id,created_at,id);

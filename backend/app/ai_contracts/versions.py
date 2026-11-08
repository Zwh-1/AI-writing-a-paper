"""Common immutable input versions for contracts and experiment pairing."""
from copy import deepcopy

FIELDS = ('core_version_id', 'contract_version_id', 'rule_package_id', 'target_snapshot_id',
          'base_revision_id', 'related_revisions', 'evidence_versions', 'reference_versions',
          'reference_metadata_hashes', 'evidence_payload_hashes', 'knowledge_documents',
          'knowledge_unit_versions', 'knowledge_read_scopes', 'document_reading_versions',
          'source_read_scopes', 'resource_refs', 'permission_stamps', 'permissions_hash',
          'section_materials', 'terminology_hash', 'project_brief_hash', 'manuscript_hash',
          'project_grounding_hash', 'required_rule_ids', 'loaded_rule_ids', 'rule_text_hash')


def source_versions(manifest):
    result = {key: deepcopy(manifest[key]) for key in FIELDS if manifest.get(key) is not None}
    facts = dict(manifest.get('fact_versions') or {})
    for key, value in (manifest.get('fact_revisions') or {}).items():
        if key in facts and facts[key] != value:
            # Never hide inconsistent aliases while building an identity.
            result.setdefault('fact_version_conflicts', {})[key] = [facts[key], value]
        facts[key] = value
    if facts:
        result['fact_versions'] = facts
    if manifest.get('writing_state'):
        result['writing_state'] = deepcopy(manifest['writing_state'])
    return result

"""Atomic candidate patches bound to exact paragraph and citation inventories."""
from copy import deepcopy
from .writing_quality_protocols import digest, REVISION


LINKS = ('source_ids', 'evidence_ids', 'knowledge_unit_ids', 'fact_ids', 'document_reading_ids', 'reference_ids')
PARAGRAPH_FIELDS = {'text', 'source_ids', 'evidence_ids', 'knowledge_unit_ids', 'fact_ids',
    'document_reading_ids', 'reference_ids', 'claim_spans', 'citation_intents', 'claim_version_ids',
    'visual_ids', 'preserve_node_id', 'brief_points'}


def contract(candidate, issues, allowed_reference_ids=()):
    paragraphs = candidate.get('paragraphs', [])
    allowed = set()
    for issue in issues:
        index = issue.get('paragraph_index')
        if type(index) is int and 0 <= index < len(paragraphs):allowed.add(index)
    inventory = []
    for index, paragraph in enumerate(paragraphs):
        claim_ids = [span.get('local_id') for span in paragraph.get('claim_spans', []) if span.get('local_id')]
        inventory.append({'paragraph_id': 'paragraph-' + str(index+1), 'paragraph_index': index,
            'old_hash': digest(paragraph), 'editable': index in allowed,
            'allowed_claim_ids': claim_ids,
            'allowed_citation_ids': [digest(intent) for intent in paragraph.get('citation_intents', [])],
            'allowed_reference_ids': sorted(set(allowed_reference_ids) | set(paragraph.get('reference_ids', [])) |
                {intent['reference_id'] for intent in paragraph.get('citation_intents', []) if intent.get('reference_id')})})
    return {'schema': REVISION, 'candidate_hash': digest(candidate), 'paragraphs': inventory,
        'operations': ['replace_paragraph'], 'allow_insert_delete_reorder': False,
        'allow_top_level_changes': False,
        'response_contract': 'Return {"schema":"restricted_patch_v1","candidate_hash":"given hash","patches":'
             '[{"paragraph_id":"given id","old_hash":"given hash","paragraph":{...complete replacement...}}]}. '
             'Only editable paragraphs may change; preserve other claims and citations. No full candidate rewrite.'}


def apply(candidate, value, approved):
    """Validate every operation first; conflicts never mutate the old candidate."""
    if (not isinstance(value, dict) or set(value) != {'schema', 'candidate_hash', 'patches'}
            or value.get('schema') != REVISION or value.get('candidate_hash') != approved.get('candidate_hash')
            or digest(candidate) != approved.get('candidate_hash')):
        raise ValueError('REVISION_PATCH_CONFLICT: candidate or patch protocol does not match')
    patches = value.get('patches')
    if not isinstance(patches, list) or len(patches) > len(approved['paragraphs']):
        raise ValueError('REVISION_PATCH_CONFLICT: invalid patch list')
    known = {row['paragraph_id']: row for row in approved['paragraphs']}
    replacements = {}; seen = set()
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) != {'paragraph_id', 'old_hash', 'paragraph'}:
            raise ValueError('REVISION_PATCH_CONFLICT: unexpected patch fields')
        identity = patch.get('paragraph_id'); record = known.get(identity)
        if not record or not record['editable'] or identity in seen or patch.get('old_hash') != record['old_hash']:
            raise ValueError('REVISION_PATCH_CONFLICT: paragraph is stale, repeated or outside allowed scope')
        replacement = patch.get('paragraph')
        if not isinstance(replacement, dict) or set(replacement)-PARAGRAPH_FIELDS or not isinstance(replacement.get('text'), str) or not replacement['text'].strip():
            raise ValueError('REVISION_PATCH_CONFLICT: invalid replacement paragraph')
        prior = candidate['paragraphs'][record['paragraph_index']]
        if digest(prior) != record['old_hash']:
            raise ValueError('REVISION_PATCH_CONFLICT: paragraph changed')
        if replacement.get('preserve_node_id') != prior.get('preserve_node_id'):
            raise ValueError('REVISION_PATCH_CONFLICT: paragraph identity cannot be rebound')
        allowed_claims = set(record['allowed_claim_ids'])
        if any(not isinstance(span, dict) or span.get('local_id') not in allowed_claims
               for span in replacement.get('claim_spans', [])):
            raise ValueError('REVISION_PATCH_CONFLICT: patch invented a claim identity')
        if any(not isinstance(intent, dict) or intent.get('claim_id') not in allowed_claims
               for intent in replacement.get('citation_intents', [])):
            raise ValueError('REVISION_PATCH_CONFLICT: patch citation points outside allowed claims')
        if (set(replacement.get('reference_ids', []))-set(record['allowed_reference_ids'])
                or any(intent.get('reference_id') and intent['reference_id'] not in record['allowed_reference_ids']
                       for intent in replacement.get('citation_intents', []))):
            raise ValueError('REVISION_PATCH_CONFLICT: citation outside the authorized reference inventory')
        for key in LINKS:
            if key in replacement and not isinstance(replacement[key], list):
                raise ValueError('REVISION_PATCH_CONFLICT: source links must be arrays')
        seen.add(identity); replacements[record['paragraph_index']] = deepcopy(replacement)
    result = deepcopy(candidate)
    for index, paragraph in replacements.items():result['paragraphs'][index] = paragraph
    return result, {'schema': REVISION, 'base_hash': approved['candidate_hash'],
                    'candidate_hash': digest(result), 'changed_paragraph_indices': sorted(replacements),
                    'unchanged_paragraphs_byte_preserved': True}

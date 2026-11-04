"""Frozen quality protocols and shadow-only dependency projections."""
from copy import deepcopy
import hashlib
import json


VERIFICATION = 'claim_window_v2'
REVISION = 'restricted_patch_v1'
RANGES = 'evidence.range.v1'


def payload(task):
    value = task.get('payload', {}) if isinstance(task, dict) else {}
    if isinstance(value, str):
        try:value = json.loads(value)
        except (TypeError, ValueError):return {}
    return value if isinstance(value, dict) else {}


def verification_enabled(task):
    return payload(task).get('verification_scope_version') == VERIFICATION


def patch_enabled(task):
    return payload(task).get('revision_protocol_version') == REVISION


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def project_verification(request, paragraphs, context, local):
    """Keep exact selected source ranges and explicit premises, never infer support."""
    result = deepcopy(request)
    target = {claim['paragraph_index'] for claim in request['claims']}
    selected = {index for position in target for index in (position-1, position, position+1)
                if 0 <= index < len(paragraphs)}
    result['candidate_paragraphs'] = [dict(text=paragraphs[index].get('text', ''), paragraph_index=index)
                                      for index in sorted(selected)]
    dependencies = context.get('section', {}).get('dependencies', [])
    if isinstance(dependencies, str):
        try:dependencies = json.loads(dependencies)
        except ValueError:dependencies = []
    needed = set(dependencies if isinstance(dependencies, list) else [])
    needed.update(row.get('section_id') or row.get('id') for row in context.get('related_sections', [])
                  if row.get('relation') in ('dependency', 'previous') or row.get('scope') == 'full_required_dependency')
    omitted_sections = []
    for lane in ('related_sections', 'paper_draft_sections'):
        rows = request.get(lane, [])
        result[lane] = [row for row in rows if (row.get('section_id') or row.get('id')) in needed]
        omitted_sections.extend({'section_id': row.get('section_id') or row.get('id'),
                                 'text_hash': digest(row.get('text', ''))}
                                for row in rows if row not in result[lane])
    claim_sources = {}
    all_sources = {row['id'] for row in result['actual_sources']}
    fields = ('source_ids', 'evidence_ids', 'knowledge_unit_ids', 'fact_ids', 'document_reading_ids')
    for claim in request['claims']:
        linked = set()
        for name in fields:linked.update(claim.get(name, []) or [])
        position = claim['paragraph_index']
        mappings = local.get('paragraphs', [])
        spans = mappings[position].get('claim_spans', []) if position < len(mappings) else []
        relevant = [span for span in spans if span.get('start_cp', 0) < claim.get('end_cp', 0)
                    and span.get('end_cp', 0) > claim.get('start_cp', 0)]
        for span in relevant:
            for name in fields:linked.update(span.get(name, []) or [])
            linked.update(row.get('id') for row in span.get('source_records', []) if row.get('id'))
        if not spans:
            for name in fields:linked.update(paragraphs[position].get(name, []) or [])
        for source in context.get('sources', []):
            if source.get('id') in linked:linked.update(source.get('text_in_knowledge_units', []))
        for reading in context.get('document_readings', []):
            if (reading.get('reading_id') or reading.get('id')) in linked:
                linked.update(row.get('unit_id') or row.get('id') for row in reading.get('passages', []))
        # Unlinked claims get the actual full source inventory for independent
        # review; the receipt records that this is not a pre-existing citation.
        claim_sources[claim['claim_id']] = {'allowed_source_ids': sorted(all_sources & linked) if linked else sorted(all_sources),
                                           'citation_linked': bool(linked)}
    receipt = {'schema': VERIFICATION, 'target_paragraph_indices': sorted(target),
        'provided_paragraph_indices': sorted(selected), 'omitted_paragraph_count': len(paragraphs)-len(selected),
        'paragraph_hashes': {str(index): digest(paragraphs[index]) for index in sorted(selected)},
        'source_ranges': [{'id': row['id'], 'text_hash': digest(row['text']),
                           'char_start': row.get('char_start'), 'char_end': row.get('char_end')}
                          for row in result['actual_sources']],
        'claim_sources': claim_sources, 'omitted_sections': omitted_sections,
        'global_coherence': {'status': 'not_checked', 'coverage_complete': False,
                             'reason': 'Only claim windows and explicit premises were supplied.'}}
    receipt['hash'] = digest(receipt)
    result['verification_scope'] = receipt
    result['output_contract'] += ' paragraph_index与paragraph_indices使用输入明确列出的原章节编号；未提供的段落和章节未核查，不得宣称全文衔接通过。'
    return result


def dependency_shadow(claims, changed_claim_ids=(), changed_source_ids=()):
    """Observe an affected closure; this never reduces any production guard."""
    affected = set(changed_claim_ids)
    changed_sources = set(changed_source_ids)
    for claim in claims:
        if changed_sources & set(claim.get('source_ids', [])):affected.add(claim['claim_id'])
    while True:
        before = len(affected)
        for claim in claims:
            if affected & set(claim.get('requires_claim_ids', [])):affected.add(claim['claim_id'])
        if len(affected) == before:break
    return {'mode': 'shadow', 'affected_claim_ids': sorted(affected),
            'paragraph_indices': sorted({row['paragraph_index'] for row in claims if row['claim_id'] in affected}),
            'enforced_guard': 'existing_full_version_and_permission_checks',
            'may_skip_verification': False}

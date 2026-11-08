"""Whole-paper coverage projection; the existing semantic gate stays required."""
from ..ai_request_cache import digest


def audit(sections):
    rows=[]
    for item in sections:
        quality=item.get('writing_quality') or {}
        obligations=quality.get('obligations') or {}
        rows.append({'section_id':item.get('section_id'),
            'candidate_hash':quality.get('candidate_hash') or digest(item.get('document',item.get('text',''))),
            'required':obligations.get('required_count'), 'verified':obligations.get('verified_count'),
            'coverage_complete':obligations.get('complete',False),
            'semantic_complete':(quality.get('semantic') or {}).get('coverage_complete',False),
            'rule_coverage_complete':(quality.get('rule_application_summary') or {}).get('coverage_complete'),
            'human_review':'pending'})
    assessed=[row for row in rows if row['required'] is not None]
    return {'schema':'paper.obligations.v1','sections':rows,
            'assessed_sections':len(assessed),'unassessed_sections':len(rows)-len(assessed),
            'required_count':sum(row['required'] for row in assessed),
            'verified_count':sum(row['verified'] or 0 for row in assessed),
            'complete':bool(rows) and all(row['coverage_complete'] for row in rows),
            'scope':'chapter_obligations_only_whole_paper_semantic_review_remains_required'}

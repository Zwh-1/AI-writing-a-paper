"""Relevance proposes readings; typed provenance limits what a claim may assert."""
import re


def kinds(row):
    from ..writing_evidence_quality import assess_result_evidence
    category = row.get('type', '')
    kind = row.get('kind', '')
    origin = row.get('origin_type', '')
    result = set()
    if category in ('source', 'knowledge_unit', 'document_reading') or kind == 'literature':
        result.update(('source', 'literature'))
    if category == 'reference' or row.get('reference_ids'):
        result.add('reference')
    if category == 'fact':
        result.update(('fact', 'project'))
    if category == 'evidence':
        result.update(('evidence', 'project'))
    if kind in ('code_observation', 'test_definition') or origin == 'code':
        result.add('code')
    if kind in ('code_observation', 'test_definition', 'deployment', 'author_statement'):
        result.add(kind)
    if kind == 'test_definition':
        result.add('test_definition')
    record = {'kind': 'data_result', 'origin_type': 'fact',
              'payload': row.get('payload') or row} if category == 'fact' else row
    level = assess_result_evidence(record)
    if kind in ('test_run', 'data_result', 'run_result') or category == 'fact':
        if level['can_assert_measurement']:
            result.update(('run_result', 'result'))
        elif kind == 'test_run' and level['level'] == 'planned':
            result.add('test_definition')
    # A conclusion is an author proposition, never an automatic measurement.
    if kind == 'author_statement':
        result.add('conclusion')
    return result


def tokens(value):
    value = str(value).lower()
    result=set(re.findall(r'[a-z][a-z0-9_]+',value))
    for word in re.findall(r'[\u3400-\u9fff]+',value):
        result.update(word[i:i+2] for i in range(max(0,len(word)-1)))
    return result


def select(question, candidates, needs=(), limit=4):
    """Greedy coverage and diversity, with no fabricated semantic support label."""
    terms = tokens(question)
    ranked = []
    for row in candidates:
        overlap = len(terms & tokens(row.get('text', '')))/max(1, len(terms))
        if not overlap:
            continue
        eligible = bool(set(needs) & kinds(row)) if needs else True
        ranked.append((eligible, overlap, row))
    ranked.sort(key=lambda item: (-int(item[0]), -item[1], str(item[2].get('id'))))
    selected = []
    origins = set()
    while ranked and len(selected)<limit:
        # Cover eligible evidence types before adding near-duplicate sources.
        best = max(range(len(ranked)), key=lambda i: (
            ranked[i][0], (ranked[i][2].get('file_id') or ranked[i][2].get('id')) not in origins,
            ranked[i][1], -i))
        eligible, score, row = ranked.pop(best)
        selected.append({**row, 'selection_score': round(score, 6),
                         'type_eligible': eligible, 'support': 'not_checked'})
        origins.add(row.get('file_id') or row.get('id'))
    return selected


def support_view(paragraphs, quality, claim_checks=()):
    reviews = {row.get('claim_id'): row for row in (quality.get('semantic') or {}).get('claims', [])}
    items = []
    for claim in claim_checks:
        identity=claim['claim_id'];review=reviews.get(identity,{})
        support=review.get('support')
        if support in ('supported', 'contradicted', 'partial') and not review.get('quote_verified'):
            support='unknown'
        items.append({'paragraph_index':claim['paragraph_index'],'claim_id':identity,
                      'claim_text':claim.get('text',''),'claim_text_hash':claim.get('text_hash'),
                      'start_cp':claim.get('start_cp'),'end_cp':claim.get('end_cp'),
                      'source_ids':claim.get('source_ids',[]),'evidence_ids':claim.get('evidence_ids',[]),
                      'source_quotes':review.get('source_quotes',[]),
                      'model_judgment':review.get('support'),
                      'quote_verified':bool(review.get('quote_verified')),
                      'relation':{'supported':'supports','contradicted':'contradicts','partial':'qualifies'}.get(support,'unresolved'),
                      'verification':'model_review' if review else 'not_assessed','human_approval':False})
    return {'schema': 'claim.support.view.v1', 'items': items}

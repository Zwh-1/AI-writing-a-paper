"""Describe unresolved reading separately from unresolved rule applicability."""
from . import db

SOURCE_STATES = {'needs_full_source', 'needs_original_comparison', 'source_cropped'}


def describe(rule, source, receipt, valid, sources):
    state = receipt.get('state') if valid else 'stale' if receipt else 'pending'
    content = receipt.get('content_review_state') if valid else None
    source_issue = state in SOURCE_STATES or content in SOURCE_STATES or receipt.get('state') in SOURCE_STATES
    # An old classification can guide navigation, but cannot finish a reading
    # check or establish a current requirement.
    hint = receipt.get('classification_purpose') if valid else None
    if hint not in ('conditional', 'knowledge', 'example', 'counterexample', 'heading', 'noise'):
        hint = None
    reason = receipt.get('reason') if valid else '条款或原文已变化，需要重新核验' if receipt else '尚无当前条款的判定'
    if source_issue:
        reason = ('原图下沿已裁断，原件未包含完整句子；须补充未裁断原件后继续核对，不能推测缺字。'
                  if content == 'source_cropped' else
                  '本条OCR文字尚未逐项对应到完整原图。需核对错字、公式与段落边界；已有局部摘录不能替代剩余内容。')
        if valid and content != 'source_cropped' and receipt.get('decision_method') == 'assistant_visual_original_comparison' and receipt.get('reason'):
            reason = receipt['reason']
    value = {'kind': 'rule', 'id': rule['id'], 'label': rule['statement'], 'reason': reason,
             'state': state, 'issue_kind': 'source_text' if source_issue else 'rule_decision',
             'content_status': content or ('needs_original_comparison' if source_issue else None),
             'purpose_hint': hint, 'purpose_hint_verified': False,
             'source_file': (source or {}).get('source_file'), 'source_page': (source or {}).get('page_number'),
             'source_id': rule.get('source_id')}
    excerpt = sources.get(receipt.get('verified_excerpt_source_id')) if valid else None
    if excerpt and source and excerpt['file_id'] == source['file_id'] and excerpt['page_id'] == source['page_id']:
        meta = db.loads(excerpt['locator'], {})
        value['supporting_excerpt'] = {'text': excerpt['text'], 'source_id': excerpt['id'],
                                     'coverage': meta.get('transcription_scope'),
                                     'supports_entire_candidate': False}
    return value

import pytest
from test_workbench import client
from test_visual_rule_repair import seed
from app import db, ai_autopilot as auto, visual_rule_repair as repair, document_coverage, document_structure, knowledge_units
from app.rule_exceptions import describe


def test_unread_text_is_separate_from_rule_applicability_and_does_not_complete_review(client):
    pid, rule, source, review = seed(client)
    rec = {'fingerprint': auto.rule_fingerprint(rule, source), 'state': 'needs_full_source', 'completed': False,
           'content_review_state': 'needs_original_comparison', 'classification_purpose': 'counterexample', 'ai_covered': True}
    auto.save_receipt(pid, 'rule', rule['id'], rec)
    before = db.one('SELECT * FROM rules WHERE id=?', (rule['id'],))
    report = auto.report(pid)
    item = next(e for e in report['exceptions'] if e['id'] == rule['id'])
    assert item['kind'] == 'rule' and item['issue_kind'] == 'source_text'
    assert item['purpose_hint'] == 'counterexample' and item['purpose_hint_verified'] is False
    assert item['source_file'] == source['source_file'] and item['source_page'] == 1
    assert report['rules']['automatic_completed'] == 0
    assert db.one('SELECT * FROM rules WHERE id=?', (rule['id'],)) == before


def test_stale_classification_is_not_presented_as_a_current_purpose_hint(client):
    pid, rule, source, review = seed(client)
    rec = {'state': 'needs_full_source', 'content_review_state': 'needs_original_comparison', 'classification_purpose': 'conditional'}
    item = describe(rule, source, rec, False, {})
    assert item['state'] == 'stale' and item['purpose_hint'] is None
    assert item['issue_kind'] == 'source_text'
    crop = describe(rule, source, {**rec, 'content_review_state': 'source_cropped'}, False, {})
    assert crop['content_status'] != 'source_cropped' and '裁断' not in crop['reason']


def test_same_page_partial_excerpt_does_not_cover_entire_candidate(client):
    pid, rule, source, review = seed(client)
    review.pop('candidate_replacements')
    result = repair.apply(pid, review, [{'rule': rule, 'source': source}])
    after = db.one('SELECT * FROM rules WHERE id=?', (rule['id'],))
    item = next(e for e in auto.report(pid)['exceptions'] if e['id'] == rule['id'])
    assert result['pending_rule_ids'] == [rule['id']]
    assert item['supporting_excerpt']['text'] == review['text']
    assert item['supporting_excerpt']['supports_entire_candidate'] is False
    assert after['statement'] == rule['statement'] and after['state'] == 'candidate'


def test_cropped_original_keeps_unresolved_candidate_even_when_partial_quote_matches(client):
    pid, rule, source, review = seed(client)
    review['source_issues'] = ['cropped_original']
    result = repair.apply(pid, review, [{'rule': rule, 'source': source}])
    after = db.one('SELECT * FROM rules WHERE id=?', (rule['id'],))
    rec = auto.receipt(pid, 'rule', rule['id'])
    assert result['fixed_rule_ids'] == [] and result['pending_rule_ids'] == [rule['id']]
    assert after['statement'] == rule['statement'] and after['source_id'] == source['id'] and after['state'] == 'candidate'
    assert rec['completed'] is False and rec['quote'] is None and rec['content_review_state'] == 'source_cropped'
    item = next(e for e in auto.report(pid)['exceptions'] if e['id'] == rule['id'])
    assert item['content_status'] == 'source_cropped' and '未包含完整句子' in item['reason']
    coverage = document_coverage.file_report(pid, source['file_id'])
    assert coverage['assets'][0]['source_issues'] == ['cropped_original']
    original = db.one('SELECT * FROM files WHERE id=?', (source['file_id'],))
    page = next(p for p in document_structure.extract(original) if p['origin'] == 'assistant_visual_excerpt')
    candidate = next(document_structure.candidates(page, 'paragraphs'))
    assert 'cropped_original' in candidate['issues']
    scope = knowledge_units.observation_scope(candidate['locator'])
    assert scope['original_content_complete'] is False and not scope['entire_original_image_transcribed']
    # Omitting the already recorded issue cannot certify the same bytes as complete.
    next_review = {k: v for k, v in review.items() if k != 'source_issues'}
    with pytest.raises(ValueError, match='裁断'):
        repair.apply(pid, {**next_review, 'coverage': 'complete_image_text'}, [{'rule': after, 'source': auto.rule_source(after)}])
    again = repair.apply(pid, next_review, [{'rule': after, 'source': auto.rule_source(after)}])
    assert again['pending_rule_ids'] == [rule['id']]


def test_crop_flag_cannot_coexist_with_full_transcription_claim(client):
    pid, rule, source, review = seed(client)
    with pytest.raises(ValueError, match='裁断'):
        repair.apply(pid, {**review, 'source_issues': ['cropped_original'], 'coverage': 'complete_image_text'}, [{'rule': rule, 'source': source}])
    assert db.one('SELECT * FROM rules WHERE id=?', (rule['id'],)) == rule


def test_missing_excerpt_cannot_be_replaced_by_another_pages_excerpt(client):
    pid, rule, source, review = seed(client)
    rec = {'state': 'needs_full_source', 'content_review_state': 'needs_original_comparison', 'verified_excerpt_source_id': 'other'}
    item = describe(rule, source, rec, True, {'other': {**source, 'page_id': 'another-page'}})
    assert 'supporting_excerpt' not in item


def test_later_partial_inspection_keeps_other_checked_passages_readable(client):
    pid, rule, source, review = seed(client)
    first = repair.apply(pid, review, [{'rule': rule, 'source': source}])
    rid = db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,created_at) VALUES(?,?,?,?,?)',
               (rid, pid, source['id'], '另一个位置的OCR残缺', db.now()))
    other = db.one('SELECT * FROM rules WHERE id=?', (rid,))
    import hashlib
    second_text = '正文中介绍使用该技术解决的具体问题。'
    second_review = {**review, 'text': second_text,
                     'candidate_replacements': {rid: {'previous_statement_hash': hashlib.sha256(other['statement'].encode()).hexdigest(), 'quote': second_text}}}
    repair.apply(pid, second_review, [{'rule': other, 'source': auto.rule_source(other)}])
    read = document_coverage.read_asset(pid, source['file_id'], review['asset_sha256'], 1)
    assert {passage['text'] for passage in read['verified_passages']} == {review['text'], second_text}
    assert first['replacement_source_ids'][0] not in db.setting('document_observation_supersessions', {})
    original = db.one('SELECT * FROM files WHERE id=?', (source['file_id'],))
    parts = document_structure.extract(original)
    assert {part['text'] for part in parts if part['origin'] == 'assistant_visual_excerpt'} == {review['text'], second_text}
    checked = [next(document_structure.candidates(part, 'paragraphs')) for part in parts if part['origin'] == 'assistant_visual_excerpt']
    assert len({part['locator']['context_stream'] for part in checked}) == 2


def test_specific_original_inspection_blocker_is_visible_in_exception(client):
    pid, rule, source, review = seed(client)
    review.pop('candidate_replacements')
    review['reason'] = '原图的作者与年份被遮挡；只能核对标题，不能猜测被遮挡书目字段。'
    repair.apply(pid, review, [{'rule': rule, 'source': source}])
    item = next(e for e in auto.report(pid)['exceptions'] if e['id'] == rule['id'])
    assert '作者与年份被遮挡' in item['reason'] and item['issue_kind'] == 'source_text'

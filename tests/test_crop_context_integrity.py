import json
import pytest
from test_workbench import client
from test_visual_rule_repair import seed
from app import db, document_structure, document_observations as observations, document_coverage, knowledge_units, ai_autopilot as auto, provider


def test_unknown_legacy_transcription_coverage_cannot_hide_full_ocr(client):
    pid, rule, source, review = seed(client)
    tail = '原图尾部条件：仅作课堂案例，不作为当前项目要求。'
    text = '完整观察内容。' * 200 + tail
    db.execute('UPDATE sources SET text=? WHERE id=?', (text, source['id']))
    revised = db.uid()
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)', (revised, source['file_id'], source['page_id'],
        db.dumps({'evidence_asset_sha256': review['asset_sha256']}), '局部标题', '局部标题', 'rule_candidate', 'visual_transcription', 0))
    db.set_setting('document_observation_supersessions', {source['id']: {'replacement_source_id': revised}})
    original = db.one('SELECT * FROM files WHERE id=?', (source['file_id'],))
    parts = document_structure.extract(original)
    assert next(p['text'] for p in parts if p['origin'] == 'unverified_image_transcript') == text
    assert next(p['text'] for p in parts if p['origin'] == 'visual_transcription') == '局部标题'


def test_full_transcription_of_another_asset_cannot_replace_this_observation():
    raw = {'id': 'raw', 'method': 'windows_ocr', 'text': '这张图片的完整条件。',
           'locator': db.dumps({'asset_sha256': 'first', 'char_start': 0})}
    other = {'id': 'other', 'method': 'assistant_visual_excerpt', 'text': '另一张图片。',
             'locator': db.dumps({'evidence_asset_sha256': 'second', 'transcription_scope': 'complete_image_text'})}
    result = observations.observations([raw, other], {'raw': {'replacement_source_id': 'other'}}, preserve_unverified=True)
    assert next(o['text'] for o in result if o['origin'] == 'unverified_image_transcript') == raw['text']


def test_saved_engine_result_recovers_missing_tail_beyond_old_storage_window(client):
    pid, rule, source, review = seed(client)
    text = '参考文献需保留作者与年份。' * 120 + '尾部例外：不得据此补造实验数据。'
    loc = db.loads(source['locator'])
    loc.update(recognition=[{'asset_sha256': review['asset_sha256'], 'text': text}])
    db.execute('UPDATE sources SET text=?,locator=? WHERE id=?', (text[:1000], db.dumps(loc), source['id']))
    full = document_coverage.read_asset(pid, source['file_id'], review['asset_sha256'], 1)
    assert full['raw_observations'][0]['text'] == text
    assert full['raw_observations'][0]['cannot_support_claims']
    # The source route also reads the full observation behind the old candidate.
    reply = client.get(f'/api/v1/rules/{rule["id"]}/source')
    assert reply.status_code == 200
    context = reply.json()['reading_context']
    assert context['raw_observations'][0]['text'] == text
    assert context['full_observation_characters'] == len(text)
    assert 'legacy_source_is_not_complete_image_observation' in context['source_issues']
    assert db.one('SELECT state FROM rules WHERE id=?', (rule['id'],))['state'] == 'candidate'


def test_filtered_native_matching_lines_are_retained_in_complete_engine_view(client):
    pid, rule, source, review = seed(client)
    text = '标题与原生文字相同。\n截图中另有完整条件。\n原生重复行仍须保留位置。'
    loc = db.loads(source['locator'])
    loc['recognition'] = [{'text': text}]
    row = {**source, 'text': '截图中另有完整条件。', 'locator': db.dumps(loc)}
    values = observations.observations([row])
    assert len(values) == 1 and values[0]['text'] == text


def test_overlapping_storage_windows_rejoin_without_duplicating_or_losing_text():
    text = '每张原表保留本行条件，不跨列推断。' * 100
    def row(identity, start, end):
        return {'id': identity, 'method': 'windows_ocr', 'text': text[start:end],
                'locator': db.dumps({'asset_sha256': 'same-image', 'char_start': start})}
    values = observations.observations([row('a', 0, 1000), row('b', 800, 1400), row('c', 1300, len(text))])
    assert len(values) == 1 and values[0]['text'] == text and values[0]['issues'] == []


def test_conflicting_saved_recognition_outputs_remain_independent():
    values = []
    for identity, text in (('a', '参考文献不少于10篇。'), ('b', '参考文献不少于20篇。')):
        values.append({'id': identity, 'method': 'windows_ocr', 'text': text, 'locator': db.dumps({
            'asset_sha256': 'same-image', 'char_start': 0, 'recognition': [{'text': text}]})})
    result = observations.observations(values)
    assert {o['text'] for o in result} == {v['text'] for v in values}
    assert all('conflicting_recognition_results' in o['issues'] for o in result)


def test_source_coverage_and_locator_changes_invalidate_parse(client):
    pid, rule, source, review = seed(client)
    parsed = knowledge_units.build(pid, source['file_id'], allow_material=True)
    assert knowledge_units.parse_current(parsed)
    db.set_setting('document_observation_supersessions', {source['id']: {'replacement_source_id': 'unknown'}})
    assert not knowledge_units.parse_current(parsed)
    newer = knowledge_units.build(pid, source['file_id'], allow_material=True)
    assert newer['id'] != parsed['id']
    loc = db.loads(source['locator']); loc['recognition'] = [{'text': '恢复的完整观察'}]
    db.execute('UPDATE sources SET locator=? WHERE id=?', (db.dumps(loc), source['id']))
    assert not knowledge_units.parse_current(newer)


def test_model_evidence_keeps_distant_exception_after_twelve_short_matches():
    statement = '论文需要图表说明。'
    text = '\n'.join([statement] * 20 + ['例外：本页为反例，不得作为当前项目要求。'])
    item = {'statement': statement, 'text': text, 'context': '原页标题：反例'}
    outgoing = auto.wire_item('rules', item)
    assert any(unit['text'] == text for unit in outgoing['evidence_units'])
    assert outgoing['context'] == item['context']


def test_model_context_never_silently_crops_long_source_or_page(monkeypatch):
    monkeypatch.setattr(provider, 'config', lambda: {'context_limit': 500000})
    text = '正文完整来源。' * 3000
    page = '原页完整上下文。' * 3000
    assert auto.context({'text': text, 'page_text': page}, '正文') == (text, page)
    monkeypatch.setattr(provider, 'config', lambda: {'context_limit': 10000})
    with pytest.raises(ValueError, match='未裁剪'):
        auto.context({'text': text, 'page_text': page}, '正文')


def test_complete_ocr_cannot_be_adopted_as_verified_by_high_model_confidence(client):
    pid, rule, source, review = seed(client)
    decision = {'purpose': 'knowledge', 'quote': source['text'], 'reason': '模型认为候选符合课堂知识说明。', 'confidence': .999}
    with pytest.raises(ValueError, match='尚未逐字对照'):
        auto.apply_rule(pid, rule, source, decision, source['text'], 'synthetic', 'synthetic', 'synthetic')
    assert db.one('SELECT * FROM rules WHERE id=?', (rule['id'],)) == rule


def test_page_review_flag_does_not_approve_a_different_ocr_candidate(client):
    pid, rule, source, review = seed(client)
    db.execute("UPDATE pages SET review_state='reviewed' WHERE id=?", (source['page_id'],))
    reply = client.get(f'/api/v1/rules/{rule["id"]}/source').json()
    assert reply['reading_context']['requires_original_comparison']
    decision = {'purpose': 'knowledge', 'quote': source['text'], 'reason': '页面有旧审核标记但本条文字未核验。', 'confidence': .999}
    with pytest.raises(ValueError, match='尚未逐字对照'):
        auto.apply_rule(pid, rule, source, decision, source['text'], 'synthetic', 'synthetic', 'synthetic')


def test_extraction_batch_preserves_a_condition_crossing_the_old_character_cutoff():
    from app.rule_pipeline import batches
    text = '说明。' * 999 + '引用不少于20篇，但案例资料不得当作当前项目事实。'
    source = {'id': 'source', 'text': text, 'method': 'native', 'locator': '{}'}
    result = batches([source], {'context_limit': 100000, 'max_output_tokens': 4096})
    assert len(result) == 1 and len(result[0]) == 1
    assert result[0][0]['text'] == text and result[0][0]['part_end'] == len(text)
    with pytest.raises(ValueError, match='未按字符切断'):
        batches([source], {'context_limit': 5000, 'max_output_tokens': 4096})

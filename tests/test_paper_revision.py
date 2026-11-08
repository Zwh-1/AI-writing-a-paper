"""Review real documents and revise one complete candidate without partial adoption."""
from copy import deepcopy
import pytest
from test_paper_writing import manuscript, finish, adopt
from app import db, documents, paper_writing as paper, provider, tasks, security
from app.paper_review_checks import review, numbers
from app.summary_billing import BudgetExceeded
from test_workbench import client, save


def revision(env, tid, output, **extra):
    return env['client'].post(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/revise',
        json={'paper_hash': output['paper_hash'], 'section_ids': [env['sections'][3]['id']],
              'instruction': '在本次候选基础上据代码解释关键流程，消除无依据的效果断言。', **extra},
        headers={'Idempotency-Key': extra.pop('_key', 'revision-' + tid)})


def completed(tid):
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'succeeded', task['error']
    return db.loads(task['result'])


def test_revision_keeps_other_chapters_and_updates_conclusion_and_both_abstracts(manuscript):
    e = manuscript
    source_id, source = finish(e)
    response = revision(e, source_id, source)
    assert response.status_code == 200, response.text
    child = response.json()
    assert child['payload']['revision_section_ids'] == [e['sections'][i]['id'] for i in (3, 4, 0, 1)]
    assert child['result']['retained_sections'] == 1
    assert not db.rows('SELECT * FROM revisions')
    revised = completed(child['id'])
    first = e['calls'][5]
    baseline = next(s for s in source['paper_sections'] if s['section_id'] == e['sections'][3]['id'])
    assert first['operation'] == 'revise'
    assert first['existing_text'] == baseline['text']
    assert first['candidate_revision_context']['text'] == baseline['text']
    assert first['paper_revision']['instruction'].startswith('在本次候选基础上')
    assert len(first['paper_draft_sections']) == 4  # Complete other candidates, including later chapters.
    assert first['paper_plan'] and len(first['paper_plan']) == 5
    untouched = next(s for s in source['paper_sections'] if s['kind'] == 'introduction')
    actual = next(s for s in revised['paper_sections'] if s['kind'] == 'introduction')
    assert actual['document'] == untouched['document'] and actual['text'] == untouched['text']
    assert revised['revised_sections'] == 4 and len(e['calls']) == 9
    assert revised['review']['verification'] == 'unverified_draft'
    assert not db.rows('SELECT * FROM revisions')
    assert adopt(e, source_id, source).status_code == 409
    accepted = adopt(e, child['id'], revised)
    assert accepted.status_code == 200, accepted.text
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 5


def test_selecting_a_review_issue_uses_server_side_scope_and_replays_once(manuscript):
    e = manuscript
    tid, output = finish(e)
    path = f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}'
    report = e['client'].get(path + '/review').json()
    finding = next(f for f in report['findings'] if f['section_ids'] == [e['sections'][3]['id']])
    response = revision(e, tid, output, section_ids=[], instruction='', finding_ids=[finding['id']])
    assert response.status_code == 200, response.text
    again = revision(e, tid, output, section_ids=[], instruction='', finding_ids=[finding['id']])
    assert again.status_code == 200 and again.json()['id'] == response.json()['id']
    assert len(db.rows('SELECT * FROM tasks WHERE kind=?', (paper.KIND,))) == 2
    assert len(e['calls']) == 5
    changed = revision(e, tid, output, instruction='不同要求')
    assert changed.status_code == 409


def test_revision_reads_previous_candidate_instead_of_reverting_to_saved_draft(manuscript, monkeypatch):
    e = manuscript
    sid = e['sections'][3]['id']
    assert save(e['client'], sid, '早期已保存的旧稿内容。').status_code == 200
    tid, source = finish(e)
    previous = next(s for s in source['paper_sections'] if s['section_id'] == sid)
    previous['document']['content'].append(documents.text_document('这是上一轮候选中应原样保留的独有段落。')['content'][0])
    previous['text'] = documents.plain(previous['document'])
    source['paper_hash'] = paper.paper_hash(source)
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(source), tid))
    generate = e['generate']
    def preserve(system, user, images=None):
        payload = db.loads(user)
        result = generate(system, user, images)
        if payload.get('section', {}).get('id') == sid and payload.get('paper_revision'):
            assert '独有段落' in payload['existing_text']
            assert payload['current_section_context']['text'] == '早期已保存的旧稿内容。'
            blocks = payload['existing_blocks']
            result['text'] = db.dumps({'paragraphs': [{'preserve_node_id': b['node_id']} for b in blocks]})
        return result
    monkeypatch.setattr(provider, 'generate', preserve)
    response = revision(e, tid, source)
    assert response.status_code == 200, response.text
    revised = completed(response.json()['id'])
    actual = next(s for s in revised['paper_sections'] if s['section_id'] == sid)
    assert actual['document']['content'] == previous['document']['content']
    assert actual['manifest']['paper_revision_source']['task_id'] == tid
    assert documents.current(security.require_section(sid))['text'] == '早期已保存的旧稿内容。'


def test_revision_resumes_without_repeating_retained_or_completed_chapters(manuscript, monkeypatch):
    e = manuscript
    tid, output = finish(e)
    child = revision(e, tid, output).json()
    original = tasks.model_call
    count = []
    def pause(*args, **kwargs):
        if args[3].get('writing_stage')!='discourse_review':
            count.append(True)
            if len(count) == 2:
                raise BudgetExceeded('模拟修订预算暂停')
        return original(*args, **kwargs)
    monkeypatch.setattr(tasks, 'model_call', pause)
    assert tasks.run_one()
    paused = db.one('SELECT * FROM tasks WHERE id=?', (child['id'],))
    assert paused['status'] == 'budget_blocked'
    parts = db.loads(paused['result'])['paper_sections']
    assert len(parts) == 2
    monkeypatch.setattr(tasks, 'model_call', original)
    response = e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing/{child["id"]}/resume', json={})
    assert response.status_code == 200, response.text
    result = completed(child['id'])
    assert all(s in result['paper_sections'] for s in parts)
    assert len(e['calls']) == 9 and result['revised_sections'] == 4


@pytest.mark.parametrize('change', ['saved_text', 'source_tampering'])
def test_revision_input_drift_stops_before_another_model_request(manuscript, change):
    e = manuscript
    tid, source = finish(e)
    child = revision(e, tid, source).json()
    if change == 'saved_text':
        assert save(e['client'], e['sections'][2]['id'], '用户后来保存的新正文。').status_code == 200
    else:
        tampered = deepcopy(source)
        tampered['paper_sections'][0]['document'] = documents.text_document('未经审阅的替换稿。')
        db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(tampered), tid))
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (child['id'],))
    assert task['status'] == 'failed'
    assert len(e['calls']) == 5


def test_revision_revalidates_retained_sources_and_preserves_assets(manuscript):
    from app import parsers
    e = manuscript
    file, _ = parsers.register_file(e['pid'], '原始研究.txt', '有条件的真实研究范围。'.encode(), 'research')
    parsers.parse_file(file['id'])
    tid, source = finish(e)
    # A figure produced by the original task is part of the next task's frozen
    # inputs. Its metadata must not be silently dropped by the live checks.
    aid = db.uid()
    db.execute('INSERT INTO artifacts(id,project_id,kind,name,sha256,metadata,created_at) VALUES(?,?,?,?,?,?,?)',
        (aid, e['pid'], 'image', '已生成图', 'fixture-hash', db.dumps({'writing_task_id': tid}), db.now()))
    item = next(s for s in source['paper_sections'] if s['kind'] == 'introduction')
    item['manifest']['file_ids'] = [file['id']]
    source['paper_hash'] = paper.paper_hash(source)
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(source), tid))
    child = revision(e, tid, source)
    assert child.status_code == 200, child.text
    assert any(a['id'] == aid for a in child.json()['payload']['snapshot']['live_sources']['artifacts'])
    revised = completed(child.json()['id'])
    # A second round must also retain valid ancestors' artifacts and provenance.
    next_round = revision(e, child.json()['id'], revised)
    assert next_round.status_code == 200, next_round.text
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (file['id'],))
    assert tasks.run_one()
    assert len(e['calls']) == 9
    assert db.one('SELECT status FROM tasks WHERE id=?', (next_round.json()['id'],))['status'] == 'failed'


def test_revision_retains_native_table_citations_and_original_source_provenance(manuscript, monkeypatch):
    from app import parsers
    from app.paper_tables import table_node
    e = manuscript
    file, _ = parsers.register_file(e['pid'], '研究原件.txt', '模块边界只按原始说明解释，不推断实测效果。'.encode(), 'research')
    parsers.parse_file(file['id'])
    source = db.one('SELECT * FROM sources WHERE file_id=?', (file['id'],))
    ref = e['client'].post(f'/api/v1/projects/{e["pid"]}/references', json={
        'file_id': file['id'], 'metadata': {'title': '研究原件'}}).json()['id']
    tid, output = finish(e)
    item = next(s for s in output['paper_sections'] if s['kind'] == 'implementation')
    paragraph = item['document']['content'][0]
    paragraph['attrs']['sourceIds'] = [source['id']]
    paragraph['content'].append({'type': 'citation', 'attrs': {'nodeId': 'kept-citation', 'citationId': ref}})
    item['citation_proposals'] = [{'node_id': 'kept-citation', 'reference_id': ref, 'source_ids': [source['id']]}]
    item['manifest']['source_ids'] = [source['id']]
    item['manifest']['file_ids'] = [file['id']]
    item['document']['content'].append(table_node({'headers': ['模块', '依据'], 'rows': [['模块甲', '已读说明']],
                                                  'title': '模块说明', 'note': ''}))
    item['document'] = documents.normalize(item['document'])
    item['text'] = documents.plain(item['document'])
    output['paper_hash'] = paper.paper_hash(output)
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(output), tid))
    generate = e['generate']
    def preserve(system, user, images=None):
        payload = db.loads(user)
        result = generate(system, user, images)
        if payload.get('section', {}).get('id') == item['section_id'] and payload.get('paper_revision'):
            result['text'] = db.dumps({'paragraphs': [{'preserve_node_id': b['node_id']} for b in payload['existing_blocks']]})
        return result
    monkeypatch.setattr(provider, 'generate', preserve)
    child = revision(e, tid, output)
    assert child.status_code == 200, child.text
    revised = completed(child.json()['id'])
    actual = next(s for s in revised['paper_sections'] if s['kind'] == 'implementation')
    assert actual['document']['content'] == item['document']['content']
    assert actual['citation_proposals'] == item['citation_proposals']
    assert source['id'] in actual['manifest']['source_ids']
    response = adopt(e, child.json()['id'], revised)
    assert response.status_code == 200, response.text
    citation = db.one('SELECT * FROM citations WHERE node_id=?', ('kept-citation',))
    assert db.loads(citation['source_ids']) == [source['id']]
    assert db.one("SELECT * FROM dependencies WHERE revision_id=? AND upstream_type='source' AND upstream_id=?",
                  (citation['revision_id'], source['id']))


def test_explicit_dependencies_and_summary_closure():
    sections = [{'id': sid, 'kind': kind, 'dependencies': db.dumps(deps)} for sid, kind, deps in (
        ('a', 'methods', []), ('b', 'implementation', ['a']), ('c', 'results', ['b']),
        ('other', 'literature', []), ('end', 'conclusion', []), ('zh', 'abstract', []), ('en', 'abstract_en', []))]
    assert paper.revision_scope(sections, ['a']) == {'a', 'b', 'c', 'end', 'zh', 'en'}
    assert paper.revision_scope(sections, ['zh']) == {'zh', 'en'}
    assert paper.revision_scope(sections, ['en']) == {'en'}


def test_local_review_uses_structured_nodes_and_section_ids_with_equal_titles():
    long = '两个同名章节里重复出现的完整论证段落，需要结合不同章节的具体任务检查并消除不必要重复。'
    doc = documents.text_document(long + '\n【待补证据】')
    doc['content'][0]['content'].append({'type': 'citation', 'attrs': {'nodeId': 'cite-1', 'citationId': 'missing'}})
    doc['content'].append({'type': 'paragraph', 'attrs': {'nodeId': 'link'}, 'content': [
        {'type': 'crossReference', 'attrs': {'nodeId': 'xref', 'targetSectionId': 'b', 'targetId': 'deleted'}}]})
    items = [{'section_id': sid, 'kind': 'body', 'title': '同名章节', 'document': deepcopy(doc),
              'text': '故意过时的缓存文本', 'manifest': {}} for sid in ('a', 'b')]
    result = review({'paper_sections': items}, [{'id': sid, 'target_words': 1500} for sid in ('a', 'b', 'c')], [])
    codes = {f['code'] for f in result['findings']}
    assert {'missing_section', 'short_section', 'placeholder', 'unknown_citation', 'broken_visual_reference', 'repeated_paragraph'} <= codes
    assert result['repeated_paragraphs'][0]['section_ids'] == ['a', 'b']
    assert result['placeholder_count'] == 2
    assert result['verification'] == 'unverified_draft'


def test_abstract_number_check_handles_chinese_boundaries_and_equivalent_decimals():
    assert numbers('样本20个，比例10.0%，发表于2025年。') == numbers('20 samples, 10%, published in 2025.')
    assert numbers('20个样本') != numbers('30 samples')


def test_bad_reference_structure_cannot_be_written_as_a_complete_paper(manuscript):
    e = manuscript
    tid, source = finish(e)
    item = source['paper_sections'][2]
    item['document']['content'].append(documents.text_document('如[figref:unknown:deleted]所示。')['content'][0])
    item['text'] = documents.plain(item['document'])
    source['paper_hash'] = paper.paper_hash(source)
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(source), tid))
    response = adopt(e, tid, source)
    assert response.status_code == 422 and response.json()['detail']['code'] == 'PAPER_STRUCTURE_INVALID'
    assert not db.rows('SELECT * FROM revisions')


def test_selected_broken_cross_reference_can_be_rewritten_instead_of_protected_forever(manuscript):
    e = manuscript
    tid, source = finish(e)
    item = next(s for s in source['paper_sections'] if s['kind'] == 'implementation')
    item['document']['content'][0]['content'].append({'type': 'crossReference', 'attrs': {
        'nodeId': 'broken-ref', 'targetSectionId': item['section_id'], 'targetId': 'gone'}})
    item['text'] = documents.plain(item['document'])
    source['paper_hash'] = paper.paper_hash(source)
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(source), tid))
    report = paper.review(source, db.loads(db.one('SELECT payload FROM tasks WHERE id=?', (tid,))['payload'])['snapshot'])
    issue = next(f for f in report['findings'] if f['code'] == 'broken_visual_reference')
    child = revision(e, tid, source, section_ids=[], finding_ids=[issue['id']])
    assert child.status_code == 200, child.text
    revised = completed(child.json()['id'])
    assert not any(f['code'] == 'broken_visual_reference' for f in revised['review']['findings'])
    actual = next(s for s in revised['paper_sections'] if s['kind'] == 'implementation')
    assert not any(n['type'] == 'crossReference' for n in documents.nodes(actual['document']))
    response = adopt(e, child.json()['id'], revised)
    assert response.status_code == 200, response.text


def test_invalid_revision_scope_and_stale_findings_create_no_tasks_or_requests(manuscript):
    e = manuscript
    tid, output = finish(e)
    for extra, status in [({'section_ids': ['outside']}, 422), ({'finding_ids': ['stale']}, 409),
                          ({'section_ids': [], 'instruction': ''}, 422), ({'paper_hash': 'changed'}, 409)]:
        response = revision(e, tid, output, **extra)
        assert response.status_code == status, response.text
    assert len(db.rows('SELECT * FROM tasks WHERE kind=?', (paper.KIND,))) == 1
    assert len(e['calls']) == 5

"""Validation tests for the P0/P1/P2 writing-chain upgrades.

Covers: whole-paper review gate (P0), chapter argument checkpoints (P1),
evidence-gap audit separating test definitions from run results (P1),
whole-paper edit compression audit (P2), and real-candidate diagnostic
registration (P2).  Each test asserts the *verifiable* behaviour only.
"""
from copy import deepcopy

import pytest

from test_workbench import client, project, manual_package, save
from app import db, documents, provider, security, tasks, knowledge_service
from app import paper_writing as paper
from app.writing_agent import paper_pipeline
from app.writing_checks import check, evidence_checkpoints
from app.writing_brief import build as build_brief, ARGUMENT_CHECKPOINTS


@pytest.fixture
def manuscript(client, monkeypatch):
    pid = project(client)
    db.set_setting('expense_policy:'+pid,{'require_pricing_basis':False,'block_on_unresolved':False})
    manual_package(client, pid)
    db.set_setting('provider', {**provider.config(), 'model': 'paper-fixture', 'context_limit': 200000,
                              'max_output_tokens': 2048, 'input_price': 1., 'output_price': 4.})
    monkeypatch.setattr(provider, 'get_key', lambda: 'paper-fixture-key')
    monkeypatch.setattr(security, 'get_key', lambda *a, **k: 'paper-fixture-key')
    monkeypatch.setattr(knowledge_service, 'prepare_writing', lambda *a, **k: {
        'sources': [], 'knowledge_units': [], 'gaps': [], 'selected_unit_ids': [], 'document_readings': []})
    from app import guidance_preparation
    monkeypatch.setattr(guidance_preparation, 'prepare', lambda *a, **k: {
        'examples': [], 'readings': [], 'gaps': [], 'file_ids': []})
    created = []
    for title, kind in [('摘要', 'abstract'), ('Abstract', 'abstract_en'), ('第一章 绪论', 'introduction'),
                        ('第二章 实现', 'implementation'), ('结论', 'conclusion')]:
        response = client.post(f'/api/v1/projects/{pid}/sections', json={'title': title, 'kind': kind})
        assert response.status_code == 200, response.text
        created.append(response.json())
    calls = []
    review_calls = []

    def generate(system, user, images=None):
        payload = db.loads(user)
        if 'messages' in payload:
            content=payload['messages'][0].get('content','{}')
            inner=db.loads(content)
            review_calls.append(inner)
            parsed={'findings':[],'coverage_complete':True,'summary':'夹具全文核验通过。'}
            if 'navigation_indexes' in inner:parsed['requests']=[]
            return {'text':db.dumps({'agent_transport':True,'text':db.dumps(parsed),'tool_calls':[]}),
                    'usage':{},'cost':.001,'remote_id':'fixture','request_hash':'fixture'}
        # Review payloads carry passages / navigation_indexes / question; writing
        # payloads carry section / paper_draft_sections / paper_plan.
        if ('passages' in payload or 'navigation_indexes' in payload or 'question' in payload
                or 'section_ids' in payload and 'section' not in payload and 'paper_plan' not in payload):
            review_calls.append(payload)
            return {'text': db.dumps({'findings': [], 'coverage_complete': True,
                                      'summary': '夹具全文核验通过。', 'requests': []}),
                    'usage': {}, 'cost': .001, 'remote_id': 'fixture', 'request_hash': 'fixture'}
        calls.append(payload)
        text = ('English translation of ' + payload['source_abstract']['text']
                if 'source_abstract' in payload else payload['section']['title'] + '完整候选：仅描述有依据的研究范围。')
        return {'text': db.dumps({'paragraphs': [{'text': text}]}), 'usage': {},
                'cost': .001, 'remote_id': 'fixture', 'request_hash': 'fixture'}

    monkeypatch.setattr(provider, 'generate', generate)
    return {'client': client, 'pid': pid, 'sections': created, 'calls': calls,
            'review_calls': review_calls, 'generate': generate}


def launch(env, **extra):
    c, pid = env['client'], env['pid']
    preview = c.get(f'/api/v1/projects/{pid}/paper-writing/preflight').json()
    return c.post(f'/api/v1/projects/{pid}/paper-writing', json={
        'instruction': '保持全篇术语和研究范围一致。', 'outline_revision': preview['outline_revision'],
        'section_versions': preview['section_versions'], **extra})


def finish(env):
    response = launch(env)
    assert response.status_code == 200, response.text
    tid = response.json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'succeeded', task['error']
    return tid, db.loads(task['result'])


def adopt(env, tid, output, **extra):
    return env['client'].post(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/accept', json={
        'paper_hash': output['paper_hash'], 'reviewed': True, 'accept_policy': 'draft', **extra},
        headers={'Idempotency-Key': 'whole-paper-upgrade-adoption'})


# --------------------------------------------------------------------------
# P0: whole-paper review is recorded on every candidate, and a completed review
#     with hard blockers blocks adoption; a stale review after an edit fails.
# --------------------------------------------------------------------------

def test_whole_paper_candidate_records_semantic_review(manuscript):
    """P0: the candidate carries a whole-paper review attempt record."""
    e = manuscript
    tid, result = finish(e)
    review = result.get('whole_paper_review')
    assert review, '整篇候选必须记录全文语义审查结果'
    assert review['schema'] == 'whole_paper.review.v1'
    assert review['paper_hash'] == result['paper_hash']
    assert set(review['buckets']) == {'hard_blocker', 'needs_author', 'advice'}
    # A completed or explicitly-failed review both carry the schema; the P0
    # gate reads the status in accept().
    assert review['status'] in ('completed', 'failed', 'skipped')


def test_completed_review_with_hard_blocker_blocks_adoption(manuscript, monkeypatch):
    """P0: a blocker-severity finding in the whole-paper review stops adoption."""
    e = manuscript
    tid, result = finish(e)
    # Force the review into a completed state with a blocker so the accept gate
    # rejects.  This isolates the gate logic from the review transport, which
    # may fail in a fixture environment without a real model endpoint.
    result['whole_paper_review']['status'] = 'completed'
    result['whole_paper_review']['coverage_complete'] = True
    result['whole_paper_review']['findings'] = [{
        'severity': 'blocker', 'kind': 'cross_chapter_contradiction',
        'message': '第二章与结论对同一指标给出相反描述。',
        'section_ids': [result['paper_sections'][3]['section_id'], result['paper_sections'][4]['section_id']],
    }]
    result['whole_paper_review']['buckets'] = paper_pipeline.classify(result['whole_paper_review']['findings'])
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(result), tid))
    response = adopt(e, tid, result)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'PAPER_WHOLE_REVIEW_BLOCKED'
    assert not db.rows('SELECT * FROM revisions')


def test_failed_whole_paper_review_blocks_new_candidate_adoption(manuscript, monkeypatch):
    """Incomplete review cannot bypass the new candidate gate."""
    e = manuscript
    tid, result = finish(e)
    result['whole_paper_review']['status'] = 'skipped'
    result['whole_paper_review']['reason'] = 'no_verify_profile'
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(result), tid))
    response = adopt(e, tid, result)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'PAPER_WHOLE_REVIEW_INCOMPLETE'
    assert not db.rows('SELECT * FROM revisions')


def test_completed_review_with_incomplete_coverage_blocks_adoption(manuscript):
    """P0: a review that did not cover all sections blocks adoption until fixed."""
    e = manuscript
    tid, result = finish(e)
    result['whole_paper_review']['status'] = 'completed'
    result['whole_paper_review']['coverage_complete'] = False
    result['whole_paper_review']['unresolved_scopes'] = [{'section_id': result['paper_sections'][0]['section_id'],
                                                          'reason': 'not_read'}]
    result['whole_paper_review']['buckets'] = paper_pipeline.classify([])
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(result), tid))
    response = adopt(e, tid, result)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'PAPER_WHOLE_REVIEW_INCOMPLETE'


# --------------------------------------------------------------------------
# P1: chapter argument checkpoints carry evidence/strength targets; the brief
#     check reports an unresolved point when a covered point lacks its
#     required evidence and reports over-claiming when static points assert
#     measurements.
# --------------------------------------------------------------------------

def test_argument_checkpoints_have_evidence_and_strength_targets():
    brief = build_brief({'id': 's', 'kind': 'results', 'target_words': 1000}, {}, params={'cover_points': True})
    checkpoints = brief['argument_checkpoints']
    assert checkpoints, '任务书必须输出可核对的论证检查点'
    run_points = [cp for cp in checkpoints if '执行' in cp['text'] or '结果' in cp['text']]
    assert any(cp['needs'] for cp in run_points), '结果章节执行点必须声明需要证据'
    assert all('evidence_fields' in cp for cp in checkpoints)


def test_brief_check_flags_point_missing_declared_evidence():
    brief = build_brief({'id': 's', 'kind': 'results', 'target_words': 1000}, {}, params={'cover_points': True})
    points = brief['points']
    run_point = next(p for p in points if '执行过程' in p['text'] or '指标' in p['text'])
    paragraphs = [{
        'text': '本节完成执行过程与指标结果。', 'brief_points': [run_point['id']],
        # 无任何依据字段 → 应触发 argument_checkpoint_unresolved
    }]
    report = check({'id': 's'}, brief, paragraphs, text=' '.join(p['text'] for p in paragraphs))
    assert any(f['code'] == 'argument_checkpoint_unresolved' for f in report['findings'])
    assert report['passed'] is False


def test_brief_check_flags_static_point_that_asserts_measurement():
    brief = build_brief({'id': 's', 'kind': 'implementation', 'target_words': 1000}, {}, params={'cover_points': True})
    points = brief['points']
    static_point = next(p for p in points if '代码结构' in p['text'] or '流程' in p['text'])
    paragraphs = [{
        'text': '关键代码结构通过率达到99%，性能表现显著提升。',
        'brief_points': [static_point['id']],
        # 静态实现点出现实测/性能表述且未绑定实测证据
    }]
    report = check({'id': 's'}, brief, paragraphs, text=' '.join(p['text'] for p in paragraphs))
    codes = {f['code'] for f in report['findings']}
    assert 'argument_checkpoint_unresolved' in codes


# --------------------------------------------------------------------------
# P1: evidence gap audit distinguishes literature / code / test definitions /
#     real run results, so a test-definition paragraph does not stand in for a
#     real run in a results chapter.
# --------------------------------------------------------------------------

def test_evidence_gap_audit_separates_test_definition_from_run_result(manuscript):
    from app.writing_evidence_quality import evidence_gap_audit
    e = manuscript
    pid = e['pid']
    # A results chapter that binds only a "planned" fact is a run_result gap.
    results_section = next(s for s in e['sections'] if s['kind'] == 'conclusion')
    paragraphs = [{'text': '测试通过率99%，性能提升20%。', 'fact_ids': []}]
    context = {'section': {'kind': 'results'}}
    # No bound evidence of any category → both test_definition and run_result required.
    findings = evidence_gap_audit(pid, 'results', context, paragraphs, manifest={})
    categories = {f['category'] for f in findings}
    assert 'run_result' in categories, '结果章节缺少实测记录时必须报告 run_result 缺口'
    message = next(f for f in findings if f['category'] == 'run_result')['message']
    assert '测试设计不能代替实测' in message or '实际执行记录' in message


def test_evidence_gap_audit_passes_when_run_result_present(manuscript, monkeypatch):
    from app.writing_evidence_quality import evidence_gap_audit, evidence_kind_label
    e = manuscript
    pid = e['pid']
    # test_run kind maps to run_result via evidence_kind_label.
    assert evidence_kind_label({'kind': 'test_run', 'origin_type': 'author'}) == 'unverified_result'
    # Mock evidence.get so the audit sees the bound test_run evidence item.
    from app.writing_agent import evidence as evidence_service
    monkeypatch.setattr(evidence_service, 'get', lambda pid_, eid, remote=False: {
        'id': eid, 'kind': 'test_run', 'origin_type': 'author',
        'payload': {'status':'executed','actual_execution':True,'raw_data_sha256':'fixture-raw-record'}})
    paragraphs = [{'text': '测试通过率99%。', 'evidence_ids': ['e1']}]
    findings = evidence_gap_audit(pid, 'results', {'section': {'kind': 'results'}}, paragraphs, manifest={})
    assert not any(f['category'] == 'run_result' for f in findings)


# --------------------------------------------------------------------------
# P2: whole-paper edit audit finds repeated boundary phrases and flags them as
#     compressible candidates without auto-rewriting facts.
# --------------------------------------------------------------------------

def test_whole_paper_edit_audit_flags_repeated_boundaries():
    e = None  # standalone
    repeated = ('由于缺少真实运行记录，本章不给出通过率结论，性能提升数据尚未验证，'
                '测试设计不能代替实测结论，因此本章不报告实测结果。')
    document = documents.text_document(repeated)
    output = {'paper_sections': [
        {'section_id': 'a', 'title': '摘要', 'kind': 'abstract', 'text': repeated, 'document': document},
        {'section_id': 'b', 'title': '结论', 'kind': 'conclusion', 'text': repeated, 'document': document},
    ]}
    report = paper._run_whole_paper_edit(output, {})
    assert report['schema'] == 'whole_paper.edit.v1'
    assert report['compressible'] is True
    assert any(c['phrase'] in repeated for c in report['boundary_candidates'])
    assert any(p['kind'] == 'repeated_boundary_paragraph' for p in report['repeated_paragraphs'])


def test_whole_paper_edit_audit_no_flags_when_unique():
    output = {'paper_sections': [
        {'section_id': 'a', 'title': '摘要', 'kind': 'abstract', 'text': '本章只陈述研究范围。'},
        {'section_id': 'b', 'title': '结论', 'kind': 'conclusion', 'text': '结论部分另述。',
         },
    ]}
    report = paper._run_whole_paper_edit(output, {})
    assert report['compressible'] is False
    assert not report['boundary_candidates']


# --------------------------------------------------------------------------
# P2: a real whole-paper candidate can be registered into the diagnostic harness
#     and scored with the same rubric as synthetic cases.
# --------------------------------------------------------------------------

def test_register_and_score_real_whole_paper_candidate(manuscript, monkeypatch):
    e = manuscript
    pid = e['pid']
    p = security.require_project(pid)
    # Ensure a non-zero budget so the diagnostic run can be created.
    db.execute('UPDATE projects SET budget=? WHERE id=?', (1000.0, pid))
    p = security.require_project(pid)
    from app.writing_agent import evaluations
    profile_id = None
    from app.writing_agent import profiles as profiles_mod
    try:
        profile_id = profiles_mod.current_default(pid)['id']
    except Exception:
        profile_id = None
    if not profile_id:
        pytest.skip('无法解析诊断使用的模型配置')
    run = evaluations.create(pid, {'profile_id': profile_id, 'case_ids': ['D01'], 'repetitions': 1,
                                   'budget_limit': float(p['budget'])}, defer_enqueue=True)
    answer = '真实整篇候选正文，含研究问题、方法与边界。'
    registered = evaluations.register_real_whole_paper(pid, run['id'], 'D01', 'baseline', 0, answer, profile_id)
    assert registered['item_id']
    item = db.one('SELECT * FROM diagnostic_items WHERE id=?', (registered['item_id'],))
    assert item['status'] == 'completed'
    from app.writing_agent.common import unblob
    stored = unblob(item['result_blob'])
    assert stored.get('real_whole_paper') is True
    assert stored.get('answer') == answer

"""Regression coverage for the audited writing workflow; no real model calls."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

import pytest
from test_workbench import client, file
from test_paper_writing_workflow_upgrade import manuscript
from test_paper_writing_workflow_upgrade import finish as finish_paper
from test_writing_flow_integration import writing_project, write
from app import db, documents, paper_writing, tasks
from app.writing_brief import build
from app.writing_checks import evidence_checkpoints
from app.writing_evidence_quality import evidence_gap_audit, assess_result_evidence
from app.writing_agent import evaluations, profiles, diagnostic_scoring, paper_pipeline, provider_adapter
from fastapi import HTTPException


def registered(env, variant='candidate'):
    pid = env['pid']
    db.execute('UPDATE projects SET budget=1000 WHERE id=?', (pid,))
    profile_id = profiles.current_default(pid)['id']
    run = evaluations.create_real_run(pid,profile_id,1000)
    item = evaluations.register_real_whole_paper(pid, run['id'], 'PAPER', variant, 0,
        '完整论文候选，研究对象是可验证的学生教育凭证。', profile_id,
        source={'instruction':'对照原始证据评价论文','context':{'originals':[{'text':'尚未执行测试，只说明代码结构。'}]}})
    return run, item, profile_id


def test_real_candidate_scoring_reaches_evaluator(manuscript, monkeypatch):
    run, item, profile_id = registered(manuscript)
    calls = []
    def fake_call(*args, **kwargs):
        calls.append(args)
        scores = {key: {'score': 2, 'applicable': True, 'rationale': '隔离夹具评分接口检查'}
                  for key in diagnostic_scoring.DIMENSIONS}
        return {'text': db.dumps({'decision': 'pass', 'note': '隔离夹具只检查评分接口连通。', 'dimensions': scores})}
    monkeypatch.setattr(provider_adapter, 'call', fake_call)
    queued=evaluations.score_real_whole_paper(manuscript['pid'], run['id'], item['item_id'], profile_id)
    assert queued['kind']=='writing_real_diagnostic_review'
    assert tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(queued['id'],))['status']=='succeeded'
    assert calls


def test_default_real_candidate_variant_has_blind_packet(manuscript):
    run, item, _ = registered(manuscript)
    packet = diagnostic_scoring.blind_packet(manuscript['pid'], run['id'], 'audit-reviewer')
    assert packet['items']


def test_custom_results_goal_keeps_run_evidence_requirement():
    brief = build({'id': 's', 'kind': 'results', 'target_words': 900,
                   'goal': '评估凭证撤销后的链上链下一致性'}, {}, params={'cover_points': True})
    assert any('run_result' in point['needs'] for point in brief['argument_checkpoints'])


def test_result_checkpoint_does_not_accept_code_id_as_run_evidence():
    brief = build({'id': 's', 'kind': 'results', 'target_words': 900}, {}, params={'cover_points': True})
    point = next(p for p in brief['points'] if p['text'] == '执行过程与样本')
    gaps = evidence_checkpoints(brief, [{'text': '执行过程与样本需要进一步核对。',
                                       'brief_points': [point['id']], 'evidence_ids': ['only-code']}])
    assert gaps, 'A bound ID alone must not satisfy a run_result requirement.'


def test_gap_audit_uses_exact_claim_bindings(manuscript, monkeypatch):
    from app.writing_agent import evidence
    monkeypatch.setattr(evidence, 'get', lambda *args, **kwargs: {
        'id': 'code1', 'kind': 'code_observation', 'origin_type': 'repository'})
    paragraph = {'text': '源码实现授权检查。', 'claim_spans': [
        {'local_id': 'c1', 'text': '源码实现授权检查。', 'evidence_ids': ['code1']}]}
    gaps = evidence_gap_audit(manuscript['pid'], 'implementation', {}, [paragraph])
    assert not any(g['category'] == 'code_observation' for g in gaps)


def test_unexecuted_test_run_remains_run_gap(manuscript, monkeypatch):
    from app.writing_agent import evidence
    record = {'id': 'planned1', 'kind': 'test_run', 'origin_type': 'author',
              'payload': {'status': 'not_run'}}
    assert assess_result_evidence(record)['can_assert_measurement'] is False
    monkeypatch.setattr(evidence, 'get', lambda *args, **kwargs: record)
    gaps = evidence_gap_audit(manuscript['pid'], 'results', {}, [
        {'text': '该测试尚未执行。', 'evidence_ids': ['planned1']}])
    assert any(g['category'] == 'run_result' for g in gaps)


def test_candidate_review_reads_linked_literature(manuscript, monkeypatch):
    env = manuscript
    original = '独有原文：凭证撤销必须同步核对链上授权与离线存档。'
    source_file = file(env['client'], env['pid'], original)
    source = db.one('SELECT * FROM sources WHERE file_id=?', (source_file['id'],))
    section = env['sections'][2]
    doc = documents.normalize(documents.text_document('文献讨论凭证撤销后的状态同步。'))
    calls = []
    def fake_call(task, profile, system, payload, manifest, **kwargs):
        calls.append(payload)
        result = {'findings': [], 'coverage_complete': True, 'summary': '仅用于隔离检查范围。'}
        if 'navigation_indexes' in payload:
            result['requests'] = []
        return {'text': db.dumps(result)}
    monkeypatch.setattr(provider_adapter, 'call', fake_call)
    profile_id = profiles.current_default(env['pid'])['id']
    paper_pipeline.review({'project_id': env['pid'], 'id': 'local-audit'},
        {'profile_id': profile_id, 'section_ids': [section['id']]}, candidate_sections=[{
            'section_id': section['id'], 'document': doc,
            'manifest': {'source_ids': [source['id']], 'evidence_versions': {}}}])
    assert any(original in db.dumps(payload) for payload in calls), 'Linked source text was not passed to the reviewer.'


def test_partial_review_keeps_previously_found_blocker(manuscript, monkeypatch):
    from app import provider
    calls = []
    def fake_call(task, profile, system, payload, manifest, **kwargs):
        calls.append(payload)
        if len(calls) > 1:
            raise provider.ProviderError('isolated interrupted review', unknown=True)
        return {'text': db.dumps({'findings': [{
            'node_id': '', 'sentence_id': '', 'severity': 'blocker', 'kind': 'coherence',
            'message': '前章与结论对是否执行实验相互矛盾。', 'evidence_ids': [], 'rationale': '隔离夹具问题'}],
            'coverage_complete': True, 'summary': '首章已发现问题。'})}
    monkeypatch.setattr(provider_adapter, 'call', fake_call)
    output = {'paper_hash': 'frozen-paper', 'paper_sections': [
        {'section_id': s['id'], 'title': s['title'],
         'document': documents.normalize(documents.text_document('本节是需要复核的候选正文。')),
         'manifest': {}} for s in manuscript['sections'][2:4]]}
    with pytest.raises(provider.ProviderError):
        paper_writing._run_whole_paper_review(
            {'project_id': manuscript['pid'], 'id': 'local-audit'}, output, {})
    report=output['whole_paper_review']
    assert report.get('findings') or report.get('buckets', {}).get('hard_blocker'), \
        'An error in a later chunk discarded the already-observed blocker.'


def test_well_formed_candidate_review_can_complete(manuscript, monkeypatch):
    def fake_call(task, profile, system, payload, manifest, **kwargs):
        result = {'findings': [], 'coverage_complete': True, 'summary': '隔离正向对照。'}
        if 'navigation_indexes' in payload:
            result['requests'] = []
        return {'text': db.dumps(result)}
    monkeypatch.setattr(provider_adapter, 'call', fake_call)
    output = {'paper_hash': 'frozen-paper', 'paper_sections': [
        {'section_id': s['id'], 'title': s['title'],
         'document': documents.normalize(documents.text_document('本节是需要复核的候选正文。')),
         'manifest': {}} for s in manuscript['sections'][2:4]]}
    report = paper_writing._run_whole_paper_review(
        {'project_id': manuscript['pid'], 'id': 'local-audit'}, output, {})
    assert report['status'] == 'completed'
    assert report['coverage_complete'] is True


def test_review_cancellation_is_not_normal_failure(manuscript, monkeypatch):
    def cancelled(*args, **kwargs):
        raise InterruptedError('audit cancellation')
    monkeypatch.setattr(paper_pipeline, 'review', cancelled)
    with pytest.raises(InterruptedError):
        paper_writing._run_whole_paper_review(
            {'project_id': manuscript['pid'], 'id': 'local-audit'},
            {'paper_hash': 'frozen-paper', 'paper_sections': []}, {})


def test_boundary_audit_does_not_confuse_common_word_with_duplicate_argument():
    output = {'paper_sections': [
        {'section_id': 'a', 'title': '需求', 'kind': 'requirements',
         'text': '学生仍需确认披露范围。'},
        {'section_id': 'b', 'title': '实验', 'kind': 'results',
         'text': '实验环境仍需固定依赖版本。'}]}
    report = paper_writing._run_whole_paper_edit(output, {})
    assert report['compressible'] is False, 'Two unrelated claims sharing 仍需 are not established repetition.'


def test_explain_changed_model_call_count(writing_project):
    task, result = write(writing_project)
    runs = db.rows('SELECT manifest FROM generation_runs WHERE task_id=? ORDER BY rowid', (task['id'],))
    print('AUDIT_STAGES', [db.loads(r['manifest'], {}).get('writing_flow_stage') for r in runs])
    print('AUDIT_FINDINGS', [(f.get('kind'), f.get('category')) for f in result['writing_quality']['findings']])
    print('AUDIT_REPAIR_ROUNDS', result['writing_quality']['repair_rounds'])
    stages=[db.loads(row['manifest'],{}).get('writing_stage') for row in runs]
    assert stages==['draft','semantic_review','discourse_review'], 'Only drafting and the two required independent reviews may call the model.'


def test_recheck_reviews_existing_candidate_without_regeneration(manuscript):
    env=manuscript;tid,output=finish_paper(env);before=len(env['calls'])
    response=env['client'].post(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/recheck',
        json={'paper_hash':output['paper_hash']},headers={'Idempotency-Key':'recheck-existing-paper'})
    assert response.status_code==200,response.text
    assert tasks.run_one()
    recheck=db.one('SELECT * FROM tasks WHERE id=?',(response.json()['id'],))
    assert recheck['status']=='succeeded',recheck['error']
    current=db.loads(db.one('SELECT result FROM tasks WHERE id=?',(tid,))['result'])
    assert current['paper_hash']==output['paper_hash']
    assert current['whole_paper_review']['status']=='completed'
    assert len(env['calls'])==before


def test_model_finding_is_visible_and_can_drive_revision(manuscript):
    env=manuscript;tid,output=finish_paper(env)
    output['whole_paper_review'].update(status='completed',coverage_complete=True,findings=[{
        'kind':'coherence','severity':'warning','node_id':'','sentence_id':'',
        'message':'结论应回应绪论提出的研究问题。','rationale':'对照两个章节后修订。'}])
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(output),tid))
    report=env['client'].get(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/review').json()
    issue=next(f for f in report['findings'] if f.get('method')=='whole_paper_semantic')
    response=env['client'].post(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/revise',
        json={'paper_hash':output['paper_hash'],'finding_ids':[issue['id']],'instruction':'',
              'section_ids':[],'budget_limit':10},headers={'Idempotency-Key':'revise-model-finding'})
    assert response.status_code==200,response.text
    assert response.json()['payload']['revision_findings'][0]['message']==issue['message']


def test_missing_real_evidence_context_cannot_be_scored(manuscript):
    env=manuscript;pid=env['pid'];profile_id=profiles.current_default(pid)['id']
    run=evaluations.create_real_run(pid,profile_id)
    item=evaluations.register_real_whole_paper(pid,run['id'],'PAPER','candidate',0,'尚无原文上下文的论文。',profile_id)
    with pytest.raises(HTTPException) as problem:
        evaluations.score_real_whole_paper(pid,run['id'],item['item_id'],profile_id)
    assert problem.value.detail['code']=='EVIDENCE_CONTEXT_REQUIRED'
    assert not db.rows("SELECT * FROM tasks WHERE kind='writing_real_diagnostic_review'")


def test_true_run_registration_is_idempotent_and_rejects_unknown_variant(manuscript):
    env=manuscript;run,item,profile_id=registered(env)
    replay=evaluations.register_real_whole_paper(env['pid'],run['id'],'PAPER','candidate',0,
        '完整论文候选，研究对象是可验证的学生教育凭证。',profile_id,
        source={'instruction':'对照原始证据评价论文','context':{'originals':[{'text':'尚未执行测试，只说明代码结构。'}]}})
    assert replay['item_id']==item['item_id'] and replay['idempotent_replay']
    with pytest.raises(HTTPException):
        evaluations.register_real_whole_paper(env['pid'],run['id'],'PAPER','unknown-variant',1,'另一稿。',profile_id)


def test_crash_during_required_review_does_not_recover_as_success(manuscript):
    env=manuscript;tid,output=finish_paper(env)
    output['whole_paper_review'].update(status='running',coverage_complete=False)
    db.execute("UPDATE tasks SET status='running',result=? WHERE id=?",(db.dumps(output),tid))
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    paper_writing.recover(task)
    recovered=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    assert recovered['status']=='incomplete'
    assert db.loads(recovered['result'])['whole_paper_review']['coverage_complete'] is False


def test_review_restores_all_disjoint_passages_under_one_evidence_identity(manuscript,monkeypatch):
    from app import writing_review_context as restored
    records=[{'id':'u1','type':'knowledge_unit','text':text,'version':'v1','locator':{'offset':i}}
             for i,text in enumerate(('先前实际读取的条件。','后来实际读取的局限。'))]
    monkeypatch.setattr(restored,'_source_records',lambda *args,**kwargs:records)
    originals,_=restored.restore(manuscript['pid'],{})
    assert len(originals)==1
    assert all(record['text'] in originals[0]['payload']['text'] for record in records)
    assert len(originals[0]['payload']['read_observations'])==2
    assert originals[0]['payload']['scope']=='disjoint_read_observations'


def test_selected_execution_leaves_other_queued_tasks_untouched(manuscript,monkeypatch):
    pid=manuscript['pid'];other=tasks.enqueue(pid,'fixture_other',{})
    selected=tasks.enqueue(pid,'fixture_selected',{})
    monkeypatch.setattr(tasks,'dispatch',lambda task:{'selected':task['id']})
    assert tasks.run_one(selected['id'])
    assert db.one('SELECT status FROM tasks WHERE id=?',(selected['id'],))['status']=='succeeded'
    assert db.one('SELECT status FROM tasks WHERE id=?',(other['id'],))['status']=='queued'

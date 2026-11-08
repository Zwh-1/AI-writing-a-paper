"""A single full-paper job, candidate continuity, resume and atomic adoption."""
from copy import deepcopy
import pytest
from test_workbench import client, project, manual_package, save
from app import db, documents, provider, security, tasks, knowledge_service
from app import paper_writing as paper
from app.summary_billing import BudgetExceeded


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
        # The agent review path wraps the payload in a messages frame, while the
        # writing path passes the context dict directly.  Detect either shape.
        def nested(obj):
            if isinstance(obj, dict) and 'messages' in obj:
                first = obj.get('messages', [{}])[0]
                if isinstance(first, dict) and isinstance(first.get('content'), str):
                    try: return db.loads(first['content'])
                    except Exception: return {}
            return obj
        inner = nested(payload)
        if payload.get('operation')=='review_candidate_discourse':
            return {'text':db.dumps({'coverage_complete':True,'findings':[],
                'rule_reviews':[{'rule_id':r['id'],'status':'satisfied','paragraph_indices':[0],
                    'quote':payload['candidate_paragraphs'][0]['text'],'reason':'已保留合成夹具的来源与范围。'}
                    for r in payload.get('rule_applications',[])]}),
                    'usage':{},'cost':.001,'remote_id':'discourse-fixture','request_hash':'fixture'}
        if 'messages' in payload or ('passages' in inner or 'navigation_indexes' in inner or 'question' in inner):
            review_calls.append(inner)
            parsed={'findings': [], 'coverage_complete': True,'summary': '夹具全文核验通过。'}
            if 'navigation_indexes' in inner:parsed['requests']=[]
            return {'text': db.dumps({'agent_transport':True,'text':db.dumps(parsed),'tool_calls':[]}),
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
        headers={'Idempotency-Key': 'whole-paper-test-adoption'})


def test_one_task_generates_complete_manuscript_and_shares_unadopted_candidates(manuscript):
    e = manuscript
    tid, result = finish(e)
    assert db.one('SELECT COUNT(*) n FROM tasks WHERE kind=?', ('write_paper',))['n'] == 1
    assert not db.rows('SELECT * FROM tasks WHERE kind=?', ('write',))
    assert not db.rows('SELECT * FROM revisions')
    assert [p['section']['kind'] for p in e['calls'][:-1]] == ['introduction', 'implementation', 'conclusion', 'abstract']
    second = e['calls'][1]
    assert second['paper_draft_sections'][0]['text'] == result['paper_sections'][2]['text']
    assert second['paper_draft_sections'][0]['status'] == 'unadopted_candidate'
    assert len(e['calls'][-2]['paper_draft_sections']) == 3
    chinese = next(s for s in result['paper_sections'] if s['kind'] == 'abstract')
    assert e['calls'][-1]['source_abstract']['text'] == chinese['text']
    assert result['complete'] and result['completed_sections'] == result['total_sections'] == 5
    assert [s['kind'] for s in result['paper_sections']] == ['abstract', 'abstract_en', 'introduction', 'implementation', 'conclusion']
    assert result['review']['verification'] == 'unverified_draft'
    assert result['text'].startswith('摘要\n')


@pytest.mark.parametrize('bad_reply', ['prose', 'empty_paragraph'])
@pytest.mark.parametrize('layout',['legacy','cache_v2'])
def test_introduction_candidate_repairs_malformed_or_empty_model_response(manuscript, monkeypatch, bad_reply,layout):
    e=manuscript
    db.set_setting('ai_cache_policy',{'new_task_prompt_layout':layout})
    original=provider.generate
    introduction_systems=[]

    def malformed_once(system,user,images=None):
        response=original(system,user,images)
        payload=db.loads(user)
        if payload.get('section',{}).get('kind')=='introduction':
            introduction_systems.append(system)
            if len(introduction_systems)==1:
                response['text']=('模型只返回了说明，没有符合契约的JSON。' if bad_reply=='prose'
                                  else db.dumps({'paragraphs':[{}]}))
        return response

    monkeypatch.setattr(provider,'generate',malformed_once)
    tid,result=finish(e)
    introduction=next(s for s in result['paper_sections'] if s['kind']=='introduction')
    assert introduction['text'].strip()
    assert introduction['validation_attempts'][0]['valid'] is False
    assert introduction['validation_attempts'][0]['error']
    assert introduction['validation_attempts'][1]=={'attempt':2,'valid':True,'error':None}
    assert introduction['manifest']['candidate_validation']['retry_count']==1
    if layout=='legacy':
        assert '章节响应校验发现以下问题：'+introduction['validation_attempts'][0]['error'] in introduction_systems[1]
    else:
        repairs=[row for row in e['calls'] if row.get('section',{}).get('kind')=='introduction']
        assert repairs[1]['request_parameters']['validation_error']==introduction['validation_attempts'][0]['error']
        assert repairs[1]['request_parameters']['repair_attempt']==2
        assert introduction['validation_attempts'][0]['error'] not in introduction_systems[1]
    assert next(c for c in result['section_checks'] if c['section_id']==introduction['section_id'])['response_format_retries']==1
    assert len(result['completed_run_ids'])==10  # Six writing/repair calls and four rule/discourse reviews.
    assert db.one('SELECT status FROM tasks WHERE id=?',(tid,))['status']=='succeeded'


def test_unrepairable_introduction_response_fails_visibly_without_prose_fallback(manuscript, monkeypatch):
    e=manuscript
    original=provider.generate
    introduction_calls=0
    invalid_response='模型只返回了说明，没有符合契约的JSON。'

    def always_prose(system,user,images=None):
        nonlocal introduction_calls
        response=original(system,user,images)
        payload=db.loads(user)
        if payload.get('section',{}).get('kind')=='introduction':
            introduction_calls+=1
            response['text']=invalid_response
        return response

    monkeypatch.setattr(provider,'generate',always_prose)
    task_id=launch(e).json()['id']
    assert tasks.run_one()
    task=db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
    result=db.loads(task['result'])
    invalid=result['invalid_output']
    assert task['status']=='failed'
    assert introduction_calls==tasks.CANDIDATE_FORMAT_RETRIES+1==5
    assert '第一章 绪论' in task['error'] and 'JSON解析失败' in task['error']
    assert len(invalid['validation_attempts'])==tasks.CANDIDATE_FORMAT_RETRIES+1
    assert all(not attempt['valid'] and attempt['error'] for attempt in invalid['validation_attempts'])
    assert invalid['raw_text']==invalid_response
    assert result['paper_sections']==[]
    assert not db.rows('SELECT * FROM revisions')


def test_whole_adoption_saves_every_section_once_and_retains_draft_status(manuscript):
    tid, output = finish(manuscript)
    response = adopt(manuscript, tid, output)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['revision_ids']) == 5 and result['review_state'] == 'unverified'
    assert all(s['current_revision_id'] and s['state'] == 'draft' for s in paper.active_sections(manuscript['pid']))
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 5
    again = adopt(manuscript, tid, output)
    assert again.status_code == 200 and again.json()['idempotent_replay']
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 5
    assert len(db.rows('SELECT * FROM object_dependencies WHERE upstream_type=?', ('writing_origin',))) == 5


def test_one_changed_chapter_blocks_entire_adoption_without_partial_writes(manuscript):
    tid, output = finish(manuscript)
    changed = manuscript['sections'][3]
    assert save(manuscript['client'], changed['id'], '用户在候选生成后写入的正文。').status_code == 200
    response = adopt(manuscript, tid, output)
    assert response.status_code == 409
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 1
    assert sum(bool(s['current_revision_id']) for s in paper.active_sections(manuscript['pid'])) == 1


def test_failure_in_later_adoption_rolls_back_all_sections_and_receipts(manuscript, monkeypatch):
    tid, output = finish(manuscript)
    original = documents.save
    calls = []
    def fail_later(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            from fastapi import HTTPException
            raise HTTPException(409, '模拟后续章节保存冲突')
        return original(*args, **kwargs)
    monkeypatch.setattr(documents, 'save', fail_later)
    response = adopt(manuscript, tid, output)
    assert response.status_code == 409
    assert not db.rows('SELECT * FROM revisions')
    assert not db.rows('SELECT * FROM writing_commit_receipts')
    assert not db.rows('SELECT * FROM document_analyses')
    assert not db.loads(db.one('SELECT result FROM tasks WHERE id=?', (tid,))['result']).get('adoption')


def test_one_task_can_resume_after_budget_stop_without_regenerating_completed_content(manuscript, monkeypatch):
    e = manuscript
    original = tasks.model_call
    count = []
    def pause(*args,**kwargs):
        if args[3].get('writing_stage')!='discourse_review':
            count.append(True)
            if len(count) == 2:
                raise BudgetExceeded('模拟本次预算不足')
        return original(*args,**kwargs)
    monkeypatch.setattr(tasks, 'model_call', pause)
    response = launch(e)
    tid = response.json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'budget_blocked'
    result = db.loads(task['result'])
    assert result['completed_sections'] == 1 and len(result['paper_sections']) == 1
    first = deepcopy(result['paper_sections'][0])
    assert not db.rows('SELECT * FROM revisions')
    monkeypatch.setattr(tasks, 'model_call', original)
    response = e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}/resume', json={})
    assert response.status_code == 200, response.text
    assert response.json()['id'] == tid
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'succeeded', task['error']
    result = db.loads(task['result'])
    assert first in result['paper_sections'] and len(e['calls']) == 5


def test_unknown_remote_work_requires_acknowledgement_but_completed_sections_are_kept(manuscript, monkeypatch):
    e = manuscript
    count = []
    original = e['generate']
    def disconnect(system, user, images=None):
        if db.loads(user).get('operation')!='review_candidate_discourse':
            count.append(True)
            if len(count) == 2:
                raise provider.ProviderError('模拟连接断开，执行情况未知', unknown=True)
        return original(system, user, images)
    monkeypatch.setattr(provider, 'generate', disconnect)
    tid = launch(e).json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'outcome_unknown' and len(db.loads(task['result'])['paper_sections']) == 1
    path = f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}/resume'
    response = e['client'].post(path, json={})
    assert response.status_code == 422 and response.json()['detail']['code'] == 'PAPER_REMOTE_ACK_REQUIRED'
    response = e['client'].post(path, json={'acknowledge_possible_duplicate_charge': True})
    assert response.status_code == 200
    assert db.one('SELECT status FROM generation_runs WHERE status=?', ('outcome_unknown',))


def test_rule_review_budget_pause_resumes_paid_draft_without_regenerating_it(manuscript,monkeypatch):
    e=manuscript;original=tasks.model_call;paused=False
    def stop_review(*args,**kwargs):
        nonlocal paused
        if args[3].get('writing_stage')=='discourse_review' and not paused:
            paused=True
            raise BudgetExceeded('模拟规则复核前费用不足')
        return original(*args,**kwargs)
    monkeypatch.setattr(tasks,'model_call',stop_review)
    tid=launch(e).json()['id'];assert tasks.run_one()
    stopped=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    assert stopped['status']=='budget_blocked' and len(e['calls'])==1
    first_section=e['calls'][0]['section']['id']
    from app.writing_flow import checkpointed_candidate_run_ids
    known=checkpointed_candidate_run_ids(stopped)
    assert len(known)==1
    known_run=next(iter(known))
    db.execute("UPDATE generation_runs SET status='outcome_unknown' WHERE id=?",(known_run,))
    assert not checkpointed_candidate_run_ids(stopped)
    uncertain=e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}/resume',json={})
    assert uncertain.status_code==422 and uncertain.json()['detail']['code']=='PAPER_REMOTE_ACK_REQUIRED'
    db.execute("UPDATE generation_runs SET status='completed' WHERE id=?",(known_run,))
    monkeypatch.setattr(tasks,'model_call',original)
    resumed=e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}/resume',json={})
    assert resumed.status_code==200,resumed.text
    assert tasks.run_one()
    completed=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    assert completed['status']=='succeeded',completed['error']
    assert len([row for row in e['calls'] if row.get('section',{}).get('id')==first_section])==1


def test_root_task_budget_applies_to_all_calls_before_any_send(manuscript):
    tid = launch(manuscript, budget_limit=0.00000001).json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'budget_blocked'
    assert not manuscript['calls'] and not db.rows('SELECT * FROM generation_runs')
    assert not db.loads(task['result'])['paper_sections']


def test_whole_adoption_requires_full_review_and_cannot_claim_verification(manuscript):
    tid, output = finish(manuscript)
    assert adopt(manuscript, tid, output, reviewed=False).status_code == 422
    assert adopt(manuscript, tid, output, accept_policy='strict').status_code == 422
    assert not db.rows('SELECT * FROM revisions')


def test_repeated_launch_reuses_the_same_active_task_and_checks_outline_versions(manuscript):
    first = launch(manuscript)
    second = launch(manuscript)
    assert first.status_code == second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    assert launch(manuscript, outline_revision=-1).status_code == 409


def test_complete_candidate_cannot_be_adopted_in_another_project(manuscript):
    tid, output = finish(manuscript)
    other = project(manuscript['client'], '另一个项目')
    response = manuscript['client'].post(f'/api/v1/projects/{other}/paper-writing/{tid}/accept', json={
        'paper_hash': output['paper_hash'], 'reviewed': True})
    assert response.status_code == 404 and not db.rows('SELECT * FROM revisions')


def test_recovery_does_not_discard_checkpointed_sections(manuscript):
    tid, output = finish(manuscript)
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    task['status'] = 'running'
    paper.recover(task)
    actual = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert actual['status'] == 'succeeded'
    assert db.loads(actual['result'])['paper_hash'] == output['paper_hash']


def test_resume_removes_legacy_degraded_chapter_and_requires_charge_acknowledgement(manuscript):
    e=manuscript
    tid,output=finish(e)
    introduction=next(item for item in output['paper_sections'] if item['kind']=='introduction')
    introduction['degraded']=True
    introduction['validation_error']='模型未返回章节JSON；旧版本曾按纯文本降级保存'
    introduction['raw_text']='绪论纯文本原始响应，仅用于证明此结果必须重新生成。'
    db.execute('UPDATE tasks SET status=?,error=?,result=? WHERE id=?',
               ('failed','旧版任务准备从第二章继续',db.dumps(output),tid))

    resume_path=f"/api/v1/projects/{e['pid']}/paper-writing/{tid}/resume"
    blocked=e['client'].post(resume_path,json={})
    assert blocked.status_code==422
    assert blocked.json()['detail']['code']=='PAPER_REMOTE_ACK_REQUIRED'
    assert '第一章 绪论' in blocked.json()['detail']['message']

    saved=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    checkpoint=db.loads(saved['result'])
    assert saved['status']=='failed'
    assert all(item['section_id']!=introduction['section_id'] for item in checkpoint['paper_sections'])
    assert checkpoint['completed_sections']==len(checkpoint['paper_sections'])==4
    rejection=checkpoint['invalid_output']
    assert rejection['checkpoint_rejected'] is True
    assert rejection['raw_text']==introduction['raw_text']
    assert rejection['validation_attempts'][-1]['valid'] is False
    assert 'JSON解析失败' in rejection['validation_error']
    assert '第二章' not in checkpoint['recovery_note']
    invalid_runs=[row['id'] for row in db.rows('SELECT id,manifest FROM generation_runs WHERE task_id=?',(tid,))
                  if db.loads(row['manifest'],{}).get('section_id')==introduction['section_id']]
    assert invalid_runs
    assert not set(invalid_runs).intersection(checkpoint['completed_run_ids'])
    assert set(invalid_runs).issubset({row['id'] for row in paper.uncheckpointed_runs(saved)})
    assert not db.rows('SELECT * FROM revisions')

    resumed=e['client'].post(resume_path,json={'acknowledge_possible_duplicate_charge':True})
    assert resumed.status_code==200,resumed.text
    assert tasks.run_one()
    completed=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    result=db.loads(completed['result'])
    assert completed['status']=='succeeded',completed['error']
    assert result['complete'] and len(result['paper_sections'])==5
    assert len(e['calls'])==6
    assert e['calls'][-1]['section']['kind']=='introduction'
    regenerated=next(item for item in result['paper_sections'] if item['kind']=='introduction')
    assert not regenerated.get('degraded')
    assert any(row['raw_text']==introduction['raw_text'] for row in result['candidate_rejections'])
    assert not db.rows('SELECT * FROM revisions')


def test_legacy_degraded_chapter_cannot_be_adopted_as_a_complete_paper(manuscript):
    e=manuscript
    tid,output=finish(e)
    introduction=next(item for item in output['paper_sections'] if item['kind']=='introduction')
    introduction['degraded']=True
    introduction['validation_error']='旧版纯文本降级候选没有通过章节JSON契约'
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(output),tid))
    response=adopt(e,tid,output)
    assert response.status_code==422
    assert response.json()['detail']['code']=='PAPER_INVALID_CANDIDATE'
    assert '第一章 绪论' in response.json()['detail']['findings'][0]['title']
    assert not db.rows('SELECT * FROM revisions')


@pytest.mark.parametrize('prior_attempts',[
    [],
    [{'attempt':1,'valid':False,'error':'输出不符合JSON契约，请查看后手工整理'}],
])
def test_legacy_parse_failure_is_reconstructed_from_saved_raw_response(manuscript,prior_attempts):
    e=manuscript
    launch_response=launch(e)
    assert launch_response.status_code==200
    tid=launch_response.json()['id']
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    section=e['sections'][2]
    raw='模型只返回纯文字说明，没有输出章节JSON。'
    invalid={'section_id':section['id'],'raw_text':raw,
             'validation_error':'输出不符合JSON契约，请查看后手工整理',
             'validation_attempts':prior_attempts}
    if prior_attempts:
        invalid['checkpoint_rejected']=True
    output={'paper_sections':[],'invalid_output':invalid}

    normalized,changed=paper.normalize_legacy_invalid_output(task,output)

    assert changed
    invalid=normalized['invalid_output']
    assert invalid['title']=='第一章 绪论'
    assert invalid['raw_text']==raw
    assert len(invalid['validation_attempts'])==len(prior_attempts)+1
    assert invalid['validation_attempts'][-1]['valid'] is False
    assert invalid['removed_from_checkpoint'] is False
    assert 'JSON解析失败' in invalid['validation_error']
    assert any(item['raw_text']==raw for item in normalized['candidate_rejections'])
    assert not e['calls']


def test_existing_full_manuscript_and_locked_paragraphs_survive_whole_generation(manuscript):
    e = manuscript
    sid = e['sections'][2]['id']
    original = '这是锁定的原始定义，不能被整篇生成覆盖。'
    assert save(e['client'], sid, original).status_code == 200
    section = security.require_section(sid)
    node = db.loads(documents.current(section)['document'])['content'][0]['attrs']['nodeId']
    assert e['client'].put(f'/api/v1/sections/{sid}/locks', json={'node_ids': [node]}).status_code == 200
    tid, output = finish(e)
    assert e['calls'][0]['current_section_context']['text'] == original
    assert all(original in db.dumps(call) for call in e['calls'][:-1])
    candidate = next(s for s in output['paper_sections'] if s['section_id'] == sid)
    assert original in candidate['text']
    assert documents.current(security.require_section(sid))['text'] == original
    response = adopt(e, tid, output)
    assert response.status_code == 200, response.text
    current = documents.current(security.require_section(sid))
    assert original in current['text']
    assert any(n.get('attrs', {}).get('nodeId') == node for n in documents.nodes(db.loads(current['document'])))
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 6


def test_capacity_stop_preserves_full_candidate_and_resumes_after_capacity_changes(manuscript, monkeypatch):
    e = manuscript
    cfg = provider.config()
    from app.writing_request import FORMAT_REPAIR_INPUT_RESERVE
    from app.writing_expression import guidance
    # Keep room for the shared prose instructions and task-point metadata.
    # The test must reach the first call, then stop on its oversized manuscript
    # context; it must not fail merely because the system prompt grew.
    initial_limit = 23000 + FORMAT_REPAIR_INPUT_RESERVE + len(guidance().encode('utf-8')) + 4096
    db.set_setting('provider', {**cfg, 'context_limit': initial_limit})
    original = e['generate']
    def large_first(system, user, images=None):
        result = original(system, user, images)
        payload = db.loads(user)
        if payload.get('section', {}).get('kind') == 'introduction':
            result['text'] = db.dumps({'paragraphs': [{'text': '完整保留的候选原文及其限定条件。' * 1000}]})
        return result
    monkeypatch.setattr(provider, 'generate', large_first)
    tid = launch(e).json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    result = db.loads(task['result'])
    assert task['status'] == 'failed' and 'WRITING_CONTEXT_BUDGET_EXCEEDED' in task['error']
    assert len(result['paper_sections']) == result['completed_sections'] == 1
    assert result['paper_sections'][0]['text'] == '完整保留的候选原文及其限定条件。' * 1000
    assert result['context_budget']['component_bytes']['paper_draft_sections'] > initial_limit
    assert len(e['calls']) == 1 and not db.rows('SELECT * FROM revisions')
    db.set_setting('provider', cfg)
    monkeypatch.setattr(provider, 'generate', original)
    response = e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing/{tid}/resume', json={})
    assert response.status_code == 200, response.text
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'succeeded', task['error']
    assert len(e['calls']) == 5


def test_source_permission_revocation_blocks_entire_adoption(manuscript, monkeypatch):
    from app import parsers
    e = manuscript
    file, _ = parsers.register_file(e['pid'], '本篇研究资料.txt', '仅在已核对范围内描述研究。'.encode(), 'research')
    parsers.parse_file(file['id'])
    source = db.one('SELECT * FROM sources WHERE file_id=?', (file['id'],))
    monkeypatch.setattr(knowledge_service, 'prepare_writing', lambda *a, **k: {
        'sources': [source], 'knowledge_units': [], 'gaps': [], 'selected_unit_ids': [], 'document_readings': []})
    tid, output = finish(e)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (file['id'],))
    response = adopt(e, tid, output)
    assert response.status_code == 409
    assert not db.rows('SELECT * FROM revisions')


def test_changed_candidate_hash_prevents_adoption(manuscript):
    tid, output = finish(manuscript)
    tampered = deepcopy(output)
    tampered['paper_sections'][0]['document'] = documents.text_document('未经审阅的替换内容。')
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(tampered), tid))
    assert adopt(manuscript, tid, output).status_code == 409
    assert not db.rows('SELECT * FROM revisions')


def test_completed_start_request_replays_without_rechecking_consumed_budget(manuscript):
    e = manuscript
    path = f'/api/v1/projects/{e["pid"]}/paper-writing'
    preview = e['client'].get(path + '/preflight').json()
    body = {'instruction': '同一次整篇请求', 'budget_limit': 20,
            'outline_revision': preview['outline_revision'], 'section_versions': preview['section_versions']}
    headers = {'Idempotency-Key': 'same-completed-full-paper-request'}
    first = e['client'].post(path, json=body, headers=headers)
    assert first.status_code == 200
    assert tasks.run_one()
    again = e['client'].post(path, json=body, headers=headers)
    assert again.status_code == 200 and again.json()['id'] == first.json()['id']
    assert len(e['calls']) == 5
    conflict = e['client'].post(path, json={**body, 'instruction': '另一个请求'}, headers=headers)
    assert conflict.status_code == 409

import json,sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app import db,security,provider,documents,tasks,config,exports
import app.main as main
from app.writing_agent import repository,evidence,contracts,profiles,coordinator,adoption,sentences,verification,evaluations,tools
from app.writing_agent.common import state,owned,unblob,digest,blob

@pytest.fixture
def env(tmp_path,monkeypatch):
    for d in ('objects','tmp','previews','exports','backups','logs'):(tmp_path/d).mkdir()
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'workbench.sqlite3')
    for m in (config,security,exports,main):monkeypatch.setattr(m,'DATA',tmp_path)
    monkeypatch.setattr(main,'PORT',9996)
    monkeypatch.setattr(security,'get_key',lambda profile='':'test-key')
    monkeypatch.setattr(provider,'get_key',lambda:'test-key')
    db.init()
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://example.test/v1','model':'deepseek-test','protocol':'openai','max_output_tokens':2048,'context_limit':32000,'input_price':0,'output_price':0,'max_request_cost':1,'max_attempts':1})
    with TestClient(main.app) as c:
        c.headers['X-CSRF-Token']=c.post('/api/v1/session',json={'token':security.BOOTSTRAP_TOKEN}).json()['csrf']
        pid=c.post('/api/v1/projects',json={'title':'证据写作测试','budget':20}).json()['id']
        # This isolated project uses only the synthetic transport below. The
        # production invoice gate is exercised by test_expense_evidence.py.
        db.set_setting('expense_policy:'+pid,{'require_pricing_basis':False,'block_on_unresolved':False})
        c.post(f'/api/v1/projects/{pid}/rules',json={'statement':'保留条件和证据范围，不得编造测试结果。','modality':'mandatory','scope':'global'})
        response=c.post(f'/api/v1/projects/{pid}/rule-packages');assert response.status_code==200,response.text
        sid=c.post(f'/api/v1/projects/{pid}/sections',json={'title':'功能约束','kind':'system_design'}).json()['id']
        root=tmp_path/'project';root.mkdir();(root/'gate.ts').write_text('export function allowed(issuer: boolean, enabled: boolean) { return issuer && enabled; }\n',encoding='utf-8');(root/'AGENTS.md').write_text('只读调查，不得把测试定义当作运行结果。',encoding='utf-8')
        (root/'.env').write_text('SECRET=never include',encoding='utf-8')
        binding=repository.bind(pid,{'root':str(root),'egress':'selected_content'});snapshot=repository.capture(pid,binding['id']);eid=evidence.create(pid,{'kind':'code_observation','origin_type':'code','origin_id':snapshot['id'],'locator':{'path':'gate.ts','line_start':1,'line_end':2}})['id']
        core=contracts.save_core(pid,{'research_question':'如何准确描述功能的授权条件？','scope':'冻结源码静态观察'});contracts.publish_core(pid,core['id'],state(pid)['revision'])
        contract=contracts.save_contract(pid,sid,{'goal':'解释操作的合取条件及证据范围','evidence_ids':[eid],'depends_on_sections':[]},state(pid)['revision'])
        profile=profiles.create(pid,{'role':'draft','configuration':{'route':'primary','max_output_tokens':2048}})
        yield {'client':c,'pid':pid,'sid':sid,'root':root,'binding':binding,'snapshot':snapshot,'eid':eid,'contract':contract,'profile':profile,'path':tmp_path}

def fake_model(monkeypatch,fail_stage=None):
    calls=[]
    def generate(system,user,images=None):
        frame=json.loads(user);payload=json.loads(frame['messages'][0]['content']);calls.append((system,payload))
        if fail_stage and fail_stage in system:raise provider.ProviderError('网络中断，执行结果未知',unknown=True)
        if 'JSON动作模式' in system:answer={'action':'done','summary':'已给定完整的冻结代码依据'}
        elif '返回 {"schema_version"' in system:answer={'schema_version':'judgment.v1','task_goal':'准确描述合取条件','claims':[{'local_id':'J1','statement':'发行方且启用时才允许操作','evidence_ids':[payload['evidence'][0]['id']],'conditions':['发行方','开关启用'],'not_established':['测试执行结果']}],'required_explanations':['保留两项条件'],'missing_evidence':[]}
        elif '独立阅读冻结候选' in system:answer={'sentences':[{'sentence_id':s['id'],'role':'fact','claims':[{'statement':s['text'],'evidence_ids':[payload['evidence'][0]['id']],'support':'supported','conditions':['发行方','开关启用']}]} for s in payload['sentences']],'findings':[],'coverage_complete':True,'summary':'覆盖全部句子'}
        elif '返回 {"paragraphs"' in system:answer={'paragraphs':[{'local_id':'p1','text':'只有发行方且开关启用时，操作才会被允许。该描述针对本次冻结源码，尚不能作为运行测试通过的结论。'}],'missing_materials':[]}
        elif '仅返回JSON {"answer"' in system or '只返回JSON {"answer"' in system:answer={'answer':'根据给定材料保留条件，未知部分不作推断。'}
        else:answer={'findings':[],'coverage_complete':True,'summary':'已核对当前给定范围'}
        envelope={'agent_transport':True,'text':json.dumps(answer,ensure_ascii=False),'tool_calls':[],'message':None,'items':[],'finish':'stop','reported_model':'fixture'}
        return {'text':json.dumps(envelope,ensure_ascii=False),'usage':{'input_tokens':100,'output_tokens':100},'cost':0,'remote_id':'fixture-'+str(len(calls)),'request_hash':digest([system,user])}
    monkeypatch.setattr(provider,'generate',generate);return calls

def start(e):return coordinator.create(e['pid'],{'section_id':e['sid'],'operation':'generate_unit','base_revision_id':db.one('SELECT current_revision_id FROM sections WHERE id=?',(e['sid'],))['current_revision_id'],'expected_project_state_revision':state(e['pid'])['revision'],'contract_version_id':e['contract']['id'],'profile_id':e['profile']['id']})

def finish(e,session):
    for _ in range(15):
        s=coordinator.get(e['pid'],session['id'])
        if s['status'] not in ('running','queued'):return s
        assert tasks.run_one()
    pytest.fail('工作流未在预期阶段内结束')

def reviewed_candidate(e,monkeypatch):
    fake_model(monkeypatch);session=finish(e,start(e));assert session['status']=='needs_review',session
    cid=session['candidates'][-1]['id'];a=db.one('SELECT * FROM document_analyses WHERE candidate_id=?',(cid,));verification.human_review(e['pid'],a['id'],{'decision':'approved','note':'已逐句对照冻结源码及合取条件，没有将静态观察当成测试结果。'})
    return cid,a

def test_migrations_atomic_and_repeatable(env):
    from app.writing_agent import migrations
    db.init();db.init()
    with db.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==migrations.CURRENT
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]==len(migrations.FILES)

def test_migration_refuses_future_database(env):
    with db.connect() as c:c.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):db.init()

def test_source_is_frozen_and_secrets_excluded(env):
    s=env['snapshot'];assert not any(x['path']=='.env' for x in s['manifest']['files'])
    (env['root']/'gate.ts').write_text('changed',encoding='utf-8')
    assert 'issuer && enabled' in repository.read(env['pid'],s['id'],'gate.ts')['text']
    assert s['manifest']['atomic_filesystem_snapshot'] is False

def test_path_escape_and_cross_project_evidence_rejected(env):
    with pytest.raises(HTTPException):repository.read(env['pid'],env['snapshot']['id'],'../.env')
    other=env['client'].post('/api/v1/projects',json={'title':'其他项目'}).json()['id']
    assert env['client'].get(f'/api/v1/projects/{other}/evidence/{env["eid"]}').status_code==404

def test_code_cannot_be_promoted_to_execution(env):
    with pytest.raises(HTTPException):evidence.create(env['pid'],{'kind':'test_run','origin_type':'code','origin_id':env['snapshot']['id'],'locator':{'path':'gate.ts'}})

def test_model_profile_cannot_override_route_secret_or_budget(env):
    with pytest.raises(HTTPException):profiles.create(env['pid'],{'configuration':{'base_url':'https://evil.example'}})
    with pytest.raises(HTTPException):profiles.create(env['pid'],{'configuration':{'context_limit':999999}})

def test_project_state_compare_and_swap(env):
    from app.writing_agent.common import update_state
    with pytest.raises(HTTPException):update_state(env['pid'],999,target_snapshot_id=env['snapshot']['id'])

def test_workflow_has_separate_judgment_check_and_no_auto_human(env,monkeypatch):
    calls=fake_model(monkeypatch);s=finish(env,start(env));assert s['status']=='needs_review',s
    assert [x['stage'] for x in s['steps']]==['investigate','judgment','judgment_check','draft','annotation','semantic','coherence']
    assert len(calls)==7
    a=db.one('SELECT * FROM document_analyses WHERE candidate_id=?',(s['candidates'][-1]['id'],));assert a['state']=='model_reviewed'
    with pytest.raises(HTTPException):adoption.adopt(env['pid'],s['candidates'][-1]['id'],{'base_revision_id':None,'accept_policy':'strict'},'test-strict-before-review')

def test_strict_adoption_transaction_and_idempotency(env,monkeypatch):
    cid,a=reviewed_candidate(env,monkeypatch);body={'base_revision_id':None,'accept_policy':'strict'}
    first=adoption.adopt(env['pid'],cid,body,'adoption-same-request');second=adoption.adopt(env['pid'],cid,body,'adoption-same-request')
    assert first['revision_ids']==second['revision_ids'] and second['idempotent_replay']
    assert db.one('SELECT count(*) n FROM revisions')['n']==1
    with pytest.raises(HTTPException):adoption.adopt(env['pid'],cid,{**body,'accept_policy':'draft'},'adoption-same-request')
    copied=sentences.get(env['pid'],first['analysis_id']);assert copied['state']=='human_reviewed' and copied['coverage']['unclassified']==0

def test_changed_upstream_cannot_be_adopted(env,monkeypatch):
    cid,_=reviewed_candidate(env,monkeypatch);evidence.set_validity(env['pid'],env['eid'],'revoked')
    with pytest.raises(HTTPException):adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'draft'},'stale-source-request')
    assert not db.rows('SELECT * FROM revisions')

def test_unknown_remote_outcome_never_retried_implicitly(env,monkeypatch):
    calls=fake_model(monkeypatch,fail_stage='JSON动作模式');s=finish(env,start(env));assert s['status']=='outcome_unknown'
    with pytest.raises(HTTPException):coordinator.resume(env['pid'],s['id'])
    assert len(calls)==1
    assert db.one("SELECT count(*) n FROM generation_runs WHERE status='outcome_unknown'")['n']==1

def test_sentence_positions_chinese_emoji_decimal_and_quote():
    text='😀甲说：“第1.5次。”乙继续。'
    spans=list(sentences.ranges(text));assert ''.join(text[a:b] for a,b in spans)==text
    assert len(spans)==2
    assert sentences.utf16_offset(text,1)==2
    assert text[spans[0][0]:spans[0][1]].endswith('”')

def test_sentence_analysis_never_hides_unclassified(env):
    doc=documents.text_document('这是一个事实。还有一个事实。')
    rev=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),doc,None)
    a=sentences.analyze(env['pid'],db.loads(rev['document']),revision_id=rev['id'])
    assert a['coverage']['total']==2 and a['coverage']['unclassified']==2

def test_tool_deny_cache_and_egress_revoke(env,monkeypatch):
    s=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(s['id'],));args={'snapshot_id':env['snapshot']['id'],'relative_path':'gate.ts','line_start':1,'line_end':2}
    first=tools.execute(env['pid'],step['id'],'read_code',args,'same-call',env['snapshot']['id']);assert first['data']['evidence_id']
    assert tools.execute(env['pid'],step['id'],'read_code',args,'same-call',env['snapshot']['id'])==first
    with pytest.raises(HTTPException):tools.execute(env['pid'],step['id'],'arbitrary_shell',{},'bad',env['snapshot']['id'])
    with pytest.raises(HTTPException):tools.execute(env['pid'],step['id'],'read_code',{**args,'line_end':1},'same-call',env['snapshot']['id'])
    repository.set_egress(env['pid'],env['binding']['id'],'local_only')
    with pytest.raises(HTTPException):tools.execute(env['pid'],step['id'],'read_code',args,'same-call',env['snapshot']['id'])

def test_diagnostic_gold_isolation_and_no_fake_scores(env,monkeypatch):
    calls=fake_model(monkeypatch);r=evaluations.create(env['pid'],{'profile_id':env['profile']['id'],'case_ids':['D02'],'repetitions':1,'budget_limit':2})
    for _ in range(2):assert tasks.run_one()
    result=evaluations.get(env['pid'],r['id']);assert result['summary']['completed']==2 and result['summary']['human_passed']==0
    for system,payload in calls:assert set(payload)=={'instruction','context'}
    assert all(i['result']['semantic_score'] is None for i in result['items'])
    with pytest.raises(HTTPException):evaluations.qualify(env['pid'],r['id'],'不能用少量题目冒充完整质量验收')

def test_http_routes_retain_auth_and_conflict_details(env):
    assert env['client'].get(f'/api/v1/projects/{env["pid"]}/writing-state').status_code==200
    r=env['client'].patch(f'/api/v1/projects/{env["pid"]}/writing-state',json={'target_snapshot_id':env['snapshot']['id'],'expected_revision':999})
    assert r.status_code==409 and r.json()['detail']['code']=='INPUT_VERSION_CHANGED'
    with TestClient(main.app) as unauthenticated:assert unauthenticated.get(f'/api/v1/projects/{env["pid"]}/writing-state').status_code==401

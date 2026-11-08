"""Real LangChain/LangGraph, isolated SQLite, synthetic transport only."""
import json
import threading
from contextvars import ContextVar
from pathlib import Path
import pytest
from fastapi import HTTPException
from test_workbench import client, project
from app import db, provider, security, tasks
from app.ai_runtime import policy, receipts, telemetry, gateway, parallel
from app.ai_graphs.chapter import run
from app.ai_contracts.tasks import compile_spec, assess
from app.ai_contracts.evidence import select
from test_writing_flow_integration import writing_project, write
from test_writing_agent import env as agent_env, start as start_agent


@pytest.fixture
def live_task(client):
    pid=project(client)
    task=tasks.enqueue(pid,'support_check',{'instruction':'fixture'})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    return db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))


def test_new_tasks_freeze_runtime_and_historical_tasks_remain_legacy(live_task):
    saved=policy.frozen(live_task)
    policy.save({'new_task_engine':'legacy','new_task_quality':'legacy'})
    assert saved['engine']=='langgraph_v1'
    assert policy.frozen(live_task)==saved
    old={**live_task,'id':'historical-task-without-runtime-metadata','payload':'{}'}
    assert policy.frozen(old)['engine']=='legacy'
    assert policy.freeze('write',{})['ai_runtime']['engine']=='legacy'
    assert policy.freeze('provider_probe',{'question':'exact'})=={'question':'exact'}


@pytest.mark.parametrize('body',[{'new_task_engine':'other'}, {'max_parallel_checks':True},
    {'max_parallel_checks':4},{'telemetry_enabled':'yes'},{'unknown':1}])
def test_invalid_policy_rejected(client,body):
    assert client.put('/api/v1/ai-runtime/policy',json=body).status_code==422


def test_gateway_uses_real_runnable_once_without_uploading_text(live_task,monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    monkeypatch.setenv('LANGSMITH_ENDPOINT','https://invalid.example')
    import requests
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('No external tracing allowed'))
    calls=[]
    secret='private-paper-and-key-marker'
    def execute(**value):
        calls.append(value)
        return {'text':'ok','usage':{'input_tokens':2,'output_tokens':1},'cost':.001}
    result=gateway.invoke(live_task,'system',secret,{'stage':'draft'},None,execute)
    assert result['text']=='ok' and len(calls)==1
    assert calls[0]['user']==secret
    assert secret not in db.dumps(db.rows('SELECT * FROM ai_runtime_events'))
    assert telemetry.summary(live_task['project_id'])['events']==1


def test_gateway_unknown_error_not_retried(live_task):
    calls=[]
    def execute(**value):
        calls.append(1)
        raise provider.ProviderError('already sent',unknown=True)
    with pytest.raises(provider.ProviderError,match='already sent'):
        gateway.invoke(live_task,'s','u',{},None,execute)
    assert len(calls)==1


def test_graph_replays_committed_node_after_checkpoint_failure(live_task,monkeypatch):
    section={'id':'section-fixture'}
    counts={'prepare':0,'finish':0}
    def prepare(state):
        counts['prepare']+=1
        return {**state,'prepared':True}
    def finish(state):
        counts['finish']+=1
        return {**state,'done':True}
    original=receipts.commit
    failed=[]
    def crash(*args):
        original(*args)
        if not failed:
            failed.append(True)
            raise RuntimeError('crash after commit before checkpoint')
    monkeypatch.setattr(receipts,'commit',crash)
    stages=[('prepare',prepare),('finish',finish)]
    with pytest.raises(RuntimeError,match='crash after commit'):
        run(live_task,section,{'input':'same'},stages,lambda value:None)
    db.execute('UPDATE tasks SET attempt=2 WHERE id=?',(live_task['id'],))
    resumed=db.one('SELECT * FROM tasks WHERE id=?',(live_task['id'],))
    result=run(resumed,section,{'input':'same'},stages,lambda value:None)
    assert result['done'] and counts=={'prepare':1,'finish':1}
    assert len(db.rows('SELECT * FROM ai_node_receipts'))==2
    assert Path(db.DB_PATH).with_name('ai-graph-checkpoints.sqlite3').is_file()


def test_graph_changed_input_does_not_reexecute(live_task):
    calls=[]
    stages=[('draft',lambda state:(calls.append(1) or state))]
    run(live_task,{'id':'s'},{'instruction':'first'},stages,lambda value:None)
    with pytest.raises(HTTPException) as error:
        run(live_task,{'id':'s'},{'instruction':'changed'},stages,lambda value:None)
    assert error.value.status_code==409 and len(calls)==1


def test_graph_unresolved_request_blocks_new_action(live_task):
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
               ('unknown-run',live_task['id'],live_task['project_id'],'outcome_unknown','fixture','openai','{}',db.now()))
    with pytest.raises(provider.ProviderError) as error:
        run(live_task,{'id':'s'},{},[('draft',lambda value:pytest.fail('must not send'))],lambda value:None)
    assert error.value.unknown and not db.rows('SELECT * FROM ai_node_receipts')


def test_old_owner_cannot_commit_or_replay(live_task):
    db.execute('UPDATE tasks SET attempt=2 WHERE id=?',(live_task['id'],))
    with pytest.raises(InterruptedError):
        receipts.commit(live_task,'run','draft','hash',{'x':1},lambda value:None)
    assert not db.rows('SELECT * FROM ai_node_receipts')


def test_receipt_validation_runs_again_after_permission_change(live_task):
    permitted=[True]
    def validate(value):
        if not permitted[0]:raise HTTPException(403,'revoked')
    receipts.commit(live_task,'run','draft','hash',{'evidence':'source'},validate)
    permitted[0]=False
    with pytest.raises(HTTPException) as error:
        receipts.load(live_task,'run','draft','hash',validate)
    assert error.value.status_code==403


def test_receipt_hash_mismatch_blocks_replay(live_task):
    receipts.commit(live_task,'run','draft','hash',{'x':1},lambda value:None)
    db.execute("UPDATE ai_node_receipts SET result_hash='tampered'")
    with pytest.raises(HTTPException):
        receipts.load(live_task,'run','draft','hash',lambda value:None)


def test_independent_checks_propagate_owner_but_not_sql_connection(live_task):
    payload=db.loads(live_task['payload']);payload['ai_runtime']=dict(policy.frozen(live_task));payload['ai_runtime']['max_parallel_checks']=2
    task={**live_task,'payload':payload}
    marker=ContextVar('test_ai_owner');marker.set('owner')
    barrier=threading.Barrier(2)
    with db.read_session():
        parent=db._READ_SESSION.get()[1]
        def check():
            barrier.wait(timeout=10)
            assert marker.get()=='owner'
            assert db._READ_SESSION.get()[1] is not parent
            return db.one('SELECT id FROM tasks WHERE id=?',(task['id'],))['id']
        result=parallel.run(task,{'semantic':check,'discourse':check})
    assert result=={'semantic':task['id'],'discourse':task['id']}


def test_parallel_checks_do_not_split_shared_attempt_budget(live_task):
    payload=db.loads(live_task['payload']);payload['ai_runtime']=dict(policy.frozen(live_task));payload['ai_runtime']['max_parallel_checks']=3
    task={**live_task,'payload':payload}
    thread=threading.get_ident()
    with provider.attempt_budget(2):
        result=parallel.run(task,{'one':threading.get_ident,'two':threading.get_ident})
    assert set(result.values())=={thread}


def test_parallel_schema_error_cannot_hide_an_unknown_remote_outcome(live_task):
    payload=db.loads(live_task['payload']);payload['ai_runtime']=dict(policy.frozen(live_task));payload['ai_runtime']['max_parallel_checks']=2
    task={**live_task,'payload':payload};barrier=threading.Barrier(2)
    def schema_error():
        barrier.wait(timeout=10)
        raise ValueError('known response schema error')
    def unknown():
        barrier.wait(timeout=10)
        raise provider.ProviderError('already sent but unknown',unknown=True)
    with pytest.raises(provider.ProviderError) as error:
        parallel.run(task,{'semantic':schema_error,'discourse':unknown})
    assert error.value.unknown


def test_taskspec_uses_frozen_obligations_not_draft_claim_count():
    section={'id':'s','current_revision_id':None}
    context={'writing_brief':{'points':[{'id':'p1','text':'实现机制','required':True},
                                      {'id':'p2','text':'实验限制','required':True}]}}
    spec=compile_spec('p',section,context,{'rule_package_id':'rules'})
    quality={'mapping':{'status':'checked'},'semantic':{'coverage_complete':True,
        'claims':[{'claim_id':'c1','paragraph_index':0,'support':'supported','quote_verified':True}], 'point_reviews':[
        {'point_id':'p1','status':'complete','paragraph_indices':[0]}]}}
    result=assess(spec,quality,[{'text':'one paragraph'}])
    assert result['required_count']==2 and result['verified_count']==1 and not result['complete']


def test_taskspec_binds_versions_without_repeating_raw_locator_metadata():
    section={'id':'s','current_revision_id':None}
    context={'writing_brief':{'points':[{'id':'p1','text':'解释实际范围','required':True}]}}
    marker='local-only OCR diagnostic marker'
    manifest={'resource_refs':[{'type':'source','id':'source','version':'v1',
                'locator':{'recognition':[{'text':marker*1000}]}}]}
    first=compile_spec('p',section,context,manifest)
    assert marker not in json.dumps(first)
    manifest['resource_refs'][0]['version']='v2'
    second=compile_spec('p',section,context,manifest)
    assert first['hash']!=second['hash']


def test_evidence_selection_prioritizes_required_type_without_claiming_support():
    candidates=[{'id':'literature','type':'source','file_id':'f','text':'模块授权流程 模块授权流程'},
                {'id':'code','type':'evidence','kind':'code_observation','text':'模块授权实现'},
                {'id':'other','type':'source','file_id':'other','text':'模块授权条件'}]
    result=select('模块授权流程',candidates,['code'],limit=2)
    assert result[0]['id']=='code'
    assert all(item['support']=='not_checked' for item in result)


def test_runtime_api_scopes_and_inventory(client,live_task):
    result=client.get('/api/v1/ai-runtime/inventory')
    assert result.status_code==200 and len(result.json()['entries'])==7
    response=client.get(f'/api/v1/ai-runtime/projects/{live_task["project_id"]}')
    assert response.status_code==200 and response.json()['completed_nodes']==0
    other=project(client)
    assert client.get(f'/api/v1/ai-runtime/projects/{other}?task_id={live_task["id"]}').status_code==404


def test_comparison_is_read_only_and_has_no_invented_semantic_score(client,live_task):
    second=tasks.enqueue(live_task['project_id'],'support_check',{'instruction':'fixture',
        'ai_runtime':{'engine':'legacy','quality':'legacy'}})
    result=client.post(f'/api/v1/ai-runtime/projects/{live_task["project_id"]}/compare',
                       json={'task_ids':[live_task['id'],second['id']]})
    assert result.status_code==200,result.text
    report=result.json()
    assert report['new_model_requests']==0 and not report['semantic_improvement_verified']
    assert {row['group'] for row in report['tasks']}=={'A','D'}
    assert all(row['human_semantic_rating'] is None for row in report['tasks'])
    assert not any(row['ready_for_human_comparison'] for row in report['paired_input_groups'])


def test_runtime_migration_is_repeatable(live_task):
    db.init();db.init()
    assert db.one('PRAGMA user_version')['user_version']==19
    assert db.one("SELECT checksum FROM schema_migrations WHERE number=19")


def test_actual_chapter_parallel_checks_and_obligation_views(writing_project):
    policy.save({'max_parallel_checks':2})
    task,result=write(writing_project)
    assert policy.frozen(task)['max_parallel_checks']==2
    assert result['manifest']['task_spec_version']=='task.spec.v1'
    assert result['manifest']['task_spec']['obligations']
    draft_payload=next(payload for _,payload in writing_project['sent'] if 'writing_brief' in payload)
    assert 'task_spec' not in draft_payload and '_ai_quality_version' not in draft_payload
    assert result['writing_quality']['obligations']['required_count']>0
    support=result['writing_quality']['claim_support_view']['items']
    assert support and any(item['relation']=='supports' for item in support)
    assert not any(item['human_approval'] for item in support)
    assert len(db.rows('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],)))==3
    actions={row['action_id'] for row in db.rows('SELECT * FROM ai_node_receipts WHERE task_id=?',(task['id'],))}
    assert {'understand','draft','review','source_and_numeric_checks','claim_support_review','discourse_review','originality_review'}<=actions


def test_actual_chapter_replay_keeps_paid_request_count(writing_project,monkeypatch):
    from app.ai_graphs import chapter
    from copy import deepcopy
    inputs=[];original_digest=chapter.digest
    def capture(value):
        if isinstance(value,dict) and 'context' in value:inputs.append(deepcopy(value))
        return original_digest(value)
    monkeypatch.setattr(chapter,'digest',capture)
    task,result=write(writing_project)
    before=len(writing_project['sent'])
    db.execute("UPDATE tasks SET status='queued' WHERE id=?",(task['id'],))
    tasks.run_one(task['id'])
    current=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    def differences(left,right,prefix=''):
        if isinstance(left,dict) and isinstance(right,dict):
            return [row for key in sorted(set(left)|set(right)) for row in differences(left.get(key),right.get(key),prefix+'.'+key)]
        if left!=right:return [(prefix,str(left)[:350],str(right)[:350])]
        return []
    assert current['status']=='succeeded',(current['error'],differences(inputs[0],inputs[-1])[:20])
    assert len(writing_project['sent'])==before
    assert db.loads(current['result'])['text']==result['text']


@pytest.mark.parametrize('operation',['generate_unit','revise_unit'])
@pytest.mark.parametrize('stage',['investigate','draft','coherence','regression'])
def test_agent_graph_routes_match_legacy_contract(live_task,operation,stage):
    from app.ai_graphs.agent import route,decision
    assert route(live_task,stage,{},operation)==decision(stage,{},operation)


def test_backup_retains_graph_receipts_and_checkpoint_snapshot(live_task):
    from app import exports
    import zipfile
    run(live_task,{'id':'s'},{'data':'fixture'},[('draft',lambda value:value)],lambda value:None)
    result=exports.backup()
    with zipfile.ZipFile(exports.DATA/'backups'/result['filename']) as archive:
        assert 'ai-graph-checkpoints.sqlite3' in archive.namelist()
        manifest=json.loads(archive.read('manifest.json'))
        assert manifest['ai_graph_recovery']=='business_receipts_authoritative_checkpoints_optional'
    restored=exports.restore_backup((exports.DATA/'backups'/result['filename']).read_bytes())
    import sqlite3
    with sqlite3.connect(Path(restored['restored_data_directory'])/'workbench.sqlite3') as connection:
        assert connection.execute('SELECT COUNT(*) FROM ai_node_receipts').fetchone()[0]==1


def test_node_identity_preserves_measured_fields_and_source_hash():
    from app.ai_graphs.chapter import node_input
    value={'context':{'facts':[{'payload':{'duration_ms':12.5,'hash':'raw-source-hash'}}]},
           'manifest':{'stable_block_cache_hit':False,'evidence_bundle_hash':'ephemeral',
                       'evidence_versions':{'e1':'actual-version'}},
           'evidence_bundle':{'bundle_id':'navigation','hash':'bundlehash','source_manifest':{'version':'v1'}}}
    normalized=node_input(value)
    assert normalized['context']['facts'][0]['payload']=={'duration_ms':12.5,'hash':'raw-source-hash'}
    assert normalized['manifest']['evidence_versions']=={'e1':'actual-version'}
    assert 'stable_block_cache_hit' not in normalized['manifest']
    assert value['manifest']['stable_block_cache_hit'] is False


@pytest.mark.parametrize('engine',['legacy','langgraph_v1'])
def test_framework_only_comparison_preserves_model_calls(writing_project,engine):
    policy.save({'new_task_engine':engine,'new_task_quality':'legacy'})
    task,result=write(writing_project)
    assert len(writing_project['sent'])==3
    assert result['writing_quality']['semantic']['coverage_complete']
    assert 'task_spec_version' not in result['manifest']


def test_synchronous_writer_claims_a_real_lease_and_rejects_duplicate_owner(writing_project):
    e=writing_project
    task=tasks.enqueue(e['pid'],'write',{'section_id':e['sid'],'base_revision_id':None,'operation':'generate'})
    result=tasks.writing(task,db.loads(task['payload']))
    stored=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert result['document'] and stored['status']=='succeeded' and stored['attempt']==1
    before=len(e['sent'])
    with pytest.raises(InterruptedError):tasks.writing(task,db.loads(task['payload']))
    assert len(e['sent'])==before


def test_internal_children_inherit_frozen_parent_not_new_defaults(live_task):
    from app.task_transitions import ownership
    policy.save({'new_task_engine':'legacy','new_task_quality':'legacy','max_parallel_checks':3})
    with ownership(live_task):
        child=tasks.enqueue(live_task['project_id'],'studio',{'action':'fixture'})
    assert policy.frozen(child)==policy.frozen(live_task)
    assert db.loads(child['payload'])=={'action':'fixture'}
    fresh=tasks.enqueue(live_task['project_id'],'studio',{'action':'new_fixture'})
    assert policy.frozen(fresh)['engine']=='legacy'


def test_agent_session_children_keep_first_step_protocol(agent_env):
    from app.writing_agent import coordinator
    session=start_agent(agent_env)
    first=db.one('SELECT t.* FROM tasks t JOIN writing_steps st ON st.task_id=t.id WHERE st.session_id=?',(session['id'],))
    policy.save({'new_task_engine':'legacy','new_task_quality':'legacy','max_parallel_checks':3})
    step=coordinator.enqueue(session['id'],'judgment')
    child=db.one('SELECT * FROM tasks WHERE id=?',(step['task_id'],))
    assert policy.frozen(child)==policy.frozen(first)
    assert db.loads(child['payload'])['prompt_layout_version']==db.loads(first['payload'])['prompt_layout_version']


def test_historical_agent_session_does_not_upgrade_next_step(agent_env):
    from app.writing_agent import coordinator
    session=start_agent(agent_env)
    first=db.one('SELECT t.* FROM tasks t JOIN writing_steps st ON st.task_id=t.id WHERE st.session_id=?',(session['id'],))
    payload=db.loads(first['payload']);old={'step_id':payload['step_id']}
    db.execute('UPDATE tasks SET payload=? WHERE id=?',(db.dumps(old),first['id']))
    db.execute('DELETE FROM ai_task_protocols WHERE task_id=?',(first['id'],))
    child_step=coordinator.enqueue(session['id'],'judgment')
    child=db.one('SELECT * FROM tasks WHERE id=?',(child_step['task_id'],))
    assert policy.frozen(child)['engine']=='legacy'
    assert db.loads(child['payload'])['prompt_layout_version']=='legacy'

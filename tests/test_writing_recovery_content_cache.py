"""Complete returned inputs survive recovery endpoint/retry changes safely."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client,project,section,file,manual_package  # noqa: F401
from app import db,provider,security,tasks,writing_flow
from app.writing_agent.common import digest


def setup(client):
    pid=project(client);sid=section(client,pid);manual_package(client,pid)
    original=file(client,pid,'原文限定：模块职责明确有助于维护，但未测量吞吐率。')
    source=db.one('SELECT * FROM sources WHERE file_id=? LIMIT 1',(original['id'],))
    security.save_key('synthetic-content-cache-key')
    cfg={**provider.DEFAULT,'base_url':'https://old.example/v1','model':'fixture-model',
         'context_limit':32000,'max_output_tokens':512,'max_attempts':3,
         'input_price':0.,'output_price':0.}
    db.set_setting('provider',cfg)
    task=tasks.enqueue(pid,'provider_probe',{})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    context={'sources':[source],'question':'核对模块职责，不扩大结论。'}
    manifest={'section_id':sid,'rule_package_id':security.require_project(pid)['rule_package_id'],
              'base_revision_id':None,'file_ids':[original['id']],'source_ids':[source['id']],
              'request_options':{'max_output_tokens':512}}
    return task,context,manifest,cfg


def legacy_run(task,context,manifest,cfg,*,system='verify exact inputs',status='completed',
               merged_options=None,wire_proof=False):
    meta={**deepcopy(manifest),'provider':deepcopy(cfg),'writing_stage':'semantic_review',
          'writing_unit_index':8,'writing_flow_request_hash':digest([
              writing_flow.VERSION,system,writing_flow.stable_input(context),
              manifest.get('rule_package_id'),cfg,'semantic_review',8,manifest.get('request_options')])}
    if merged_options is not None:meta['request_options']=merged_options
    if wire_proof:
        import hashlib
        meta['system_prompt_blob']=security.put_blob(system.encode())
        meta['outgoing_context_hash']=hashlib.sha256((system+'\n'+db.dumps(context)).encode()).hexdigest()
    if status in ('completed','completed_invalid'):
        meta['output_blob']=security.put_blob(b'{"claim_reviews":[],"summary":"saved complete response"}')
    identity=db.uid()
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (identity,task['id'],task['project_id'],status,cfg['model'],cfg['protocol'],db.dumps(meta),db.now()))
    return identity


def fresh_transport(monkeypatch):
    calls=[]
    def send(task,system,user,manifest,images=None):
        calls.append({'system':system,'context':db.loads(user),'manifest':manifest})
        return {'text':'fresh complete response','run_id':'fresh-response'}
    monkeypatch.setattr(tasks,'model_call',send)
    return calls


@pytest.mark.parametrize('change',['attempts','endpoint'])
def test_legacy_complete_request_is_reused_after_transport_change(client,monkeypatch,change):
    task,context,manifest,cfg=setup(client)
    identity=legacy_run(task,context,manifest,cfg)
    changed={**cfg,'max_attempts':5}
    if change=='endpoint':changed.update(base_url='https://new.example/v1',model='other-model')
    db.set_setting('provider',changed)
    calls=fresh_transport(monkeypatch)
    result=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert result['cache_hit'] and result['run_id']==identity and not calls


def test_legacy_merged_options_reconstruct_original_stage_options(client,monkeypatch):
    task,context,manifest,cfg=setup(client)
    identity=legacy_run(task,context,manifest,cfg,merged_options={
        'protocol':'openai','streaming':True,'json_mode':True,'max_output_tokens':512})
    db.set_setting('provider',{**cfg,'base_url':'https://new.example/v1','max_output_tokens':256})
    incoming={**manifest,'request_options':{'max_output_tokens':256}}
    calls=fresh_transport(monkeypatch)
    result=writing_flow._cached_call(task,'verify exact inputs',context,incoming,stage='semantic_review',index=8)
    assert result['cache_hit'] and result['run_id']==identity and not calls


def test_exact_wire_input_proves_legacy_request_after_stage_config_overlay(client,monkeypatch):
    task,context,manifest,cfg=setup(client)
    identity=legacy_run(task,context,manifest,cfg,wire_proof=True)
    row=db.one('SELECT manifest FROM generation_runs WHERE id=?',(identity,))
    meta=db.loads(row['manifest']);meta['provider']['max_output_tokens']=256
    db.execute('UPDATE generation_runs SET manifest=? WHERE id=?',(db.dumps(meta),identity))
    db.set_setting('provider',{**cfg,'base_url':'https://new.example/v1','max_attempts':5})
    calls=fresh_transport(monkeypatch)
    result=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert result['cache_hit'] and not calls


@pytest.mark.parametrize('change',['system','context','source','rule','source_manifest','section','index'])
def test_changed_request_inputs_do_not_reuse_legacy_response(client,monkeypatch,change):
    task,context,manifest,cfg=setup(client)
    legacy_run(task,context,manifest,cfg,wire_proof=True)
    system='verify exact inputs';index=8
    if change=='system':system+=' with a new instruction'
    if change=='context':context['question']='核对不同问题。'
    if change=='source':context['sources'][0]['text']='原文已变更，旧响应不可复用。'
    if change=='rule':manifest['rule_package_id']='changed-rule-package'
    if change=='source_manifest':manifest['source_ids']=[]
    if change=='section':manifest['section_id']='different-section'
    if change=='index':index=16
    calls=fresh_transport(monkeypatch)
    result=writing_flow._cached_call(task,system,context,manifest,stage='semantic_review',index=index)
    assert result['run_id']=='fresh-response' and len(calls)==1
    assert calls[0]['manifest']['writing_flow_content_hash']


@pytest.mark.parametrize('status',['pending','sent','outcome_unknown','completed_invalid'])
def test_unacknowledged_unknown_or_partial_request_cannot_replay_on_new_endpoint(client,monkeypatch,status):
    task,context,manifest,cfg=setup(client)
    legacy_run(task,context,manifest,cfg,status=status)
    db.set_setting('provider',{**cfg,'base_url':'https://new.example/v1','max_attempts':5})
    calls=fresh_transport(monkeypatch)
    with pytest.raises(provider.ProviderError) as error:
        writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert error.value.unknown and not calls


def test_explicitly_acknowledged_unknown_allows_a_new_request(client,monkeypatch):
    task,context,manifest,cfg=setup(client)
    identity=legacy_run(task,context,manifest,cfg,status='outcome_unknown')
    task['payload']=db.dumps({'acknowledged_run_ids':[identity]})
    calls=fresh_transport(monkeypatch)
    result=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert result['run_id']=='fresh-response' and len(calls)==1


def test_reused_response_still_rechecks_source_permission(client,monkeypatch):
    task,context,manifest,cfg=setup(client)
    legacy_run(task,context,manifest,cfg)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(manifest['file_ids'][0],))
    calls=fresh_transport(monkeypatch)
    with pytest.raises(ValueError,match='禁止外发'):
        writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert not calls


@pytest.mark.parametrize('has_cache',[False,True])
def test_smaller_recovery_endpoint_checks_capacity_only_for_a_new_send(client,monkeypatch,has_cache):
    task,context,manifest,cfg=setup(client)
    context['sources'][0]['text']='完整原文不可裁剪。'*1200
    if has_cache:identity=legacy_run(task,context,manifest,cfg)
    db.set_setting('provider',{**cfg,'base_url':'https://small.example/v1','context_limit':512,'max_output_tokens':128})
    calls=fresh_transport(monkeypatch)
    if has_cache:
        result=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
        assert result['cache_hit'] and result['run_id']==identity
    else:
        with pytest.raises(HTTPException) as error:
            writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
        assert error.value.detail['code']=='WRITING_CONTEXT_BUDGET_EXCEEDED'
    assert not calls


def test_new_requests_save_content_key_that_survives_endpoint_change(client,monkeypatch):
    task,context,manifest,cfg=setup(client);calls=[]
    def send(*args,**kwargs):
        calls.append(True)
        return {'text':'{"claim_reviews":[],"summary":"saved"}','usage':{},'cost':0.,
                'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',send)
    first=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    meta=db.loads(db.one('SELECT manifest FROM generation_runs WHERE id=?',(first['run_id'],))['manifest'])
    assert meta['writing_flow_content_hash']
    db.set_setting('provider',{**cfg,'base_url':'https://new.example/v1','max_attempts':5})
    second=writing_flow._cached_call(task,'verify exact inputs',context,manifest,stage='semantic_review',index=8)
    assert second['cache_hit'] and second['run_id']==first['run_id'] and len(calls)==1

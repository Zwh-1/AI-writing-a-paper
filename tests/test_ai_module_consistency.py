"""Regressions at the gateway, queued assistant and public validation boundaries."""
import json
import pytest
from fastapi import HTTPException
from test_workbench import client, project
from app import db, tasks, skill_runtime, project_memory as memory
from app import assistant_service as assistant, research_records as records
from app.ai_runtime import gateway, health, inventory
from app.visual_delivery import quality, specification
from test_writing_agent import env as agent_env, start


def test_gateway_uses_operation_for_skill_selection_and_exact_added_bytes(client):
    pid=project(client);task=tasks.enqueue(pid,'studio',{})
    def execute(task,system,user,manifest,images):return system,manifest
    before='宿主图表提示'
    system,manifest=gateway.invoke(task,before,'{}',{'operation':'studio_paper_visuals'},None,execute)
    assert '技能 paper-figures：' in system
    assert 'references/visuals.txt' in manifest['skills_usage'][0]['resource_paths']
    assert manifest['skills_input_bytes']==len(system.encode())-len(before.encode())
    assert skill_runtime.enrich(task,system,'{}',manifest)==(system,manifest)
    augmented=system+'\n传输协议附加说明'
    final,metadata=skill_runtime.enrich(task,augmented,'{}',manifest)
    assert final==augmented and metadata['skills_usage']==manifest['skills_usage']
    assert metadata['skills_input_bytes']==manifest['skills_input_bytes']


def test_skills_do_not_trigger_on_substrings_or_trust_empty_usage_markers(client):
    task=tasks.enqueue(project(client),'studio',{})
    system,manifest=skill_runtime.enrich(task,'host','{}',{'stage':'overdraft'})
    assert system=='host' and manifest['skills_usage']==[]
    system,manifest=skill_runtime.enrich(task,'host','{}',{'stage':'draft','skills_stage':'draft','skills_usage':[]})
    assert '技能 academic-expression：' in system


def test_explicit_instruction_budget_includes_host_framing(client,tmp_path,monkeypatch):
    folder=tmp_path/'skills'/'example';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_text('---\nname: example\ndescription: example\n---\n'+'x'*990,encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',folder.parent)
    pid=project(client)
    assert client.put(f'/api/v1/projects/{pid}/skills/policy',json={'max_input_bytes':1000,'selected_ids':['example']}).status_code==200
    with pytest.raises(HTTPException) as error:
        skill_runtime.enrich(tasks.enqueue(pid,'studio',{}),'host','{}',{'stage':'draft'})
    assert error.value.status_code==422


@pytest.mark.parametrize('metadata',['null','[]','{tools: [{}]}','{contracts: [[]]}','{host_resources: {file.txt: []}}','{operations: [""]}'])
def test_malformed_skill_metadata_has_a_controlled_configuration_error(tmp_path,monkeypatch,metadata):
    folder=tmp_path/'skills'/'example';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_text(f'---\nname: example\ndescription: example\nmetadata: {metadata}\n---\nGuidance',encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',folder.parent)
    with pytest.raises(ValueError):skill_runtime.discover()


def test_junction_skill_root_is_rejected_before_discovery(tmp_path,monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',tmp_path)
    original=Path.is_junction
    monkeypatch.setattr(Path,'is_junction',lambda path:path==tmp_path or original(path))
    with pytest.raises(ValueError):skill_runtime.discover()


def test_duplicate_explicit_skill_ids_are_rejected(client):
    task=tasks.enqueue(project(client),'studio',{'skill_ids':['paper-figures','paper-figures']})
    with pytest.raises(HTTPException) as error:skill_runtime.enrich(task,'host','{}',{})
    assert error.value.status_code==422


@pytest.mark.parametrize('original,repair',[('draft','candidate_format_repair'),('argument_unit','unit_format_repair')])
def test_structural_repair_retains_generation_skill_guidance(client,original,repair):
    task=tasks.enqueue(project(client),'write',{})
    first,meta=skill_runtime.enrich(task,'host','{}',{'writing_stage':original})
    second,retry=skill_runtime.enrich(task,'host\n结构修复说明','{}',{'writing_stage':repair})
    assert [(r['skill_id'],r['resource_paths']) for r in meta['skills_usage']]==[(r['skill_id'],r['resource_paths']) for r in retry['skills_usage']]
    if original=='draft':
        assert '技能 academic-expression：' in first and '技能 academic-expression：' in second


@pytest.mark.parametrize('prefix',['/api/v1','/api/v2'])
@pytest.mark.parametrize('patch',[{'kind':[]},{'id':{}},{'section_id':[]},{'supersedes':[{}]},{'expected_revision':True}])
def test_memory_api_rejects_bad_types_without_writes(client,prefix,patch):
    pid=project(client)
    response=client.post(f'{prefix}/projects/{pid}/memory',json={'kind':'author_decision','subject':'范围','text':'单校样本',**patch})
    assert response.status_code==422
    assert not records.list_records(pid,'note')


def test_memory_budget_never_selects_only_one_conflicting_decision(client):
    pid=project(client);ids=[]
    for text in ('a'*350,'b'*350):
        ids.append(memory.save_memory(pid,{'kind':'author_decision','subject':'范围','text':text})['id'])
    snap=memory.snapshot(pid);snap['policy']['max_input_bytes']=1000
    small=memory.context(pid,snap)
    assert not small['items']
    assert {x['id'] for x in small['deferred']}==set(ids)
    assert all(x['reason']=='memory_conflict_budget' for x in small['deferred'])
    assert set(small['conflicts'][0]['memory_ids'])==set(ids)
    snap['policy']['max_input_bytes']=4000
    assert {x['id'] for x in memory.context(pid,snap)['items']}==set(ids)


def test_empty_memory_snapshot_cannot_cross_projects(client):
    first=project(client);second=project(client)
    task=tasks.enqueue(first,'studio',{})
    with pytest.raises(HTTPException) as error:memory.task_context(second,task=task)
    assert error.value.status_code==404


def assistant_task(client):
    pid=project(client)
    agent=records.save(pid,'agent',{'title':'审阅','prompt':'保留研究边界。','capabilities':[]})
    conversation=records.save(pid,'conversation',{'title':'审阅对话','agent_id':agent['id']})
    task=assistant.send_message(pid,conversation['id'],{'text':'请核对。','use_knowledge':False})
    return pid,agent,conversation,task


def change_agent(pid,agent,patch):
    return records.save(pid,'agent',{**agent['data'],**patch},identity=agent['id'],expected_revision=agent['revision'])


@pytest.mark.parametrize('patch',[{'prompt':'新的职责'},{'capabilities':['document_edit']},{'model_profile':'gpt'}])
def test_queued_assistant_blocks_changed_configuration_before_model(client,monkeypatch,patch):
    pid,agent,conversation,task=assistant_task(client)
    frozen_body=db.loads(task['payload'])
    assert frozen_body['agent_snapshot']['prompt']=='保留研究边界。'
    change_agent(pid,agent,patch)
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:pytest.fail('changed agent must not send'))
    with pytest.raises(HTTPException) as error:tasks.dispatch(task)
    assert error.value.status_code==409
    assert len(assistant.messages(pid,conversation['id']))==1


def test_assistant_rechecks_configuration_at_presend_boundary(client,monkeypatch):
    pid,agent,conversation,task=assistant_task(client)
    def model(task,system,user,manifest,images=None):
        change_agent(pid,agent,{'prompt':'在准备上下文后修改'})
        tasks._validate_model_source_inputs(pid,manifest)
        pytest.fail('changed configuration passed preflight')
    monkeypatch.setattr(tasks,'model_call',model)
    with pytest.raises(HTTPException) as error:tasks.dispatch(task)
    assert error.value.status_code==409
    assert len(assistant.messages(pid,conversation['id']))==1


def test_assistant_keeps_returned_result_out_of_chat_when_agent_changes_in_flight(client,monkeypatch):
    pid,agent,conversation,task=assistant_task(client)
    def model(task,system,user,manifest,images=None):
        tasks._validate_model_source_inputs(pid,manifest)
        change_agent(pid,agent,{'capabilities':['notes']})
        return {'text':db.dumps({'text':'旧配置回复','citations':[]}),'run_id':'preserved-run'}
    monkeypatch.setattr(tasks,'model_call',model)
    with pytest.raises(HTTPException) as error:tasks.dispatch(task)
    assert error.value.status_code==409
    assert len(assistant.messages(pid,conversation['id']))==1


@pytest.mark.parametrize('legacy',[False,True])
def test_unchanged_assistant_and_old_payloads_keep_working(client,monkeypatch,legacy):
    pid,agent,conversation,task=assistant_task(client)
    if legacy:
        body=db.loads(task['payload']);body.pop('agent_snapshot')
        task={**task,'payload':db.dumps(body)}
    def model(task,system,user,manifest,images=None):
        tasks._validate_model_source_inputs(pid,manifest)
        assert '保留研究边界。' in system
        assert manifest['assistant_agent_snapshot']['id']==agent['id']
        return {'text':db.dumps({'text':'已核对','citations':[]}),'run_id':'fixture'}
    monkeypatch.setattr(tasks,'model_call',model)
    assert tasks.dispatch(task)['message']['data']['text']=='已核对'


def test_default_agent_capabilities_are_not_mutable_shared_state(client):
    pid=project(client)
    first=assistant.agents(pid);first[0]['capabilities'].clear()
    assert assistant.agents(pid)[0]['capabilities']


@pytest.mark.parametrize('patch',[{'file_ids':[{}]},{'note_ids':[[]]},{'asset_ids':[True]},
                                  {'agent_id':['academic']},{'section_id':{}},{'revision_id':5}])
def test_bad_assistant_identifiers_are_rejected_before_enqueue(client,patch):
    pid=project(client);conversation=records.save(pid,'conversation',{'title':'测试'})
    with pytest.raises(HTTPException) as error:assistant.send_message(pid,conversation['id'],{'text':'请核对',**patch})
    assert error.value.status_code==422
    assert not assistant.messages(pid,conversation['id'])
    assert not db.rows('SELECT id FROM tasks WHERE project_id=?',(pid,))


@pytest.mark.parametrize('capabilities',[[{}],[[]],True])
def test_bad_agent_capabilities_produce_validation_errors(capabilities):
    with pytest.raises(HTTPException) as error:assistant.validate_agent({'title':'测试','capabilities':capabilities})
    assert error.value.status_code==422


@pytest.mark.parametrize('value',[[],False,''])
def test_invalid_falsy_delivery_specs_do_not_silently_enable_defaults(value):
    with pytest.raises(HTTPException) as error:specification(value)
    assert error.value.status_code==422


@pytest.mark.parametrize('width',[0,-1,float('nan'),float('inf'),True,'50'])
def test_placement_quality_rejects_invalid_widths(width):
    with pytest.raises(HTTPException) as error:quality({'width':1800},width)
    assert error.value.status_code==422


def test_nonfinite_metadata_cannot_pass_figure_checks_or_leak_invalid_json():
    result=quality({'width':float('inf'),'minimum_logical_font':float('nan'),'logical_width':1000})
    assert result['resolution_status']==result['text_status']=='unknown'
    json.dumps(result,allow_nan=False)


def test_mcp_status_distinguishes_host_tools_from_protocol_support(client):
    pid=project(client)
    response=client.get(f'/api/v1/ai-runtime/projects/{pid}').json()
    assert response['health']['integrations']['mcp']['status']=='not_implemented'
    assert response['health']['ready']
    assert inventory.scan()['tool_protocols']['mcp']['implemented'] is False


def test_agent_stage_after_settings_changes_inherits_server_owned_snapshots(agent_env,monkeypatch):
    from app.writing_agent import coordinator
    from app.ai_runtime.policy import frozen
    session=start(agent_env);pid=agent_env['pid']
    first=db.one('SELECT t.* FROM tasks t JOIN writing_steps s ON s.task_id=t.id WHERE s.session_id=?',(session['id'],))
    original=frozen(first)
    db.set_setting('skills:'+pid,{**skill_runtime.DEFAULT,'automatic':False})
    memory.save_memory(pid,{'kind':'author_decision','subject':'新增范围','text':'仅新任务采用此决定。'})
    def forbidden(*args):pytest.fail('resumed stages must not discover new snapshots')
    monkeypatch.setattr(skill_runtime,'snapshot',forbidden)
    monkeypatch.setattr(memory,'snapshot',forbidden)
    step=coordinator.enqueue(session['id'],'judgment')
    child=db.one('SELECT * FROM tasks WHERE id=?',(step['task_id'],))
    assert frozen(child)==original


def test_public_payload_cannot_inject_an_old_skill_snapshot(client):
    from app.ai_runtime.policy import frozen
    pid=project(client);task=tasks.enqueue(pid,'write',{})
    old=frozen(task)
    client.put(f'/api/v1/projects/{pid}/skills/policy',json={'automatic':False})
    with pytest.raises(HTTPException) as error:tasks.enqueue(pid,'write',{'ai_runtime':old})
    assert error.value.status_code==422


def test_checkpoint_restore_rejects_conflicts_and_rolls_back_partial_restore(client):
    import sqlite3
    from pathlib import Path
    from app import context_maintenance as maintenance, security
    from test_context_archival_and_review import completed_graph
    pid,task=completed_graph(client)
    archived=maintenance.archive(pid,{'keep_days':30})
    entry=db.setting('context_archives:'+pid)[0]
    data=db.loads(security.safe_object(entry['blob']).read_text(encoding='utf-8'))
    row=maintenance._decode(data['checkpoints']['checkpoints'][-1])
    row['checkpoint']=b'conflicting-state'
    path=Path(db.DB_PATH).parent/'ai-graph-checkpoints.sqlite3'
    with sqlite3.connect(path) as connection:
        columns=list(row)
        connection.execute('INSERT INTO checkpoints('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(row.values()))
    with pytest.raises(HTTPException) as error:maintenance.restore(pid,archived['archive_id'])
    assert error.value.status_code==409
    assert not db.rows('SELECT * FROM ai_runtime_events WHERE task_id=?',(task['id'],))
    assert not db.setting('context_archives:'+pid)[0].get('restored')
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT checkpoint FROM checkpoints').fetchall()==[(b'conflicting-state',)]

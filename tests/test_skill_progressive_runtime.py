from pathlib import Path
import pytest
from fastapi import HTTPException
from test_workbench import client,project
from app import db,tasks,security,skill_runtime
from app.ai_runtime.policy import frozen
from test_writing_agent import env as agent_env,start


def test_skill_snapshot_is_server_owned_and_instructions_survive_file_changes(client,tmp_path,monkeypatch):
    root=tmp_path/'skills';folder=root/'custom';folder.mkdir(parents=True)
    file=folder/'SKILL.md'
    file.write_text('---\nname: custom\ndescription: 说明\nmetadata:\n  operations: [draft]\n  tools: []\n---\n旧版说明',encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',root)
    pid=project(client);task=tasks.enqueue(pid,'support_check',{})
    snap=frozen(task)['skills']
    file.write_text(file.read_text(encoding='utf-8').replace('旧版','新版'),encoding='utf-8')
    assert skill_runtime.load(snap,'custom')['text']=='旧版说明'
    system,manifest=skill_runtime.enrich(task,'system','',{'stage':'draft'})
    assert '旧版说明' in system and manifest['skills_usage'][0]['trigger']=='operation:draft'
    assert skill_runtime.enrich(task,system,'',manifest)[0]==system
    assert '新版说明' in skill_runtime.load(skill_runtime.snapshot(pid),'custom')['text']


def test_explicit_skill_selection_and_unknown_name_are_validated(client):
    pid=project(client)
    response=client.put(f'/api/v1/projects/{pid}/skills/policy',json={'automatic':False,'selected_ids':['paper-figures']})
    assert response.status_code==200,response.text
    task=tasks.enqueue(pid,'support_check',{})
    system,manifest=skill_runtime.enrich(task,'system','',{'stage':'unrelated'})
    assert manifest['skills_usage'][0]['trigger']=='explicit'
    assert client.put(f'/api/v1/projects/{pid}/skills/policy',json={'selected_ids':['absent']}).status_code==422
    with pytest.raises(HTTPException):skill_runtime.load(frozen(task)['skills'],'paper-figures','../private.txt')


def test_skill_resource_hashes_and_host_tool_allowlist(client,tmp_path,monkeypatch):
    folder=tmp_path/'skills'/'custom';folder.mkdir(parents=True)
    (folder/'SKILL.md').write_text('---\nname: custom\ndescription: 说明\nmetadata:\n  tools: [delete_database]\n---\n说明',encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',folder.parent)
    with pytest.raises(ValueError,match='未注册'):skill_runtime.discover()


def test_skill_tool_uses_frozen_catalogue_and_persists_exact_observation(agent_env):
    from app.writing_agent import tools
    session=start(agent_env)
    step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    args={'skill_id':'paper-figures'}
    result=tools.execute(agent_env['pid'],step['id'],'load_skill',args,'load-one',agent_env['snapshot']['id'])
    assert result['data']['kind']=='workflow_guidance' and result['data']['evidence'] is False
    assert tools.execute(agent_env['pid'],step['id'],'load_skill',args,'load-one',agent_env['snapshot']['id'])==result
    assert db.one("SELECT id FROM tool_observations WHERE step_id=? AND tool_name='load_skill'",(step['id'],))


def test_skill_tool_loads_registered_prompt_resource_progressively(agent_env):
    from app.writing_agent import tools
    session=start(agent_env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    instructions=tools.execute(agent_env['pid'],step['id'],'load_skill',{'skill_id':'paper-figures'},'instructions',agent_env['snapshot']['id'])
    path='references/visuals.txt'
    assert path in {r['path'] for r in instructions['data']['resources']}
    resource=tools.execute(agent_env['pid'],step['id'],'load_skill',{'skill_id':'paper-figures','resource':path},'resource',agent_env['snapshot']['id'])
    assert resource['data']['resource']==path and resource['data']['evidence'] is False
    assert resource['data']['content_hash']==next(r['hash'] for r in instructions['data']['resources'] if r['path']==path)
    assert resource['data']['contracts']==['visual_spec'] and resource['data']['project_standards']['version_hash']
    assert len(db.rows("SELECT result_blob FROM tool_observations WHERE step_id=? AND tool_name='load_skill'",(step['id'],)))==2


def test_explicit_skills_get_budget_before_automatic_skills(client,tmp_path,monkeypatch):
    root=tmp_path/'skills'
    for name,text in [('a-auto','A'*700),('z-explicit','E'*600)]:
        folder=root/name;folder.mkdir(parents=True)
        (folder/'SKILL.md').write_text(f'---\nname: {name}\ndescription: description\nmetadata:\n  operations: [draft]\n---\n{text}',encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',root)
    pid=project(client)
    response=client.put(f'/api/v1/projects/{pid}/skills/policy',json={'selected_ids':['z-explicit'],'max_input_bytes':1000})
    assert response.status_code==200,response.text
    task=tasks.enqueue(pid,'support_check',{})
    system,manifest=skill_runtime.enrich(task,'system','',{'stage':'draft'})
    assert 'E'*600 in system and 'A'*700 not in system
    assert [(row['skill_id'],row['status']) for row in manifest['skills_usage']]==[('z-explicit','loaded'),('a-auto','deferred')]


def test_optional_resource_cannot_displace_later_explicit_instructions(client,tmp_path,monkeypatch):
    root=tmp_path/'skills'
    for name,text in [('first','A'*100),('second','B'*600)]:
        folder=root/name;folder.mkdir(parents=True)
        (folder/'SKILL.md').write_text(f'---\nname: {name}\ndescription: description\nmetadata:\n  resource_operations:\n    resource.txt: [draft]\n---\n{text}',encoding='utf-8')
        (folder/'resource.txt').write_text('R'*400,encoding='utf-8')
    monkeypatch.setattr(skill_runtime,'SKILL_ROOT',root)
    pid=project(client)
    client.put(f'/api/v1/projects/{pid}/skills/policy',json={'automatic':False,'selected_ids':['first','second'],'max_input_bytes':1000})
    task=tasks.enqueue(pid,'support_check',{})
    system,manifest=skill_runtime.enrich(task,'system','',{'stage':'draft'})
    assert 'A'*100 in system and 'B'*600 in system and 'R'*400 not in system
    assert all(row['deferred_resources']==['resource.txt'] for row in manifest['skills_usage'])


def test_host_prompt_resource_is_reused_with_frozen_provenance(client):
    from app.writing_agent import prompts
    pid=project(client);task=tasks.enqueue(pid,'support_check',{})
    snapshot=frozen(task)['skills']
    item=skill_runtime.load(snapshot,'academic-expression','references/expression.txt')
    original=prompts.DRAFT
    system,manifest=skill_runtime.enrich(task,original,'',{'stage':'draft'})
    assert system.count(item['text'].strip())==original.count(item['text'].strip())==1
    usage=next(row for row in manifest['skills_usage'] if row['skill_id']=='academic-expression')
    resource=next(row for row in usage['resources'] if row['path']==item['resource'])
    assert resource=={'path':item['resource'],'hash':item['content_hash'],'delivery':'existing_prompt'}
    assert manifest['skills_input_bytes']<len(item['text'].encode())


def test_new_stage_or_rebuilt_prompt_cannot_skip_skill_loading(client):
    pid=project(client);task=tasks.enqueue(pid,'support_check',{})
    system,manifest=skill_runtime.enrich(task,'draft system','',{'writing_stage':'draft'})
    rebuilt,rebuilt_manifest=skill_runtime.enrich(task,'replacement system','',manifest)
    assert '技能 academic-expression' in rebuilt and rebuilt_manifest['skills_stage']=='draft'
    review,review_manifest=skill_runtime.enrich(task,'review system','',{**manifest,'writing_stage':'discourse_review'})
    assert 'references/review.txt' in review
    assert review_manifest['skills_stage']=='discourse_review'
    assert review_manifest['skills_usage'][0]['resource_paths']==['references/review.txt']
    continued,_=skill_runtime.enrich(task,system,'',{**manifest,'writing_stage':'draft_unit'})
    assert continued.count('技能 academic-expression：')==1


def test_skill_guidance_reserves_context_before_optional_rules(agent_env):
    from test_writing_context_packing import sample_payload
    from app.writing_agent import context,provider_adapter
    from app.writing_agent.packing import estimate
    session=start(agent_env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(step['task_id'],))
    data,manifest=sample_payload();manifest['stage']='draft'
    system,manifest=provider_adapter.prepare_system(task,'写作',data,manifest)
    cfg={'context_limit':16000,'max_output_tokens':2048}
    context.save_context(step['id'],data,manifest,system,cfg)
    assert manifest['skills_usage'] and manifest['rule_selection']['deferred_recommended_ids']
    assert estimate(data,manifest,system,cfg)+2048<=16000
    assert data['rules'][0]['id']=='mandatory'
    assert provider_adapter.prepare_system(task,system,data,manifest)==(system,manifest)

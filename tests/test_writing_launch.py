"""Exercise the user-facing launch route through worker stages and saved prose."""
from test_writing_agent import env,fake_model,finish
from app import db,provider,documents,security
from app.writing_agent import profiles
from app.writing_agent.common import state,update_state


def launch(env,**overrides):
    body={'section_id':env['sid'],'base_revision_id':None,'expected_project_state_revision':state(env['pid'])['revision'],**overrides}
    return env['client'].post(f'/api/v1/projects/{env["pid"]}/writing-start',json=body)


def test_launch_without_manual_profile_runs_pipeline_and_adopts_real_revision(env,monkeypatch):
    calls=fake_model(monkeypatch)
    db.execute('DELETE FROM writing_profiles WHERE project_id=?',(env['pid'],))
    response=launch(env);assert response.status_code==200,response.text
    session=finish(env,response.json())
    assert session['status']=='needs_review',session
    assert [s['stage'] for s in session['steps']]==['investigate','judgment','judgment_check','draft','annotation','semantic','coherence']
    assert all(s['state']=='completed' for s in session['steps'])
    assert calls and not db.rows('SELECT * FROM facts') and not db.rows('SELECT * FROM artifacts')
    profile=profiles.get(env['pid'],session['profile_id'])
    assert profile['configuration_current'] and profile['qualification']=='unassessed'
    assert profile['configuration']=={'route':'primary'}
    cid=session['candidates'][-1]['id']
    candidate=env['client'].get(f'/api/v1/projects/{env["pid"]}/writing-candidates/{cid}').json()
    assert '发行方' in documents.plain(candidate['document']) and candidate['analysis']
    assert db.one('SELECT current_revision_id FROM sections WHERE id=?',(env['sid'],))['current_revision_id'] is None
    result=env['client'].post(f'/api/v1/projects/{env["pid"]}/writing-candidates/{cid}/accept',json={'base_revision_id':None,'accept_policy':'draft','placement':'replace_section'},headers={'Idempotency-Key':'launch-through-adoption'})
    assert result.status_code==200,result.text
    saved=env['client'].get(f'/api/v1/sections/{env["sid"]}/content').json()
    assert saved['section']['current_revision_id'] in result.json()['revision_ids']
    assert '发行方' in saved['revision']['text']
    assert saved['section']['state']!='user_reviewed'


def test_launch_replaces_stale_default_and_reuses_current_configuration(env,monkeypatch):
    fake_model(monkeypatch)
    cfg=provider.config();db.set_setting('provider',{**cfg,'model':'new-model-for-launch'})
    old_count=db.one('SELECT COUNT(*) n FROM writing_profiles')['n']
    first=launch(env);assert first.status_code==200,first.text
    selected=profiles.get(env['pid'],first.json()['profile_id'])
    assert selected['id']!=env['profile']['id'] and selected['actual_config']['model']=='new-model-for-launch'
    assert db.one('SELECT COUNT(*) n FROM writing_profiles')['n']==old_count+1
    second=launch(env);assert second.status_code==200,second.text
    assert second.json()['profile_id']==selected['id']
    assert db.one('SELECT COUNT(*) n FROM writing_profiles')['n']==old_count+1
    assert not profiles.get(env['pid'],env['profile']['id'])['configuration_current']


def test_launch_can_build_missing_baseline_from_existing_project(env):
    update_state(env['pid'],state(env['pid'])['revision'],core_version_id=None,target_snapshot_id=None,contract_heads_json='{}')
    response=launch(env);assert response.status_code==200,response.text
    head=state(env['pid'])
    assert head['core_version_id'] and head['target_snapshot_id']==env['snapshot']['id']
    assert head['contracts'][env['sid']]==response.json()['base_versions']['contract_version_id']


def test_launch_preserves_explicit_stale_profile_error(env):
    db.set_setting('provider',{**provider.config(),'model':'changed-after-profile'})
    response=launch(env,profile_id=env['profile']['id'])
    assert response.status_code==409 and response.json()['detail']['code']=='PROFILE_STALE'
    assert not db.rows('SELECT * FROM writing_sessions')


def test_launch_checks_versions_before_creating_profiles_or_tasks(env):
    before=db.one('SELECT COUNT(*) n FROM writing_profiles')['n']
    for extra in ({'expected_project_state_revision':999},{'base_revision_id':'obsolete-revision'}):
        response=launch(env,**extra)
        assert response.status_code==409 and response.json()['detail']['code']=='INPUT_VERSION_CHANGED'
    assert db.one('SELECT COUNT(*) n FROM writing_profiles')['n']==before
    assert not db.rows('SELECT * FROM writing_sessions') and not db.rows('SELECT * FROM tasks')


def test_launch_missing_credentials_does_not_queue_work(env,monkeypatch):
    monkeypatch.setattr(security,'get_key',lambda profile='':'')
    response=launch(env)
    assert response.status_code==422 and response.json()['detail']['code']=='PROVIDER_KEY_MISSING'
    assert not db.rows('SELECT * FROM writing_sessions') and not db.rows('SELECT * FROM tasks')


def test_launch_invalid_budget_rolls_back_automatic_profile_creation(env):
    db.execute('DELETE FROM writing_profiles WHERE project_id=?',(env['pid'],))
    for value in (True,0,-1,env['client'].get(f'/api/v1/projects/{env["pid"]}').json()['budget']+1):
        response=launch(env,budget_limit=value)
        assert response.status_code==422,response.text
    assert not db.rows('SELECT * FROM writing_profiles') and not db.rows('SELECT * FROM writing_sessions')


def test_launch_cross_project_section_and_unpublished_rules_are_rejected(env):
    other=env['client'].post('/api/v1/projects',json={'title':'另一项目'}).json()['id']
    response=env['client'].post(f'/api/v1/projects/{other}/writing-start',json={'section_id':env['sid'],'expected_project_state_revision':0})
    assert response.status_code==404
    db.execute('UPDATE projects SET rule_package_id=NULL WHERE id=?',(env['pid'],))
    response=launch(env)
    assert response.status_code==422 and response.json()['detail']['code']=='RULE_PACKAGE_REQUIRED'
    assert not db.rows('SELECT * FROM writing_sessions')


def test_preflight_without_profile_leaves_project_history_and_queue_unchanged(env):
    db.execute('DELETE FROM writing_profiles WHERE project_id=?',(env['pid'],))
    tables=('writing_profiles','evidence_items','thesis_core_versions','section_contract_versions','writing_sessions','tasks')
    before={table:db.one('SELECT COUNT(*) n FROM '+table)['n'] for table in tables}
    response=env['client'].get(f'/api/v1/projects/{env["pid"]}/writing-preflight',params={'section_id':env['sid']})
    assert response.status_code==200,response.text
    value=response.json();assert value['can_start'] and value['context']['input_estimate']>0
    assert value['budget']['available']==20 and value['default_budget']==20
    assert before=={table:db.one('SELECT COUNT(*) n FROM '+table)['n'] for table in tables}


def test_real_input_over_limit_is_reported_before_session_is_queued(env):
    package=db.one('SELECT * FROM rule_packages WHERE project_id=?',(env['pid'],))
    rules=db.loads(package['rules'],[])
    rules[0]['statement']='必须完整保留的适用规则。'*12000
    db.execute('UPDATE rule_packages SET rules=? WHERE id=?',(db.dumps(rules),package['id']))
    response=env['client'].get(f'/api/v1/projects/{env["pid"]}/writing-preflight',params={'section_id':env['sid']})
    value=response.json();assert not value['can_start']
    assert value['context']['rule_count']==len(rules)
    assert value['context']['input_estimate']>value['context']['limit']
    assert 'CONTEXT_BUDGET_EXCEEDED' in {item['code'] for item in value['blockers']}
    result=launch(env);assert result.status_code==422,result.text
    assert result.json()['detail']['code']=='CONTEXT_BUDGET_EXCEEDED'
    assert not db.rows('SELECT * FROM writing_sessions') and not db.rows('SELECT * FROM tasks')


def test_exhausted_project_budget_is_visible_and_never_raised_automatically(env):
    db.set_setting('provider',{**provider.config(),'input_price':None,'output_price':None,'max_request_cost':2})
    db.execute('UPDATE projects SET budget=.05 WHERE id=?',(env['pid'],))
    response=env['client'].get(f'/api/v1/projects/{env["pid"]}/writing-preflight',params={'section_id':env['sid']})
    value=response.json();assert not value['can_start']
    assert value['budget']['available']==.05 and value['budget']['request_reservation']==2
    assert 'PROJECT_BUDGET_INSUFFICIENT' in {item['code'] for item in value['blockers']}
    result=launch(env);assert result.status_code==422,result.text
    assert db.one('SELECT budget FROM projects WHERE id=?',(env['pid'],))['budget']==.05
    assert not db.rows('SELECT * FROM writing_sessions') and not db.rows('SELECT * FROM tasks')


def test_automatic_workflow_allocates_output_by_generation_and_verification_stage(env,monkeypatch):
    fake_model(monkeypatch)
    db.set_setting('provider',{**provider.config(),'max_output_tokens':8192})
    response=launch(env);assert response.status_code==200,response.text
    session=response.json();generation=profiles.get(env['pid'],session['profile_id'])
    assert generation['actual_config']['max_output_tokens']==6144
    overrides=session['policy']['stage_profiles'];assert set(overrides)=={'judgment_check','annotation','semantic','coherence','regression'}
    assert all(profiles.get(env['pid'],overrides[stage])['actual_config']['max_output_tokens']==4096 for stage in ('judgment_check','annotation','regression'))
    assert all(profiles.get(env['pid'],overrides[stage])['actual_config']['max_output_tokens']==3072 for stage in ('semantic','coherence'))
    assert provider.config()['max_output_tokens']==8192
    completed=finish(env,session)
    assert completed['status']=='needs_review' and all(step['state']=='completed' for step in completed['steps'])

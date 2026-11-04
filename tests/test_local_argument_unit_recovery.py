"""An explicitly bound local draft resumes a failed unit without fake API runs."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, response  # noqa: F401
from test_writing_flow_safety import _unit_setup
from app import db, provider, security, tasks, writing_flow, paper_writing
from app.writing_agent.common import digest, unblob


def generate(env):
    return writing_flow.generate(env['task'],env['section'],'按原文返回本单元JSON',
                                 env['context'],env['manifest'],tasks.candidate_from_text)


@pytest.fixture
def recovery(writing_project,monkeypatch,request):
    task,section,context,manifest=_unit_setup(writing_project)
    observation=None
    if getattr(request,'param',None)=='measurement_prefix':
        from app.writing_agent import evidence
        observation=evidence.sync_source(task['project_id'],context['sources'][0]['id'],kind='literature')
        context['project_evidence']=[observation]
        manifest['evidence_versions']={observation['id']:observation['origin_version']}
    calls=[]
    def transport(system,user,images=None):
        unit=db.loads(user)['argument_unit']['index'];calls.append(unit)
        if unit==3:raise provider.ProviderError('synthetic completed-prefix rejection')
        paragraph={'text':'模块职责清晰有助于维护。' if unit==1 else '接口边界应与授权条件保持一致。'}
        if observation is not None and unit==1:
            paragraph={'text':'不把设计取向表述为已部署能力。','evidence_ids':[observation['id']]}
        return response({'paragraphs':[paragraph]})
    monkeypatch.setattr(provider,'generate',transport)
    env={'task':task,'section':section,'context':context,'manifest':manifest,'calls':calls,'project':writing_project}
    with pytest.raises(provider.ProviderError):generate(env)
    step=db.one("SELECT * FROM writing_steps WHERE task_id=? AND stage='argument_units'",(task['id'],))
    saved=unblob(step['output_blob']);assert len(saved['units'])==2
    snapshot=paper_writing.frozen_state(task['project_id'])
    payload=db.loads(task['payload'],{})|{'snapshot':snapshot,'snapshot_hash':digest(snapshot),
        'budget_limit':10.,'generation_order':[section['id']]}
    db.execute("UPDATE tasks SET kind='write_paper',payload=? WHERE id=?",(db.dumps(payload),task['id']))
    env['task']=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    env['step']=step;env['saved']=saved
    env['prefix']=[p for unit in saved['units'] for p in unit['candidate']['paragraphs']]
    env['candidate']={'paragraphs':[{'text':'本单元依据原文讨论授权边界，真实运行结果仍待补证。',
                                    'source_ids':[context['sources'][0]['id']]}]}
    def no_model(*args,**kwargs):pytest.fail('A local recovery must not silently call a paid provider')
    monkeypatch.setattr(writing_flow,'_cached_call',no_model)
    return env


def record(env,index=2,candidate=None,prefix=None):
    candidate=candidate or env['candidate']
    return {'schema':'writing.local_argument_unit.v1','task_id':env['task']['id'],
        'section_id':env['section']['id'],'candidate_blob':security.put_blob(db.dumps(candidate).encode()),
        'unit_index':index,'total':3,'target_words':1000,
        'input_manifest':deepcopy(writing_flow._cached_input_manifest(env['manifest'])),
        'expected_prefix_hash':digest(env['prefix'] if prefix is None else prefix),
        'provenance':{'origin':'codex_recovery_draft'},'external_api_response':False,
        'human_review':'pending','semantic_review':'pending',
        'derived_source_run_ids':[unit['run_id'] for unit in env['saved']['units']]}


def install(env,records):
    task=env['task'];out=db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'],{}) or {}
    out['local_argument_unit_recoveries']={env['section']['id']:records}
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(out),task['id']))


def test_local_third_unit_preserves_prefix_and_does_not_create_an_api_run(recovery):
    env=recovery;item=record(env);install(env,{'2':item})
    before=db.rows('SELECT id,status FROM generation_runs WHERE task_id=? ORDER BY rowid',(env['task']['id'],))
    result=db.loads(generate(env)['text'])
    assert result['paragraphs'][:2]==env['prefix']
    assert result['paragraphs'][2]['text']==env['candidate']['paragraphs'][0]['text']
    saved=unblob(db.one('SELECT output_blob FROM writing_steps WHERE id=?',(env['step']['id'],))['output_blob'])
    assert saved['units'][:2]==env['saved']['units']
    local=saved['units'][2]
    assert local['run_id'] is None and local['local_recovery_blob']==item['candidate_blob']
    assert local['external_api_response'] is False and local['semantic_review']=='pending'
    assert local['provenance']['origin']=='codex_recovery_draft'
    assert env['manifest']['local_argument_unit_recoveries']['status']=='unverified'
    assert db.rows('SELECT id,status FROM generation_runs WHERE task_id=? ORDER BY rowid',(env['task']['id'],))==before


@pytest.mark.parametrize('change',[
    'other_task','other_section','unit_index','rules','source_version','prefix','api','human','semantic','origin','contradictory_provenance'])
def test_changed_scope_or_false_provenance_stops_without_a_paid_fallback(recovery,change):
    env=recovery;item=record(env)
    if change=='other_task':item['task_id']='another-task'
    elif change=='other_section':item['section_id']='another-section'
    elif change=='unit_index':item['unit_index']=1
    elif change=='rules':item['input_manifest']['rule_package_id']='another-package'
    elif change=='source_version':item['input_manifest']['knowledge_unit_versions']={'unknown':'changed'}
    elif change=='prefix':item['expected_prefix_hash']='changed'
    elif change=='api':item['external_api_response']=True
    elif change=='human':item['human_review']='verified'
    elif change=='semantic':item['semantic_review']='supported'
    elif change=='origin':item['provenance']['origin']='provider_response'
    elif change=='contradictory_provenance':item['provenance']['human_review']='verified'
    install(env,{'2':item})
    with pytest.raises(HTTPException) as error:generate(env)
    assert error.value.status_code==409
    assert error.value.detail['code']=='LOCAL_ARGUMENT_UNIT_RECOVERY_INVALID'


def test_permissions_are_rechecked_after_the_record_was_prepared(recovery):
    env=recovery;install(env,{'2':record(env)})
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(env['project']['file']['id'],))
    with pytest.raises(HTTPException) as error:generate(env)
    assert error.value.status_code==409
    assert '来源' in error.value.detail['message']


@pytest.mark.parametrize('field',['source_ids','knowledge_unit_ids','evidence_ids','document_reading_ids','reference_ids'])
def test_local_blob_cannot_invent_a_context_identity(recovery,field):
    env=recovery;candidate=deepcopy(env['candidate']);candidate['paragraphs'][0][field]=['not-provided']
    install(env,{'2':record(env,candidate=candidate)})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '没有提供' in error.value.detail['message']


def test_single_reference_intent_id_is_also_bound_to_the_actual_context(recovery):
    env=recovery;candidate=deepcopy(env['candidate']);paragraph=candidate['paragraphs'][0]
    paragraph['claim_spans']=[{'local_id':'c1','text':paragraph['text'],'source_ids':paragraph['source_ids']}]
    paragraph['citation_intents']=[{'claim_id':'c1','reference_id':'unprovided-book','source_ids':paragraph['source_ids']}]
    install(env,{'2':record(env,candidate=candidate)})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '没有提供' in error.value.detail['message']


def test_source_mapping_is_locally_compiled_before_a_local_unit_is_accepted(recovery):
    env=recovery;candidate=deepcopy(env['candidate']);paragraph=candidate['paragraphs'][0]
    paragraph['claim_spans']=[{'local_id':'c1','text':'正文不存在的原句。','source_ids':paragraph['source_ids']}]
    install(env,{'2':record(env,candidate=candidate)})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '主张或引用映射无效' in error.value.detail['message']


def test_record_cannot_be_used_by_a_stale_task_attempt(recovery):
    env=recovery;install(env,{'2':record(env)})
    env['task']['attempt']-=1
    with pytest.raises(HTTPException) as error:generate(env)
    assert '当前执行代次' in error.value.detail['message']


def test_explicit_metadata_recovery_overrides_and_archives_an_existing_unit(recovery):
    env=recovery
    corrected=deepcopy(env['saved']['units'][0]['candidate'])
    corrected['paragraphs'][0]['source_ids']=[env['context']['sources'][0]['id']]
    first=record(env,index=0,candidate=corrected,prefix=[])
    canonical=tasks.candidate_from_text(db.dumps(corrected))[0]
    new_prefix=canonical['paragraphs']+env['saved']['units'][1]['candidate']['paragraphs']
    last=record(env,prefix=new_prefix)
    install(env,{'0':first,'2':last})
    generate(env)
    saved=unblob(db.one('SELECT output_blob FROM writing_steps WHERE id=?',(env['step']['id'],))['output_blob'])
    assert saved['units'][0]['candidate']==canonical
    assert saved['units'][0]['run_id'] is None
    assert saved['units'][1]==env['saved']['units'][1]
    assert saved['local_recovery_replacements'][0]['original_unit']==env['saved']['units'][0]


def test_metadata_recovery_cannot_rewrite_a_valid_completed_prefix(recovery):
    env=recovery;candidate=deepcopy(env['saved']['units'][0]['candidate'])
    candidate['paragraphs'][0]['text']='不应暗中改写有效前缀。'
    install(env,{'0':record(env,index=0,candidate=candidate,prefix=[])})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '不得改写已有正文' in error.value.detail


@pytest.mark.parametrize('recovery',['measurement_prefix'],indirect=True)
def test_existing_local_critical_unit_can_be_explicitly_repaired_and_archived(recovery):
    env=recovery
    # This real phrase triggers the existing measurement-level detector. It is
    # not made acceptable by changing that detector or adding a bypass flag.
    old=deepcopy(env['saved']['units'][0])
    old['candidate']['paragraphs'][0]['text']='不把设计取向表述为已部署能力。'
    saved=deepcopy(env['saved']);saved['units'][0]=old
    from app.writing_agent.common import blob
    db.execute('UPDATE writing_steps SET output_blob=? WHERE id=?',(blob(saved),env['step']['id']))
    from app.writing_evidence_quality import check_candidate
    before=check_candidate(env['task']['project_id'],old['candidate']['paragraphs'],env['context'],env['manifest'])
    assert any(f['kind']=='result_evidence_level' for f in before['findings'])
    corrected=deepcopy(old['candidate'])
    corrected['paragraphs'][0]['text']='尚未确认实际部署情况。'
    canonical=tasks.candidate_from_text(db.dumps(corrected))[0]
    new_prefix=canonical['paragraphs']+env['saved']['units'][1]['candidate']['paragraphs']
    install(env,{'0':record(env,index=0,candidate=corrected,prefix=[]),
                 '2':record(env,prefix=new_prefix)})
    generate(env)
    repaired=unblob(db.one('SELECT output_blob FROM writing_steps WHERE id=?',(env['step']['id'],))['output_blob'])
    assert repaired['units'][0]['candidate']['paragraphs'][0]['text']=='尚未确认实际部署情况。'
    assert repaired['local_recovery_replacements'][0]['original_unit']==old
    assert repaired['units'][0]['human_review']=='pending'


def test_absent_record_keeps_the_normal_provider_path(recovery,monkeypatch):
    env=recovery;seen=[]
    def transport(*args,**kwargs):
        seen.append(1)
        return response(env['candidate'])
    monkeypatch.setattr(writing_flow,'_cached_call',transport)
    generate(env)
    assert seen==[1]


def test_saved_local_unit_cannot_outlive_its_explicit_recovery_record(recovery):
    env=recovery;install(env,{'2':record(env)})
    generate(env)
    env['manifest'].pop('argument_units',None)
    env['manifest'].pop('local_argument_unit_recoveries',None)
    install(env,{})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '缺少其显式恢复记录' in error.value.detail['message']


def test_derived_api_trace_cannot_belong_to_another_task(recovery):
    env=recovery;item=record(env)
    other=tasks.enqueue(env['task']['project_id'],'provider_probe',{})
    db.execute('UPDATE generation_runs SET task_id=? WHERE id=?',
               (other['id'],item['derived_source_run_ids'][0]))
    install(env,{'2':item})
    with pytest.raises(HTTPException) as error:generate(env)
    assert '其他任务' in error.value.detail['message']

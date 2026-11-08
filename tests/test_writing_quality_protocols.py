"""Frozen scoped verification, atomic patches and immutable ranges, with no API calls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_workbench import client, project, file
from test_writing_flow_integration import writing_project, response
from app import ai_cache_policy, db, evidence_ranges, knowledge_units as units, security, tasks, writing_flow
from app import writing_quality_protocols as protocols, writing_revision_patch as patches


def frozen(**fields):
    return {'id':'synthetic-task','project_id':'synthetic-project','payload':json.dumps(fields)}


def test_quality_versions_freeze_for_new_tasks_and_old_tasks_do_not_upgrade(client):
    pid=project(client)
    first=tasks.enqueue(pid,'write',{})
    payload=db.loads(first['payload'])
    assert payload['verification_scope_version']==protocols.VERIFICATION
    assert payload['revision_protocol_version']==protocols.REVISION
    ai_cache_policy.save_policy({'new_task_verification_scope':'legacy_full','new_task_revision_protocol':'full_candidate'})
    later=tasks.enqueue(pid,'write',{})
    assert not protocols.verification_enabled(later) and not protocols.patch_enabled(later)
    assert protocols.verification_enabled(first) and protocols.patch_enabled(first)
    assert not protocols.verification_enabled(frozen()) and not protocols.patch_enabled(frozen())


@pytest.mark.parametrize('field,value',[('new_task_verification_scope','anything'),('new_task_revision_protocol',True)])
def test_invalid_quality_default_does_not_persist(client,field,value):
    with pytest.raises(Exception):ai_cache_policy.save_policy({field:value})
    assert db.setting('ai_cache_policy') is None


def projection():
    paragraphs=[{'text':f'Original paragraph {index}.','source_ids':['s1']} for index in range(30)]
    context={'section':{'dependencies':['premise']},'sources':[],
             'related_sections':[{'section_id':'premise','text':'Complete required premise.'},
                                 {'section_id':'unrelated','text':'Not supplied.'}],
             'paper_draft_sections':[{'section_id':'unrelated-draft','text':'Not supplied either.'}]}
    request={'claims':[{'claim_id':'c1','paragraph_index':16,'start_cp':0,'end_cp':10,'source_ids':['s1']}],
             'actual_sources':[{'id':'s1','text':'Full exact original.','char_start':0,'char_end':20}],
             'candidate_paragraphs':paragraphs,'related_sections':context['related_sections'],
             'paper_draft_sections':context['paper_draft_sections'],'output_contract':'Return JSON.'}
    return paragraphs,context,request


def test_window_projection_preserves_exact_sources_premises_and_explicit_omissions():
    paragraphs,context,request=projection()
    prior=deepcopy((paragraphs,context,request))
    result=protocols.project_verification(request,paragraphs,context,{})
    assert [row['paragraph_index'] for row in result['candidate_paragraphs']]==[15,16,17]
    assert result['actual_sources']==request['actual_sources']
    assert result['related_sections']==context['related_sections'][:1] and not result['paper_draft_sections']
    scope=result['verification_scope']
    assert scope['omitted_paragraph_count']==27 and len(scope['omitted_sections'])==2
    assert scope['claim_sources']['c1']['allowed_source_ids']==['s1']
    assert not scope['global_coherence']['coverage_complete']
    assert (paragraphs,context,request)==prior


def test_batch_receipt_does_not_grant_one_claim_another_claims_sources():
    paragraphs,context,request=projection()
    request['claims'].append({'claim_id':'c2','paragraph_index':17,'start_cp':0,'end_cp':10,'source_ids':['s2']})
    request['actual_sources'].append({'id':'s2','text':'Other actual source.'})
    scope=protocols.project_verification(request,paragraphs,context,{})['verification_scope']
    assert scope['claim_sources']['c1']['allowed_source_ids']==['s1']
    assert scope['claim_sources']['c2']['allowed_source_ids']==['s1','s2']  # Legacy paragraph links are also explicit.
    paragraphs[17]['source_ids']=[]
    scope=protocols.project_verification(request,paragraphs,context,{})['verification_scope']
    assert scope['claim_sources']['c2']['allowed_source_ids']==['s2']


def test_semantic_window_rejects_cross_claim_quote_and_omitted_paragraph_completion(client,monkeypatch):
    pid=project(client)
    task={'id':'synthetic','project_id':pid,'payload':db.dumps({'verification_scope_version':protocols.VERIFICATION})}
    paragraphs=[{'text':f'Paragraph {index}.','source_ids':['s1' if index==1 else 's2']} for index in range(12)]
    checks=[{'claim_id':'c1','paragraph_index':1,'start_cp':0,'end_cp':12,'text':'Paragraph 1.','support':'unchecked','source_ids':['s1']},
            {'claim_id':'c2','paragraph_index':2,'start_cp':0,'end_cp':12,'text':'Paragraph 2.','support':'unchecked','source_ids':['s2']}]
    context={'sources':[{'id':'s1','text':'First exact evidence.'},{'id':'s2','text':'Second exact evidence.'}],
             'writing_brief':{'points':[{'id':'p','text':'Point','required':True}]}}
    def response(parent,system,request,manifest,**kwargs):
        assert request['verification_scope']['provided_paragraph_indices']==[0,1,2,3]
        return {'run_id':'mock','text':db.dumps({'claim_reviews':[
            {'claim_id':claim['claim_id'],'support':'supported','source_quotes':[{'source_id':'s2','quote':'Second exact evidence.'}]}
            for claim in request['claims']],
            'point_reviews':[{'point_id':'p','status':'complete','paragraph_indices':[11],'evidence_text':'Paragraph 11.'}],
            'findings':[{'kind':'style','paragraph_index':11,'message':'未提供段落的猜测不能成为修订依据。'},
                        {'kind':'style','paragraph_index':2,'message':'实际给定范围内的表达问题。'}]})}
    monkeypatch.setattr(writing_flow,'_cached_call',response)
    from app import provider
    db.set_setting('provider',{**provider.DEFAULT,'context_limit':32000,'max_output_tokens':512})
    result=writing_flow._semantic(task,{'id':'section','title':'Synthetic'},paragraphs,context,{}, {'claim_checks':checks})
    assert result['claims'][0]['support']=='unknown' and result['claims'][1]['support']=='supported'
    assert result['point_reviews'][0]['status']=='unknown'
    assert not any(row.get('paragraph_index')==11 for row in result['findings'])
    assert any(row.get('kind')=='style' and row.get('paragraph_index')==2 for row in result['findings'])
    assert not result['global_coherence']['coverage_complete']


def candidate():
    return {'paragraphs':[{'text':'Correct original paragraph.','reference_ids':['r1'],
        'claim_spans':[{'local_id':'c1','text':'Correct original paragraph.'}],
        'citation_intents':[{'claim_id':'c1','reference_id':'r1'}]},
        {'text':'Incorrect assertion.','claim_spans':[{'local_id':'c2','text':'Incorrect assertion.'}]}],
        'missing_materials':[]}


def patch_response(value,approved):
    target=approved['paragraphs'][1]
    return {'schema':protocols.REVISION,'candidate_hash':approved['candidate_hash'],
            'patches':[{'paragraph_id':target['paragraph_id'],'old_hash':target['old_hash'],
                        'paragraph':{'text':'Scope corrected.','claim_spans':[{'local_id':'c2','text':'Scope corrected.'}]}}]}


def test_patch_changes_only_authorized_paragraph_and_does_not_mutate_original():
    value=candidate(); prior=deepcopy(value)
    approved=patches.contract(value,[{'paragraph_index':1}])
    updated,receipt=patches.apply(value,patch_response(value,approved),approved)
    assert updated['paragraphs'][0]==value['paragraphs'][0] and value==prior
    assert updated['paragraphs'][1]['text']=='Scope corrected.' and receipt['changed_paragraph_indices']==[1]
    assert receipt['unchanged_paragraphs_byte_preserved']


@pytest.mark.parametrize('change',['candidate_hash','old_hash','target','duplicate','claim','citation','top_level','full_rewrite'])
def test_patch_conflicts_are_atomic_and_cannot_expand_scope(change):
    value=candidate(); prior=deepcopy(value); approved=patches.contract(value,[{'paragraph_index':1}])
    response=patch_response(value,approved)
    if change=='candidate_hash':response['candidate_hash']='outdated'
    elif change=='old_hash':response['patches'][0]['old_hash']='outdated'
    elif change=='target':response['patches'][0]['paragraph_id']='paragraph-1'
    elif change=='duplicate':response['patches']*=2
    elif change=='claim':response['patches'][0]['paragraph']['claim_spans'][0]['local_id']='invented'
    elif change=='citation':response['patches'][0]['paragraph']['citation_intents']=[{'claim_id':'c2','reference_id':'unread'}]
    elif change=='top_level':response['paragraphs']=[]
    else:response=value
    with pytest.raises(ValueError,match='REVISION_PATCH_CONFLICT'):patches.apply(value,response,approved)
    assert value==prior


def test_dependency_closure_is_shadow_only_and_handles_cycles():
    claims=[{'claim_id':'a','paragraph_index':0,'source_ids':['s1'],'requires_claim_ids':['c']},
            {'claim_id':'b','paragraph_index':1,'requires_claim_ids':['a']},
            {'claim_id':'c','paragraph_index':2,'requires_claim_ids':['b']},
            {'claim_id':'d','paragraph_index':3,'source_ids':['other']}]
    shadow=protocols.dependency_shadow(claims,changed_source_ids=['s1'])
    assert shadow['affected_claim_ids']==['a','b','c']
    assert shadow['mode']=='shadow' and not shadow['may_skip_verification']


def reading_handle(client):
    pid=project(client); original=file(client,pid,'完整原文：仅限单用户。尚未测量并发性能。')
    parsed=units.build(pid,original['id']); unit=units.get(pid,parsed['units'][0]['id'])
    observation={'unit_id':unit['id'],'file_id':unit['file_id'],'parse_id':unit['parse_id'],
                 'unit_hash':unit['content_hash'],'char_start':3,'char_end':12,'text':unit['text'][3:12]}
    return pid,original,unit,evidence_ranges.freeze(pid,observation)


def test_immutable_read_range_roundtrip_keeps_exact_text_without_claiming_other_ranges(client):
    pid,_,unit,handle=reading_handle(client)
    assert 'text' not in handle
    expanded=evidence_ranges.resolve(pid,handle)
    assert expanded['text']==unit['text'][3:12]
    assert (expanded['char_start'],expanded['char_end'])==(3,12)


@pytest.mark.parametrize('change',['foreign','tamper','permission','version'])
def test_read_range_resolve_always_rechecks_identity_version_and_permission(client,change):
    pid,original,unit,handle=reading_handle(client)
    if change=='foreign':pid='foreign-project'
    elif change=='tamper':handle['char_end']+=1
    elif change=='permission':db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    else:db.execute('UPDATE sources SET text=? WHERE file_id=?',('Changed original conditions.',original['id']))
    with pytest.raises((ValueError,HTTPException)):
        evidence_ranges.resolve(pid,handle)


def test_saved_response_benchmark_reuses_eight_dimensions_without_human_approval():
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('offline_quality',root/'scripts/benchmark_writing_quality.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    dataset=json.loads((root/'prompts/writing-quality-scenarios.v1.json').read_text(encoding='utf-8'))
    result=module.report(dataset,[{'case_id':'insufficient','response':{'support':'unknown','measured':False}}])
    assert len(result['rubric'])==8 and result['summary']['cases']==6
    assert result['summary']['external_model_calls']==0 and result['summary']['contract_checks_passed']==2
    assert all(not case['human_approval'] and case['semantic_status']=='unassessed' for case in result['cases'])
    metrics=module.observed_metrics({'usage':{'input_tokens':True,'output_tokens':12},
        'cost':{'amount':float('nan'),'currency':'USD'},'duration_seconds':-1})
    assert metrics['input_tokens'] is None and metrics['output_tokens']==12
    assert metrics['cost'] is None and metrics['duration_seconds'] is None


def test_new_range_annotations_do_not_invalidate_unchanged_legacy_recovery_identity():
    manifest={'section_id':'same','knowledge_unit_versions':{'u':'hash'}}
    assert writing_flow._cached_input_manifest(manifest)==writing_flow._cached_input_manifest(
        {**manifest,'evidence_range_handles':[{'schema':'evidence.range.v1','hash':'dual-record'}]})


@pytest.mark.parametrize('field,value',[('evidence_range_schema','invented'),('dependency_projection_mode','enforce')])
def test_unsupported_range_schema_or_enforced_dependency_projection_is_rejected(client,field,value):
    with pytest.raises(HTTPException):ai_cache_policy.freeze_payload('write',{field:value})


@pytest.mark.parametrize('valid_patch',[True,False])
@pytest.mark.parametrize('scoped',[False,True])
def test_finish_applies_frozen_patch_or_retains_original_without_full_rewrite(writing_project,monkeypatch,valid_patch,scoped):
    from test_writing_flow_safety import _running, _basis
    from app import provider
    env=writing_project
    writing_flow.save_settings(env['pid'],{'max_revision_rounds':1,'originality_check':False})
    task=_running(env,verification_scope_version=protocols.VERIFICATION if scoped else 'legacy_full',revision_protocol_version=protocols.REVISION)
    context,manifest=_basis(env);sid=context['sources'][0]['id']
    original=tasks.candidate_from_text(db.dumps({'paragraphs':[
        {'text':'模块职责清晰有助于维护。','source_ids':[sid]},
        {'text':'所有系统都能自动完成全部研究。','source_ids':[sid]}]}))[0]
    checks=0;patch_calls=[]
    def transport(system,user,images=None):
        nonlocal checks
        value=db.loads(user)
        if value.get('operation')=='review_candidate_discourse':
            return response({'coverage_complete':True,'findings':[]})
        if 'claim_reviews' in system:
            checks+=1;source=value['actual_sources'][0]
            return response({'claim_reviews':[{'claim_id':claim['claim_id'],
                'support':'unsupported' if checks==1 and claim['paragraph_index']==1 else 'supported',
                'source_quotes':[{'source_id':source['id'],'quote':source['text']}]} for claim in value['claims']],
                'point_reviews':[],'findings':[]})
        approved=value['revision_patch_contract'];patch_calls.append(approved)
        if not valid_patch:return response(original)
        target=approved['paragraphs'][1]
        paragraph={**original['paragraphs'][1],'text':'本文仅说明模块职责。'}
        return response({'schema':protocols.REVISION,'candidate_hash':approved['candidate_hash'],
            'patches':[{'paragraph_id':target['paragraph_id'],'old_hash':target['old_hash'],'paragraph':paragraph}]})
    monkeypatch.setattr(provider,'generate',transport)
    current,_,_,report,_=writing_flow.finish(task,security.require_section(env['sid']),original,'Synthetic writer.',
                                           context,manifest,tasks.candidate_from_text)
    assert len(patch_calls)==1 and current['paragraphs'][0]==original['paragraphs'][0]
    if scoped:
        assert not report['coverage_complete'] and report['status']!='checked'
        assert not report['semantic']['global_coherence']['coverage_complete']
        assert any(finding['kind']=='global_coherence_not_checked' for finding in report['findings'])
    if valid_patch:
        assert report['repair_rounds'][0]['accepted'],report['repair_rounds']
        assert current['paragraphs'][1]['text']=='本文仅说明模块职责。'
    else:
        assert current==original and not report['repair_rounds'][0]['accepted']
        assert any(finding.get('kind')=='revision_rejected' for finding in report['findings'])


def test_late_whole_paper_checkpoint_and_recovery_cannot_overwrite_successor(client):
    from test_writing_recovery_content_cache import setup
    from app import paper_writing, task_transitions
    task,_,_,_=setup(client)
    db.execute('UPDATE tasks SET attempt=attempt+1,result=? WHERE id=?',(db.dumps({'successor':True}),task['id']))
    with task_transitions.ownership(task):
        with pytest.raises(InterruptedError):paper_writing.checkpoint(task['id'],{'stale':True})
    assert paper_writing.recover(task) is False
    assert db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'])=={'successor':True}

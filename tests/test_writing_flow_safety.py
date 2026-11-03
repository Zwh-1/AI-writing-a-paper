"""Independent failure-injection coverage of writing checkpoints and adoption.

All sources, credentials and transports come from an isolated synthetic project.
No request reaches a provider or the user's project database.
"""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, response, write  # noqa: F401
from app import db,provider,security,tasks,writing_flow,writing_retrieval
from app.summary_billing import BudgetExceeded
from app.writing_agent.common import unblob,digest


def _running(e, **payload):
    task=tasks.enqueue(e['pid'],'write',{'section_id':e['sid'],'base_revision_id':None,'operation':'generate',**payload})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    return db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))


def _basis(e):
    source=db.one('SELECT * FROM sources WHERE file_id=? ORDER BY id LIMIT 1',(e['file']['id'],))
    context={'operation':'generate','project':{'title':'合成论文'},'sources':[source],'references':[e['ref']],
             'facts':[],'project_evidence':[],'knowledge_units':[],'document_readings':[],
             'writing_brief':{'points':[]}}
    manifest={'section_id':e['sid'],'base_revision_id':None,
              'rule_package_id':security.require_project(e['pid'])['rule_package_id'],'file_ids':[e['file']['id']]}
    return context,manifest


def _unit_setup(e):
    writing_flow.save_settings(e['pid'],{'unit_threshold_words':1200,'unit_target_words':1000,'originality_check':False})
    section={**security.require_section(e['sid']),'target_words':3000}
    context,manifest=_basis(e)
    return _running(e),section,context,manifest


def test_units_resume_completed_unit_without_resending_it(writing_project,monkeypatch):
    e=writing_project;task,section,context,manifest=_unit_setup(e)
    calls=[];interrupted=False
    def transport(system,user,images=None):
        nonlocal interrupted
        payload=db.loads(user);index=payload['argument_unit']['index'];calls.append(index)
        if index==2 and not interrupted:
            interrupted=True;raise provider.ProviderError('synthetic confirmed request rejection',unknown=False)
        return response({'paragraphs':[{'text':f'第{index}个完整论证单元。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    with pytest.raises(provider.ProviderError):
        writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)
    step=db.one("SELECT * FROM writing_steps WHERE task_id=? AND stage='argument_units'",(task['id'],))
    assert len(unblob(step['output_blob'])['units'])==1
    result=writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)
    assert calls==[1,2,2,3]
    assert [p['text'] for p in db.loads(result['text'])['paragraphs']]==['第1个完整论证单元。','第2个完整论证单元。','第3个完整论证单元。']
    assert manifest['argument_units']['checkpointed']


def test_unknown_unit_result_is_not_automatically_sent_again(writing_project,monkeypatch):
    e=writing_project;task,section,context,manifest=_unit_setup(e)
    calls=[]
    def transport(system,user,images=None):
        index=db.loads(user)['argument_unit']['index'];calls.append(index)
        if index==2:raise provider.ProviderError('synthetic remote result unknown',unknown=True)
        return response({'paragraphs':[{'text':'第一个完整论证单元。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    for attempt in range(2):
        with pytest.raises(provider.ProviderError) as error:
            writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)
        assert error.value.unknown
    assert calls==[1,2]
    assert db.one("SELECT COUNT(*) n FROM generation_runs WHERE task_id=? AND status='outcome_unknown'",(task['id'],))['n']==1


def test_unresolved_claims_from_all_units_survive_chapter_assembly(writing_project,monkeypatch):
    e=writing_project;task,section,context,manifest=_unit_setup(e)
    def transport(system,user,images=None):
        index=db.loads(user)['argument_unit']['index']
        return response({'paragraphs':[{'text':f'第{index}个完整论证单元，结果待补。'}],
                         'unresolved_claims':[f'缺口{index}'],'missing_materials':[f'需要原始记录{index}']})
    monkeypatch.setattr(provider,'generate',transport)
    result=writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)
    candidate=db.loads(result['text'])
    assert candidate['unresolved_claims']==['缺口1','缺口2','缺口3']
    assert len(candidate['missing_materials'])==3


def test_cancelled_parent_cannot_send_next_unit(writing_project,monkeypatch):
    e=writing_project;task,section,context,manifest=_unit_setup(e)
    db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?',(task['id'],))
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('cancelled task sent a model request'))
    with pytest.raises(InterruptedError):
        writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)


def test_budget_blocked_unit_keeps_checkpoint_and_does_not_send_more(writing_project,monkeypatch):
    e=writing_project;task,section,context,manifest=_unit_setup(e)
    calls=[]
    def transport(system,user,images=None):
        index=db.loads(user)['argument_unit']['index'];calls.append(index)
        if index==1:db.execute('UPDATE projects SET budget=0 WHERE id=?',(e['pid'],))
        return response({'paragraphs':[{'text':'已完成且已计费的第一单元。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    with pytest.raises(BudgetExceeded):
        writing_flow.generate(task,section,'synthetic',context,manifest,tasks.candidate_from_text)
    assert calls==[1]
    step=db.one("SELECT output_blob FROM writing_steps WHERE task_id=? AND stage='argument_units'",(task['id'],))
    assert len(unblob(step['output_blob'])['units'])==1


def test_completed_cache_reuse_still_rechecks_exact_bundle_permissions(writing_project,monkeypatch):
    e=writing_project;task=_running(e);context,manifest=_basis(e)
    bundle=writing_retrieval.prepare(e['pid'],e['sid'],'模块职责维护性')
    writing_flow._manifest_bundle(manifest,bundle)
    calls=[]
    def transport(*args,**kwargs):calls.append(True);return response({'paragraphs':[{'text':'模块职责说明。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    first=writing_flow._cached_call(task,'cache-safety',context,manifest,stage='test')
    second=writing_flow._cached_call(task,'cache-safety',context,manifest,stage='test')
    assert first['text']==second['text'] and second['cache_hit'] and len(calls)==1
    e['client'].patch('/api/v1/files/'+e['file']['id'],json={'egress':'local_only'})
    with pytest.raises(HTTPException):
        writing_flow._cached_call(task,'cache-safety',context,manifest,stage='test')
    assert len(calls)==1


def test_simple_unlinked_fact_without_material_is_incomplete_not_passed(writing_project,monkeypatch):
    e=writing_project;task=_running(e);section=security.require_section(e['sid'])
    writing_flow.save_settings(e['pid'],{'max_revision_rounds':0,'originality_check':False})
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('no sources should not create a fake semantic pass'))
    candidate={'paragraphs':[{'text':'系统采用三层架构。'}]}
    _,_,_,report,_=writing_flow.finish(task,section,candidate,'synthetic',{'operation':'generate'}, {},tasks.candidate_from_text)
    assert not report['coverage_complete']
    assert report['semantic']['status']=='incomplete'
    assert report['status']!='checked'


def test_disabled_semantic_review_is_not_reported_as_full_quality_pass(writing_project,monkeypatch):
    e=writing_project;task=_running(e);context,manifest=_basis(e)
    writing_flow.save_settings(e['pid'],{'semantic_review':False,'max_revision_rounds':0,'originality_check':False})
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('disabled semantic review sent a model request'))
    candidate={'paragraphs':[{'text':'本文说明研究安排。'}]}
    _,_,_,report,_=writing_flow.finish(task,security.require_section(e['sid']),candidate,'synthetic',context,manifest,tasks.candidate_from_text)
    assert report['semantic']['status']=='not_checked'
    assert not report['coverage_complete']
    assert report['status']!='checked'


def test_report_fingerprint_identifies_final_candidate_including_unresolved_gaps(writing_project,monkeypatch):
    e=writing_project;task=_running(e)
    writing_flow.save_settings(e['pid'],{'max_revision_rounds':0,'originality_check':False})
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('missing evidence should remain explicit'))
    candidate=tasks.candidate_from_text(db.dumps({'paragraphs':[{'text':'系统采用三层架构。'}],'unresolved_claims':['缺少实现原文']}))[0]
    current,_,_,report,_=writing_flow.finish(task,security.require_section(e['sid']),candidate,'synthetic',{}, {},tasks.candidate_from_text)
    assert '缺少实现原文' in current['missing_materials']
    assert report['candidate_hash']==digest(current)


def test_independent_review_cannot_mark_task_point_complete_without_real_body_excerpt(writing_project,monkeypatch):
    from app.writing_evidence_quality import check_candidate
    e=writing_project;task=_running(e);context,manifest=_basis(e)
    context['writing_brief']={'points':[{'id':'required-point','text':'说明模块维护边界','required':True}]}
    candidate=[{'text':'模块职责清晰有助于维护。','source_ids':[context['sources'][0]['id']]}]
    def transport(system,user,images=None):
        request=db.loads(user);source=request['actual_sources'][0]
        return response({'claim_reviews':[{'claim_id':c['claim_id'],'support':'supported',
            'source_quotes':[{'source_id':source['id'],'quote':source['text']}],'reason':'实际原文可核对'} for c in request['claims']],
            'point_reviews':[{'point_id':'required-point','status':'complete','paragraph_indices':[0],
                              'evidence_text':'正文中根本不存在的完整论证','reason':'不能仅相信完成声明'}],'findings':[]})
    monkeypatch.setattr(provider,'generate',transport)
    local=check_candidate(e['pid'],candidate,context,manifest)
    reviewed=writing_flow._semantic(task,security.require_section(e['sid']),candidate,context,manifest,local)
    assert reviewed['coverage_complete']
    assert not reviewed['task_coverage']['complete']
    assert reviewed['point_reviews'][0]['status']=='unknown'


def test_forged_source_excerpt_never_grants_supported_state(writing_project,monkeypatch):
    from app.writing_evidence_quality import check_candidate
    e=writing_project;task=_running(e);context,manifest=_basis(e)
    candidate=[{'text':'模块职责清晰有助于维护。','source_ids':[context['sources'][0]['id']]}]
    def transport(system,user,images=None):
        request=db.loads(user)
        return response({'claim_reviews':[{'claim_id':c['claim_id'],'support':'supported',
            'source_quotes':[{'source_id':request['actual_sources'][0]['id'],'quote':'凭空编造的性能提升100%'}],
            'reason':'声称支持但原文不存在'} for c in request['claims']],'point_reviews':[],'findings':[]})
    monkeypatch.setattr(provider,'generate',transport)
    local=check_candidate(e['pid'],candidate,context,manifest)
    reviewed=writing_flow._semantic(task,security.require_section(e['sid']),candidate,context,manifest,local)
    assert reviewed['claims'][0]['support']=='unknown'
    assert not reviewed['claims'][0]['quote_verified']
    assert any(f['kind']=='source_support_unknown' for f in reviewed['findings'])


@pytest.mark.parametrize('recalculate_hash',[False,True])
def test_dynamic_bundle_must_match_immutable_server_read_receipt(writing_project,recalculate_hash):
    from app import resource_refs
    e=writing_project
    bundle=writing_retrieval.prepare(e['pid'],e['sid'],'模块维护')
    forged=deepcopy(bundle)
    forged['query']='伪造的新读取问题'
    if recalculate_hash:forged['hash']=resource_refs.digest({k:v for k,v in forged.items() if k!='hash'})
    with pytest.raises(HTTPException) as error:
        writing_flow._manifest_bundle({},forged)
    assert error.value.status_code==409


def test_dynamic_bundle_return_patch_is_independent_and_merge_keeps_old_source_ids(writing_project):
    e=writing_project
    bundle=writing_retrieval.prepare(e['pid'],e['sid'],'模块维护')
    patch=writing_retrieval.for_writing(bundle)
    original=deepcopy(bundle['context_patch'])
    assert patch['sources']
    patch['sources'][0]['text']='仅修改调用方副本'
    assert bundle['context_patch']==original
    context={'sources':[{'id':'prior-source','text':'保留的旧读范围','file_id':e['file']['id']}]}
    writing_flow.merge_context(context,writing_retrieval.for_writing(bundle))
    assert any(s['id']=='prior-source' for s in context['sources'])
    assert all(s['text']!='仅修改调用方副本' for s in context['sources'])


def _repair_scenario(e,monkeypatch,mode):
    writing_flow.save_settings(e['pid'],{'max_revision_rounds':1,'originality_check':False})
    task=_running(e);section=security.require_section(e['sid']);context,manifest=_basis(e)
    sid=context['sources'][0]['id']
    correct={'text':'模块职责清晰有助于维护。','source_ids':[sid],
             'claim_spans':[{'local_id':'c1','text':'模块职责清晰有助于维护。','source_ids':[sid]}],
             'citation_intents':[{'claim_id':'c1','reference_id':e['ref']['id'],'source_ids':[sid]}]}
    wrong={'text':'所有系统都能自动完成全部研究。','source_ids':[sid]}
    candidate=tasks.candidate_from_text(db.dumps({'paragraphs':[correct,wrong]}))[0]
    if mode=='dynamic':candidate['retrieval_requests']=[{'query':'补读当前材料的适用边界'}]
    count=0
    def transport(system,user,images=None):
        nonlocal count
        payload=db.loads(user)
        if 'claim_reviews' in system:
            count+=1
            source=payload['actual_sources'][0]
            return response({'claim_reviews':[{'claim_id':r['claim_id'],
                'support':'unsupported' if count==1 and r['paragraph_index']==1 else 'supported',
                'source_quotes':[{'source_id':source['id'],'quote':source['text']}],
                'reason':'第二项超出材料支持范围' if count==1 and r['paragraph_index']==1 else '材料支持当前范围'} for r in payload['claims']],
                'point_reviews':[],'findings':[]})
        revised=deepcopy(candidate)
        revised['retrieval_requests']=[]
        revised['paragraphs'][1]['text']='本文仅说明模块职责。'
        if mode=='delete_correct':revised['paragraphs']=revised['paragraphs'][1:]
        elif mode=='drop_citation':revised['paragraphs'][0]['citation_intents']=[]
        elif mode=='drop_claim_source':
            revised['paragraphs'][0]['source_ids']=[]
            revised['paragraphs'][0]['claim_spans'][0]['source_ids']=[]
            revised['paragraphs'][0]['citation_intents'][0]['source_ids']=[]
        return response(revised)
    monkeypatch.setattr(provider,'generate',transport)
    result=writing_flow.finish(task,section,candidate,'synthetic',context,manifest,tasks.candidate_from_text)
    return candidate,result


@pytest.mark.parametrize('mode',['delete_correct','drop_citation','drop_claim_source'])
def test_repair_cannot_improve_by_losing_correct_claim_or_its_precise_citation(writing_project,monkeypatch,mode):
    candidate,result=_repair_scenario(writing_project,monkeypatch,mode)
    current,_,_,report,_=result
    assert report['repair_rounds'] and not report['repair_rounds'][0]['accepted']
    assert current['paragraphs'][0]==candidate['paragraphs'][0]


def test_repair_that_preserves_correct_claim_and_citation_can_improve(writing_project,monkeypatch):
    candidate,result=_repair_scenario(writing_project,monkeypatch,'valid')
    current,context,manifest,report,bundle=result
    assert report['repair_rounds'][0]['accepted'],report
    assert current['paragraphs'][0]==candidate['paragraphs'][0]
    assert bundle and manifest['evidence_bundle_hash']==bundle['hash']
    assert set(manifest['source_ids'])=={s['id'] for s in context['sources']}
    assert manifest['document_reading_versions']=={r['reading_id']:r['version'] for r in context.get('document_readings',[])}
    assert manifest['knowledge_unit_versions']=={u.get('unit_id') or u['id']:u.get('unit_hash') or u.get('content_hash') for u in context.get('knowledge_units',[])}


@pytest.mark.parametrize('partial',[False,True])
def test_server_quality_blocker_cannot_be_bypassed_by_client_or_partial_adoption(writing_project,partial):
    e=writing_project;task,result=write(e)
    result['manifest']['writing_quality_blockers']=[{'kind':'result_evidence_level','message':'合成关键等级错误'}]
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(result),task['id']))
    body={'task_id':task['id'],'base_revision_id':None,
          'manifest':{'writing_quality_blockers':[]},'writing_quality_blockers':[]}
    if partial:body.update(node_ids=[result['document']['content'][0]['attrs']['nodeId']],mode='append')
    response_=e['client'].post(f"/api/v1/sections/{e['sid']}/accept",json=body)
    assert response_.status_code==409,response_.text
    assert response_.json()['detail']['code']=='WRITING_EVIDENCE_UNRESOLVED'
    assert not db.rows('SELECT * FROM revisions')


def test_rule_changes_between_generation_and_adoption_are_not_silently_rebased(writing_project):
    e=writing_project;task,_=write(e)
    e['client'].post(f"/api/v1/projects/{e['pid']}/rules",json={'statement':'新增合成强制规则','modality':'mandatory','scope':'global'})
    published=e['client'].post(f"/api/v1/projects/{e['pid']}/rule-packages")
    assert published.status_code==200,published.text
    result=e['client'].post(f"/api/v1/sections/{e['sid']}/accept",json={'task_id':task['id'],'base_revision_id':None})
    assert result.status_code==409
    assert not db.rows('SELECT * FROM revisions')


def test_dynamic_material_bundle_is_merged_with_static_context_and_returned_seal(writing_project,monkeypatch):
    from app import writing_tool_runtime
    from test_workbench import file
    e=writing_project
    extra=file(e['client'],e['pid'],'本次补充材料限定：模块维护结论仅适用于清晰接口边界。')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(extra['id'],))
    # This is a sealed real read receipt; only the agent orchestration is mocked.
    dynamic_bundle=writing_retrieval.prepare(e['pid'],e['sid'],'本次补充材料限定接口边界')
    assert any(row.get('file_id')==extra['id'] for row in dynamic_bundle['context_patch']['sources'])
    def collect(*args,**kwargs):
        return {'material_bundles':[dynamic_bundle],'evidence_ids':[], 'tool_observations':[{'id':'synthetic-read'}],
                'context':{'dynamic_research':{'status':'completed','summary':'实际补读范围已返回'}},'manifest':{}}
    monkeypatch.setattr(writing_tool_runtime,'collect',collect)
    _,result=_repair_scenario(e,monkeypatch,'dynamic')
    current,context,manifest,report,bundle=result
    assert report['tool_calls']==1
    assert any(s['id']==source['id'] for s in context['sources'])
    assert any(s['id']==source['id'] for s in bundle['context_patch']['sources'])
    assert bundle['hash']==manifest['evidence_bundle_hash']==manifest['evidence_bundle']['hash']
    assert context['evidence_bundle']['bundle_id']==bundle['bundle_id']
    assert writing_retrieval.validate(e['pid'],bundle)


def _partial_unit_observations(e):
    from app import knowledge_units as units
    parsed=units.build(e['pid'],e['file']['id'])
    unit=units.get(e['pid'],parsed['units'][0]['id'],remote=True)
    first={**unit,'unit_id':unit['id'],'unit_hash':unit['content_hash'],'text':unit['text'][:8],
           'char_start':0,'char_end':8,'scope':'question_bound_reading_range','complete':False}
    last={**unit,'unit_id':unit['id'],'unit_hash':unit['content_hash'],'text':unit['text'][-8:],
          'char_start':len(unit['text'])-8,'char_end':len(unit['text']),'scope':'question_bound_reading_range','complete':False}
    return unit,first,last


def test_disjoint_unit_ranges_survive_context_and_manifest_without_inventing_middle(writing_project):
    from app.writing_evidence_quality import compile_paragraph
    e=writing_project;unit,first,last=_partial_unit_observations(e)
    context={'knowledge_units':[first],'references':[e['ref']]}
    writing_flow.merge_context(context,{'knowledge_units':[last]})
    assert len(context['knowledge_units'])==1
    combined=context['knowledge_units'][0]
    assert len(combined['read_observations'])==2 and not combined['complete']
    assert first['text'] in combined['text'] and last['text'] in combined['text']
    assert unit['text'][8:-8] not in combined['text']
    manifest=writing_flow.sync_manifest(e['pid'],context,{})
    ranges=manifest['knowledge_read_scopes'][unit['id']]['reading_ranges']
    assert {(r['char_start'],r['char_end']) for r in ranges}=={(0,8),(len(unit['text'])-8,len(unit['text']))}
    raw={'text':'研究原文有明确适用范围。','claim_spans':[{'local_id':'c1','text':'研究原文有明确适用范围。','knowledge_unit_ids':[unit['id']]}],
         'citation_intents':[{'claim_id':'c1','reference_id':e['ref']['id'],'quote':first['text']}]}
    compiled=compile_paragraph(e['pid'],raw,context,manifest)
    assert compiled['citation_proposals'] and not compiled['findings']
    scope=compiled['citation_proposals'][0]['knowledge_read_scopes'][unit['id']]
    assert {(r['char_start'],r['char_end']) for r in scope['reading_ranges']}=={(0,8),(len(unit['text'])-8,len(unit['text']))}
    raw['citation_intents'][0]['quote']=unit['text'][8:-8]
    assert not compile_paragraph(e['pid'],raw,context,manifest)['citation_proposals']


def test_compact_source_identity_is_not_read_permission_for_unread_same_file_range(writing_project):
    from app.writing_evidence_quality import compile_paragraph
    e=writing_project;unit,first,last=_partial_unit_observations(e)
    source=db.one('SELECT * FROM sources WHERE file_id=?',(e['file']['id'],))
    context={'knowledge_units':[first,last], 'references':[e['ref']],
             'sources':[{'id':source['id'],'file_id':e['file']['id'],'text_in_knowledge_units':[unit['id']]}]}
    raw={'text':'研究文献说明模块维护。','claim_spans':[{'local_id':'c1','text':'研究文献说明模块维护。','source_ids':[source['id']]}],
         'citation_intents':[{'claim_id':'c1','reference_id':e['ref']['id']}]}
    result=compile_paragraph(e['pid'],raw,context,{'knowledge_unit_versions':{unit['id']:unit['content_hash']}})
    assert not result['citation_proposals']
    assert any(f['kind']=='claim_mapping_invalid' for f in result['findings'])


def test_retained_candidate_provenance_survives_sync_without_expanding_new_claim_allowlist(writing_project):
    from app import document_reading as reader
    from app.writing_agent import evidence
    from app.writing_evidence_quality import compile_paragraph
    e=writing_project;unit,first,last=_partial_unit_observations(e)
    source=db.one('SELECT * FROM sources WHERE file_id=?',(e['file']['id'],))
    observed=evidence.sync_source(e['pid'],source['id'],'literature')
    fact=e['client'].post(f"/api/v1/projects/{e['pid']}/facts",json={'label':'原始事实','statement':'登记材料范围','status':'planned'}).json()
    opened=reader.create(e['pid'],{'file_id':e['file']['id'],'section_id':e['sid'],'question':'原候选的已读范围'})
    reader.read_range(e['pid'],opened['id'],{'ordinal_start':1,'ordinal_end':1})
    reading=reader.context(e['pid'],opened['id'])
    prior={'retained_candidate_provenance':True,'source_ids':[source['id']],
           'evidence_versions':{observed['id']:observed['origin_version']},
           'permission_stamps':{observed['id']:evidence.permission_stamp(e['pid'],observed['id'])},
           'fact_revisions':{fact['id']:fact['revision']},'knowledge_unit_versions':{unit['id']:unit['content_hash']},
           'document_reading_versions':{opened['id']:reading['version']},
           'knowledge_read_scopes':{unit['id']:{'passage_ids':[unit['id']],'reading_ranges':[{'unit_id':unit['id'],'char_start':0,'char_end':8}]}},
           'writing_state':{}}
    before=deepcopy(prior)
    context={'sources':[],'project_evidence':[],'facts':[],'knowledge_units':[],'document_readings':[],'references':[e['ref']]}
    result=writing_flow.sync_manifest(e['pid'],context,prior)
    for key in ('source_ids','evidence_versions','permission_stamps','fact_revisions','knowledge_unit_versions','document_reading_versions','knowledge_read_scopes'):
        assert result[key]==before[key],key
    assert context['sources']==[] and context['project_evidence']==[]
    raw={'text':'新段落不能借旧原件授权。','claim_spans':[{'local_id':'c1','text':'新段落不能借旧原件授权。','source_ids':[source['id']]}],
         'citation_intents':[{'claim_id':'c1','reference_id':e['ref']['id']}]}
    assert not compile_paragraph(e['pid'],raw,context,result)['citation_proposals']

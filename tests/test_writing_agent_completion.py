"""Completion regressions: actual state/transaction invariants, not fluent fixture scores."""
import json
import pytest
from fastapi import HTTPException
from test_writing_agent import env,start,finish,fake_model,reviewed_candidate
from app import db,documents,tasks,provider,security
import app.main as main
from app.writing_agent import adoption,impact,contracts,evidence,coordinator,verification,revision,joint,evaluations,diagnostic_scoring as scoring,diagnostic_comparisons as comparisons
from app.writing_agent.common import state,blob,unblob,digest

def test_annotation_accepts_only_explicitly_empty_known_note():
    value={'sentences':[{'sentence_id':'s1','role':'fact','claims':[{'statement':'尚未执行测试','evidence_ids':['e1'],'support':'unknown','conditions':['仅有定义']}],'claims_note':None}],'coverage_complete':True}
    parsed=verification.Annotation.model_validate(value).model_dump()
    assert parsed['sentences'][0]['claims_note'] is None
    assert parsed['sentences'][0]['claims'][0]['support']=='unknown'
    assert parsed['sentences'][0]['claims'][0]['conditions']==['仅有定义']
    assert parsed['sentences'][0]['claims'][0]['evidence_ids']==['e1']


def test_annotation_rejects_nonempty_note_and_unknown_empty_extension():
    from pydantic import ValidationError
    for extra in ({'claims_note':'这里还有一个未映射的事实'},{'claims_note':{}},{'unexpected_note':None}):
        with pytest.raises(ValidationError):verification.Annotation.model_validate({'sentences':[{'sentence_id':'s1',**extra}]})


def test_empty_annotation_note_cannot_bypass_execution_evidence_guard(env):
    from app.writing_agent import sentences
    revision_row=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('测试已经通过了。'),None)
    analysis=sentences.analyze(env['pid'],db.loads(revision_row['document']),revision_id=revision_row['id'])
    span=sentences.get(env['pid'],analysis['id'])['sentences'][0]
    code=evidence.create(env['pid'],{'kind':'code_observation','origin_type':'code','origin_id':env['snapshot']['id'],'locator':{'path':'gate.ts','line_start':1,'line_end':1}})
    _,result=verification.apply_annotation(env['pid'],analysis['id'],{'sentences':[{'sentence_id':span['id'],'role':'fact','claims_note':None,'claims':[{'statement':'测试已经通过了。','evidence_ids':[code['id']],'support':'supported'}]}],'coverage_complete':True},{'evidence_versions':{code['id']:code['origin_version']}})
    assert any(f['kind']=='evidence_level' and f['severity']=='blocker' for f in result['findings'])


def test_version_six_upgrades_without_resetting_rows(env,monkeypatch):
    from app.writing_agent import migrations
    original_files,original_current=migrations.FILES,migrations.CURRENT
    directory=env['path']/'v6-upgrade';directory.mkdir();monkeypatch.setattr(db,'DB_PATH',directory/'workbench.sqlite3')
    monkeypatch.setattr(migrations,'FILES',tuple(f for f in original_files if f[0]<=6));monkeypatch.setattr(migrations,'CURRENT',6);db.init()
    db.set_setting('kept-user-value',{'text':'旧资料不能丢失'})
    assert db.one('PRAGMA user_version')['user_version']==6
    monkeypatch.setattr(migrations,'FILES',original_files);monkeypatch.setattr(migrations,'CURRENT',original_current);db.init();db.init()
    assert db.one('PRAGMA user_version')['user_version']==9
    assert db.setting('kept-user-value')=={'text':'旧资料不能丢失'}
    assert (directory/'migration-before-writing-v9.sqlite3').is_file()
    assert not db.rows('PRAGMA foreign_key_check')

def test_commit_snapshot_is_distinct_from_dirty_working_tree(env):
    import subprocess,shutil
    from app.writing_agent import repository
    git=shutil.which('git.exe') or shutil.which('git');assert git,'Git is required for the commit snapshot acceptance case'
    for args in (['init'],['add','gate.ts','AGENTS.md'],['-c','user.name=Isolated fixture','-c','user.email=fixture@example.invalid','commit','-m','frozen fixture']):
        result=subprocess.run([git,*args],cwd=env['root'],capture_output=True,timeout=20)
        assert result.returncode==0
    (env['root']/'gate.ts').write_text('export const changed = true;\n',encoding='utf-8')
    committed=repository.capture(env['pid'],env['binding']['id'],'git_commit','HEAD')
    working=repository.capture(env['pid'],env['binding']['id'],'working_tree')
    assert 'issuer && enabled' in repository.read(env['pid'],committed['id'],'gate.ts')['text']
    assert 'changed = true' in repository.read(env['pid'],working['id'],'gate.ts')['text']
    assert committed['manifest']['atomic_filesystem_snapshot'] is True
    assert working['manifest']['atomic_filesystem_snapshot'] is False

def test_registered_check_unavailable_never_creates_success_evidence(env,monkeypatch):
    from app.writing_agent import check_registry
    from app import controlled_execution
    assert check_registry.execute(env['pid'],'missing',env['snapshot']['id'])['status']=='not_run'
    check=check_registry.register(env['pid'],{'kind':'node_test','name':'离线登记测试','image':'sha256:'+'a'*64})
    registered=db.setting('writing_checks:'+env['pid']);registered[check['id']]['enabled']=True;db.set_setting('writing_checks:'+env['pid'],registered)
    monkeypatch.setattr(controlled_execution,'capabilities',lambda:{'docker_engine':False})
    assert check_registry.execute(env['pid'],check['id'],env['snapshot']['id'])['status']=='unavailable'
    assert not db.rows('SELECT * FROM artifacts')

def test_import_alias_and_parse_gaps_are_distinct_from_missing_function(env):
    from app.writing_agent import repository,code_index
    (env['root']/'entry.ts').write_text('import { allowed as predicate } from "./gate";\nexport function check() { return predicate(true,true); }\n',encoding='utf-8')
    (env['root']/'broken.ts').write_text('export function incomplete( {',encoding='utf-8')
    snap=repository.capture(env['pid'],env['binding']['id'])
    file=db.one("SELECT id FROM code_units WHERE snapshot_id=? AND relative_path='entry.ts' AND kind='file'",(snap['id'],))
    resolved=code_index.resolve(env['pid'],snap['id'],file['id'])
    edge=next(e for e in resolved['edges'] if e['resolution_state']=='candidate_import_alias')
    assert db.one('SELECT symbol FROM code_units WHERE id=?',(edge['to_unit_id'],))['symbol']=='allowed'
    assert repository.read(env['pid'],snap['id'],'broken.ts')['parse_state']=='syntax_with_gaps'
    assert snap['index_limitations'] and any(x['parse_state']=='syntax_with_gaps' for x in snap['parse_states'])

def test_native_junction_does_not_escape_frozen_scope(env):
    import sys
    if sys.platform!='win32':pytest.skip('Windows junction acceptance uses the native Windows API')
    import _winapi
    from app.writing_agent import repository
    outside=env['path']/'outside';outside.mkdir();(outside/'outside-secret.ts').write_text('export const should_not_read=true;',encoding='utf-8')
    _winapi.CreateJunction(str(outside),str(env['root']/'linked'))
    snap=repository.capture(env['pid'],env['binding']['id'])
    assert not any('outside-secret' in r['path'] or r['path'].startswith('linked/') for r in snap['manifest']['files'])
    with pytest.raises(HTTPException):repository.read(env['pid'],snap['id'],'linked/outside-secret.ts')

def test_mandatory_context_over_budget_stops_without_saving_truncated_input(env):
    from app.writing_agent import context
    session=start(env);step=session['steps'][0]
    payload={'rules':['必须保持的条件。'*5000],'evidence':[]}
    with pytest.raises(HTTPException) as error:context.save_context(step['id'],payload,{},'强制规则不能丢弃',{'context_limit':2000,'max_output_tokens':1000})
    assert error.value.detail['code']=='CONTEXT_BUDGET_EXCEEDED'
    assert not db.rows('SELECT * FROM writing_contexts')

def test_exact_shared_code_preserves_conditions_and_required_canonical_text():
    from app.writing_agent import context
    raw=[{'id':'whole','kind':'code_observation','conditions':['全文条件'],'payload':{'text':'one\ntwo\nthree\n','path':'x.ts','file_sha256':'same','line_start':1,'line_end':3}},{'id':'slice','kind':'code_observation','conditions':['片段条件'],'payload':{'text':'two\n','path':'x.ts','file_sha256':'same','line_start':2,'line_end':2}}]
    shared=context.share_exact_code_text(raw)
    assert shared[1]['payload']['exact_text_reference']['evidence_id']=='whole'
    assert shared[1]['conditions']==['片段条件'] and 'text' not in shared[1]['payload']
    selected=context.select_evidence(shared,{'slice'})
    assert {e['id'] for e in selected}=={'slice','whole'} and selected[0]['payload']['text']==raw[0]['payload']['text']

def test_only_optional_boundaries_may_be_omitted_to_fit_a_stage(env):
    from app.writing_agent import context
    session=start(env);step=session['steps'][0];data={'rules':['必须保留合取条件。'],'evidence':[{'id':'e1','payload':{'text':'原始依据。'}}],'related_sections':[{'section_id':'required','revision_id':'r1','scope':'full_required_dependency','text':'关键前提。'},{'section_id':'optional','revision_id':'r2','scope':'complete_boundary_paragraphs','text':'可选的邻接原文。'*1000}]};manifest={'related_revisions':{'required':'r1','optional':'r2'}}
    identity=context.save_context(step['id'],data,manifest,'阶段必需规则',{'context_limit':3000,'max_output_tokens':1000})
    stored=unblob(db.one('SELECT payload_blob FROM writing_contexts WHERE id=?',(identity,))['payload_blob'])
    assert stored['rules']==['必须保留合取条件。'] and stored['evidence'][0]['payload']['text']=='原始依据。'
    assert stored['related_sections'][0]['scope']=='full_required_dependency' and len(stored['related_sections'])==1
    assert manifest['related_revisions']=={'required':'r1'} and stored['omitted_optional_boundary_sections'][0]['section_id']=='optional'

def test_known_source_ids_never_become_fake_bibliography(env):
    from app.writing_agent import drafting
    data={'paragraphs':[{'local_id':'p1','text':'同一段原文必须保持不变。','reference_ids':[env['eid']]},{'local_id':'p2','text':'第二段仍保留其条件。','evidence_ids':[env['eid']]}]}
    doc,result=drafting.document(data,evidence_ids=[env['eid']])
    assert documents.plain(doc)=='同一段原文必须保持不变。\n第二段仍保留其条件。'
    assert not any(n['type']=='citation' for n in documents.nodes(doc))
    assert result['field_corrections'][0]['semantic_support']=='not_granted'
    with pytest.raises(HTTPException):drafting.document({'paragraphs':[{'local_id':'p','text':'不能猜测书目。','reference_ids':['unknown-source']}]},evidence_ids=[env['eid']])

@pytest.mark.parametrize('text,positive',[('测试已经通过了。',True),('已经在公网已部署。',True),('不能根据源码认定测试已通过。',False),('没有运行或测试记录证明该链条已完成。',False),('测试结果并不能证明已部署。',False),('测试已通过，但尚未验证性能。',True)])
def test_absence_of_execution_proof_is_not_a_pass_claim(text,positive):
    assert verification.asserts_execution_result(text) is positive


def legacy(env):
    task=tasks.enqueue(env['pid'],'write',{'section_id':env['sid']})
    manifest={'rule_package_id':security.require_project(env['pid'])['rule_package_id']}
    output={'section_id':env['sid'],'base_revision_id':None,'document':documents.text_document('旧候选草稿，尚无逐句核验。'),'manifest':manifest}
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(output),task['id']))
    return task,output

def envelope(answer):
    return {'text':db.dumps({'agent_transport':True,'text':db.dumps(answer),'tool_calls':[],'message':None,'items':[],'finish':'stop'}),'usage':{'input_tokens':5,'output_tokens':5},'cost':0,'remote_id':'completion-fixture','request_hash':digest(answer)}

def scores(value=2):return {key:{'score':value,'applicable':True,'rationale':'对照给定原句和具体条件核对。'} for key in scoring.DIMENSIONS}

def diagnostic(env,monkeypatch):
    fake_model(monkeypatch);run=evaluations.create(env['pid'],{'profile_id':env['profile']['id'],'case_ids':['D06'],'repetitions':1,'budget_limit':2})
    while tasks.run_one():pass
    return evaluations.get(env['pid'],run['id'])

def pair(env,monkeypatch,with_prose=False):
    pid=env['pid'];sid2=env['client'].post(f'/api/v1/projects/{pid}/sections',json={'title':'另一个独立章节','kind':'system_design'}).json()['id']
    e2=evidence.create(pid,{'kind':'code_observation','origin_type':'code','origin_id':env['snapshot']['id'],'locator':{'path':'gate.ts','line_start':1,'line_end':1}})
    c2=contracts.save_contract(pid,sid2,{'goal':'核对另一个任务的具体范围','evidence_ids':[e2['id']],'depends_on_sections':[]},state(pid)['revision'])
    if with_prose:
        for sid in (env['sid'],sid2):documents.save(db.one('SELECT * FROM sections WHERE id=?',(sid,)),documents.text_document('原有条件及前提需要保留。'),None)
    fake_model(monkeypatch);one=start(env)
    s2=coordinator.create(pid,{'section_id':sid2,'operation':'generate_unit','base_revision_id':db.one('SELECT current_revision_id FROM sections WHERE id=?',(sid2,))['current_revision_id'],'expected_project_state_revision':state(pid)['revision'],'contract_version_id':c2['id'],'profile_id':env['profile']['id']})
    while tasks.run_one():pass
    sessions=[coordinator.get(pid,x['id']) for x in (one,s2)]
    for s in sessions:
        assert s['status']=='needs_review',s
        a=db.one('SELECT id FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1',(s['candidates'][-1]['id'],))
        verification.human_review(pid,a['id'],{'decision':'approved','note':'本测试模拟明确人工声明，仅验证事务控制，不作为真实模型评分。'})
    return sessions,sid2,e2

def test_legacy_strict_is_rejected_in_shared_service(env):
    task,_=legacy(env)
    with pytest.raises(HTTPException) as error:main.accept(env['sid'],{'task_id':task['id'],'base_revision_id':None,'accept_policy':'strict'})
    assert error.value.detail['code']=='VERIFICATION_UNRESOLVED'
    assert not db.rows('SELECT * FROM revisions')

def test_legacy_draft_is_idempotent_analyzed_and_preserves_stale_causes(env):
    task,_=legacy(env);impact.add_reason(env['pid'],env['sid'],'legacy','unresolved','v1','未处理的来源变化')
    body={'task_id':task['id'],'base_revision_id':None}
    first=main.accept(env['sid'],body);again=main.accept(env['sid'],body)
    assert first['id']==again['id'] and again['idempotent_replay']
    assert first['review_state']=='unverified'
    assert db.one('SELECT COUNT(*) n FROM document_analyses')['n']==1
    assert db.one('SELECT COUNT(*) n FROM writing_commit_receipts')['n']==1
    assert db.one('SELECT stale FROM sections WHERE id=?',(env['sid'],))['stale']==1

def test_legacy_explicit_key_conflict_and_atomic_rollback(env,monkeypatch):
    task,_=legacy(env);body={'task_id':task['id'],'base_revision_id':None}
    original=adoption.save_receipt
    monkeypatch.setattr(adoption,'save_receipt',lambda *a:(_ for _ in ()).throw(RuntimeError('回执失败')))
    with pytest.raises(RuntimeError):adoption.adopt_legacy(env['sid'],body,'legacy-explicit-key')
    assert not db.rows('SELECT * FROM revisions') and not db.rows('SELECT * FROM document_analyses')
    monkeypatch.setattr(adoption,'save_receipt',original)
    adoption.adopt_legacy(env['sid'],body,'legacy-explicit-key')
    with pytest.raises(HTTPException) as error:adoption.adopt_legacy(env['sid'],{**body,'mode':'append'},'legacy-explicit-key')
    assert error.value.detail['code']=='IDEMPOTENCY_CONFLICT'

def test_source_change_invalidates_chapters_that_read_it_through_full_manuscript(env,monkeypatch):
    sessions,sid2,_=pair(env,monkeypatch)
    first,second=sessions
    adoption.adopt(env['pid'],first['candidates'][-1]['id'],{'base_revision_id':None,'accept_policy':'strict'},'precise-'+first['id'])
    # The first chapter was empty in the second candidate's original input.
    # Its new body must be read before the second candidate can be adopted.
    with pytest.raises(HTTPException) as error:
        adoption.adopt(env['pid'],second['candidates'][-1]['id'],{'base_revision_id':None,'accept_policy':'strict'},'old-whole-paper-'+second['id'])
    assert error.value.detail['code']=='INPUT_VERSION_CHANGED'
    head=state(env['pid'])
    fresh=finish(env,coordinator.create(env['pid'],{'section_id':sid2,'operation':'generate_unit','base_revision_id':None,'expected_project_state_revision':head['revision'],'contract_version_id':head['contracts'][sid2],'profile_id':env['profile']['id']}))
    assert fresh['status']=='needs_review',fresh
    cid=fresh['candidates'][-1]['id'];analysis=db.one('SELECT id FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1',(cid,))
    verification.human_review(env['pid'],analysis['id'],{'decision':'approved','note':'本测试模拟核对已保存全文及原始来源，只验证版本与依赖传播。'})
    adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'fresh-whole-paper-'+fresh['id'])
    assert db.one('SELECT stale FROM sections WHERE id=?',(sid2,))['stale']==0
    evidence.set_validity(env['pid'],env['eid'],'revoked')
    assert db.one('SELECT stale FROM sections WHERE id=?',(env['sid'],))['stale']==1
    assert db.one('SELECT stale FROM sections WHERE id=?',(sid2,))['stale']==1

def test_historical_session_does_not_invalidate_replaced_current_dependencies(env,monkeypatch):
    cid,_=reviewed_candidate(env,monkeypatch);first=adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'historical-first-key')
    # A later explicitly tracked revision no longer uses the old evidence.
    section=db.one('SELECT * FROM sections WHERE id=?',(env['sid'],));current=documents.save(section,documents.text_document('后续正文使用了独立依据。'),first['revision_ids'][0])
    from app.writing_agent.common import dependency
    dependency(env['pid'],'writing_origin','independent-new-origin','2','revision',current['id'],current['id'])
    evidence.set_validity(env['pid'],env['eid'],'revoked')
    assert db.one('SELECT stale FROM sections WHERE id=?',(env['sid'],))['stale']==0

def test_separate_stale_causes_survive_unrelated_strict_adoption(env,monkeypatch):
    cid,_=reviewed_candidate(env,monkeypatch)
    impact.add_reason(env['pid'],env['sid'],'core',state(env['pid'])['core_version_id'],'v1','论文核心复查')
    impact.add_reason(env['pid'],env['sid'],'fact','other-unused-fact','v1','另一个未处理的事实原因')
    adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'causes-remain-key')
    rows=impact.reasons(env['pid'],env['sid'])
    assert next(r for r in rows if r['cause_type']=='core')['status']=='resolved'
    assert next(r for r in rows if r['cause_type']=='fact')['status']=='open'
    assert db.one('SELECT stale FROM sections WHERE id=?',(env['sid'],))['stale']==1

def test_manual_stale_resolution_is_scoped_to_current_revision(env):
    r=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('当前版本。'),None)
    impact.add_reason(env['pid'],env['sid'],'fact','f1','1','事实一');impact.add_reason(env['pid'],env['sid'],'fact','f2','1','事实二')
    rows=impact.reasons(env['pid'],env['sid'])
    with pytest.raises(HTTPException):impact.resolve_manual(env['pid'],env['sid'],{'reason_ids':[rows[0]['id']],'reviewed_revision_id':'old','note':'已经核对当前原文和事实来源的变化。'})
    impact.resolve_manual(env['pid'],env['sid'],{'reason_ids':[rows[0]['id']],'reviewed_revision_id':r['id'],'note':'已经核对当前原文和事实来源的变化。'})
    assert sum(x['status']=='open' for x in impact.reasons(env['pid'],env['sid']))==1

def test_coherence_expands_original_and_adoption_rechecks_new_premise(env,monkeypatch):
    sid=env['client'].post(f'/api/v1/projects/{env["pid"]}/sections',json={'title':'前提所在章节'}).json()['id']
    r=documents.save(db.one('SELECT * FROM sections WHERE id=?',(sid,)),documents.text_document('第一段。\n第二段。\n第三段。\n第四段。\n第五段才定义关键前提。'),None)
    anchor=db.loads(r['document'])['content'][-1]['attrs']['nodeId'];fake_model(monkeypatch);original=provider.generate;expanded=[]
    def generate(system,user,images=None):
        data=json.loads(json.loads(user)['messages'][0]['content'])
        if 'context_requests列表' in system:
            expanded.append(data)
            if not data['expanded_original_passages']:return envelope({'findings':[],'coverage_complete':False,'summary':'需读第五段','context_requests':[{'section_id':sid,'revision_id':r['id'],'node_id':anchor,'before':0,'after':0,'reason':'关键定义在更远原文'}]})
            return envelope({'findings':[],'coverage_complete':True,'summary':'已对照第五段关键原文'})
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);s=finish(env,start(env));assert s['status']=='needs_review',s
    assert len(expanded)==2 and '关键前提' in expanded[-1]['expanded_original_passages'][0]['paragraphs'][0]['text']
    assert db.one("SELECT COUNT(*) n FROM tool_observations WHERE tool_name='read_section_range'")['n']==1
    documents.save(db.one('SELECT * FROM sections WHERE id=?',(sid,)),documents.text_document('前提已经修改。'),r['id'])
    with pytest.raises(HTTPException):adoption.adopt(env['pid'],s['candidates'][-1]['id'],{'base_revision_id':None,'accept_policy':'draft'},'expanded-premise-adopt')

def test_expansion_limit_retains_exact_unresolved_scope(env,monkeypatch):
    sid=env['client'].post(f'/api/v1/projects/{env["pid"]}/sections',json={'title':'仍需前提的章节'}).json()['id']
    r=documents.save(db.one('SELECT * FROM sections WHERE id=?',(sid,)),documents.text_document('一段原文。'),None)
    fake_model(monkeypatch);original=provider.generate;calls=[]
    def generate(system,user,images=None):
        if 'context_requests列表' in system:
            calls.append(1);return envelope({'findings':[],'coverage_complete':True,'summary':'模拟持续要求更多前提','context_requests':[{'section_id':sid,'revision_id':r['id'],'reason':'尚不能确定远处前提'}]})
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);session=finish(env,start(env));result=next(s['output']['result'] for s in session['steps'] if s['stage']=='coherence')
    assert len(calls)==4 and not result['coverage_complete'] and result['unresolved_context_requests']

@pytest.mark.parametrize('lost',[False,True])
def test_revision_has_independent_old_new_regression(env,monkeypatch,lost):
    cid,_=reviewed_candidate(env,monkeypatch);parent=db.one('SELECT session_id FROM writing_candidates WHERE id=?',(cid,))['session_id'];original=provider.generate;seen=[]
    def generate(system,user,images=None):
        if '独立修订回归检查' in system:
            data=json.loads(json.loads(user)['messages'][0]['content']);seen.append(data)
            return envelope({'claim_checks':[{'local_id':c['local_id'],'status':'lost' if lost else 'preserved','evidence_ids':c['evidence_ids'],'reason':'条件被遗漏。' if lost else '原条件和结论范围保持。'} for c in data['claim_baseline']],'issue_checks':[{'issue_id':i['issue_id'],'status':'fixed','reason':'所要求用词已对照修改。'} for i in data['prior_issues']],'findings':[],'coverage_complete':True,'summary':'独立比较完成'})
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);s=revision.revise(env['pid'],parent,{'instructions':'只修改用词，保留发行方且启用的条件。'});s=finish(env,s)
    assert s['status']=='needs_review',s
    check=next(x for x in s['steps'] if x['stage']=='regression')['output']['result']
    assert check['coverage_complete'] is (not lost)
    wire=next(r for r in db.rows('SELECT c.manifest_blob FROM writing_contexts c JOIN writing_steps s ON s.id=c.step_id WHERE s.session_id=? AND s.stage=? ORDER BY c.rowid',(s['id'],'regression')) if unblob(r['manifest_blob']).get('context_kind')=='actual_wire_frame')
    from app.writing_agent.identifiers import decode
    assert seen and decode(seen[0]['original_document'],unblob(wire['manifest_blob'])['identifier_aliases'])==unblob(adoption.candidate(cid,env['pid'])['document_blob'])
    assert db.one('SELECT parent_candidate_id FROM writing_candidates WHERE id=?',(s['candidates'][-1]['id'],))['parent_candidate_id']==cid

def test_joint_review_unblocks_atomic_mutual_patch_and_rebinds_versions(env,monkeypatch):
    sessions,_,_=pair(env,monkeypatch,with_prose=True)
    items=[{'candidate_id':s['candidates'][-1]['id'],'body':{'base_revision_id':s['base_versions']['base_revision_id'],'accept_policy':'strict','placement':'replace_section'}} for s in sessions]
    with pytest.raises(HTTPException) as error:adoption.adopt_many(env['pid'],items,'joint-no-review')
    assert error.value.detail['code']=='JOINT_REVIEW_REQUIRED'
    review=joint.create(env['pid'],{'items':items,'profile_id':env['profile']['id']})
    while tasks.run_one():pass
    review=joint.get(env['pid'],review['id']);assert review['status']=='needs_review'
    joint.human_review(env['pid'],review['id'],{'decision':'approved','all_candidates_reviewed':True,'reviewed_input_hash':review['input_hash'],'note':'测试中声明已对照全部联合候选、条件和互相引用的原文，仅用于验证事务。'})
    response=adoption.adopt_many(env['pid'],items,'joint-accepted-key',review['id'])
    assert len(response['results'])==2
    current={s['id']:s['current_revision_id'] for s in db.rows('SELECT id,current_revision_id FROM sections WHERE project_id=?',(env['pid'],))}
    for result in response['results']:
        for run in verification.runs(env['pid'],result['analysis_id']):
            for sid,rid in run['input_manifest'].get('related_revisions',{}).items():assert current[sid]==rid
    again=adoption.adopt_many(env['pid'],items,'joint-accepted-key',review['id']);assert all(r['idempotent_replay'] for r in again['results'])

def test_joint_conflict_never_partially_commits(env,monkeypatch):
    sessions,_,_=pair(env,monkeypatch,with_prose=True)
    items=[{'candidate_id':s['candidates'][-1]['id'],'body':{'base_revision_id':s['base_versions']['base_revision_id'],'accept_policy':'strict'}} for s in sessions]
    review=joint.create(env['pid'],{'items':items,'profile_id':env['profile']['id']})
    while tasks.run_one():pass
    review=joint.get(env['pid'],review['id']);joint.human_review(env['pid'],review['id'],{'decision':'approved','all_candidates_reviewed':True,'reviewed_input_hash':review['input_hash'],'note':'在隔离测试中明确声明已通读全部联合候选并核对相互影响的原文条件。'})
    s=db.one('SELECT * FROM sections WHERE id=?',(sessions[0]['section_id'],));documents.save(s,documents.text_document('并发编辑形成新版本。'),s['current_revision_id'])
    before={s['id']:s['current_revision_id'] for s in db.rows('SELECT id,current_revision_id FROM sections WHERE project_id=?',(env['pid'],))}
    with pytest.raises(HTTPException):adoption.adopt_many(env['pid'],items,'joint-conflicted-key',review['id'])
    assert before=={s['id']:s['current_revision_id'] for s in db.rows('SELECT id,current_revision_id FROM sections WHERE project_id=?',(env['pid'],))}
    assert not db.rows('SELECT * FROM writing_adoptions')

def test_dimensional_reviews_preserve_disagreement_and_never_promote_machine(env,monkeypatch):
    run=diagnostic(env,monkeypatch);item=run['items'][0]
    scoring.review(env['pid'],run['id'],{'item_id':item['id'],'evaluator_id':'machine','decision':'pass','note':'仅模型辅助核对材料条件。','dimensions':scores()},method='model_assisted')
    assert db.one('SELECT human_review_json FROM diagnostic_items WHERE id=?',(item['id'],))['human_review_json'] is None
    for evaluator,value in (('reviewer-a',2),('reviewer-b',0)):
        scoring.review(env['pid'],run['id'],{'item_id':item['id'],'evaluator_id':evaluator,'decision':'pass','note':'已逐项核对给定材料的具体条件。','dimensions':scores(value),'duration_seconds':15})
    current=scoring.effective(env['pid'],item['id']);assert current['disagreement'] and not current['qualification_ready']
    assert scoring.report(env['pid'],run['id'])['recorded_human_minutes']==.5
    with pytest.raises(HTTPException):scoring.freeze_policy(env['pid'],run['id'],{'thresholds':{k:1 for k in scoring.DIMENSIONS},'note':'基于实际评分结果的政策依据，不以主观流畅度替代事实判断。'})
    scoring.adjudicate(env['pid'],run['id'],{'item_id':item['id'],'review_ids':current['review_ids'],'decision':'fail','note':'对照原始条件后确认分歧源于条件遗漏，保留两份评分并裁决不通过。'})
    assert scoring.effective(env['pid'],item['id'])['adjudicated']
    assert len(scoring.reviews(env['pid'],item['id']))==3

def test_blind_packet_hides_arm_and_keeps_review_identity(env,monkeypatch):
    run=diagnostic(env,monkeypatch);packet=scoring.blind_packet(env['pid'],run['id'],'reviewer-blind')
    assert packet['items']
    assert not {'model','configuration','arm','variant_id','prior_reviews'}&set(packet['items'][0])
    body={'packet_id':packet['packet_id'],'blind_item_token':packet['items'][0]['blind_item_token'],'evaluator_id':'reviewer-blind','decision':'pass','note':'已对照隐藏配置的原始回答和给定事实。','dimensions':scores()}
    recorded=scoring.review(env['pid'],run['id'],body)
    r=db.one('SELECT * FROM diagnostic_reviews WHERE id=?',(recorded['review_id'],));assert r['blinded']==1 and r['method']=='human_reviewed'
    with pytest.raises(HTTPException):scoring.review(env['pid'],run['id'],{**body,'evaluator_id':'someone-else'})

def test_diagnostic_uses_frozen_dataset_after_source_file_changes(env,monkeypatch):
    original=evaluations.dataset();calls=fake_model(monkeypatch)
    run=evaluations.create(env['pid'],{'profile_id':env['profile']['id'],'case_ids':['D06'],'repetitions':1,'budget_limit':2})
    modified=json.loads(json.dumps(original));modified['cases'][0]['prompt']='新的题集不能覆盖旧运行的实际输入。';monkeypatch.setattr(evaluations,'dataset',lambda:modified)
    while tasks.run_one():pass
    current=evaluations.get(env['pid'],run['id'])
    assert current['evaluation_dataset']==original
    assert current['summary']['completed']==current['summary']['total']
    assert all('新的题集不能覆盖' not in json.dumps(payload,ensure_ascii=False) for _,payload in calls)
    packet=scoring.blind_packet(env['pid'],run['id'],'frozen-reviewer')
    assert packet['items'] and current['policy']['dataset_blob']

def test_comparison_keeps_original_inputs_and_all_phase_calls(env,monkeypatch):
    fake_model(monkeypatch);comparison=comparisons.create(env['pid'],{'profile_id':env['profile']['id'],'factor':'workflow','case_ids':['D06'],'repetitions':1,'budget_limit':2})
    while tasks.run_one():pass
    report=comparisons.report(env['pid'],comparison['id']);assert report['all_generated']
    assert all(r['original_inputs_match'] and len(r['arms'])==2 for r in report['paired_samples'])
    assert report['arms']['judgment_then_draft']['billing']['requests']==report['arms']['baseline']['billing']['requests']*2
    assert not report['human_acceptance_complete'] and report['semantic_win_rate'] is None

def test_comparison_budget_is_shared_by_all_arms(env,monkeypatch):
    fake_model(monkeypatch);original=provider.generate
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:{**original(*a,**kw),'cost':.06})
    cfg=provider.config();cfg.update(input_price=None,output_price=None,max_request_cost=.06);db.set_setting('provider',cfg)
    from app.writing_agent import profiles
    new=profiles.create(env['pid'],{'role':'diagnostic','configuration':{'max_output_tokens':2048}})
    c=comparisons.create(env['pid'],{'profile_id':new['id'],'factor':'workflow','case_ids':['D06'],'repetitions':1,'budget_limit':.1})
    tasks.run_one();assert db.one('SELECT COUNT(*) n FROM generation_runs')['n']==1
    first_run=db.one("SELECT json_extract(manifest,'$.diagnostic_run_id') id FROM generation_runs LIMIT 1")['id']
    second=db.one("SELECT t.* FROM tasks t JOIN diagnostic_items i ON i.task_id=t.id JOIN diagnostic_comparison_members m ON m.run_id=i.run_id WHERE m.comparison_id=? AND m.run_id!=? ORDER BY i.rowid LIMIT 1",(c['id'],first_run))
    from app.summary_billing import BudgetExceeded
    with pytest.raises(BudgetExceeded):tasks.dispatch(second)
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n']==1

def test_scoped_budget_cannot_lose_a_bill_group_anchored_in_another_scope(env):
    from app.writing_agent.billing import recover_closed_group
    from app.writing_agent.budget import scope_charge
    cfg=provider.config();before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};after={**before,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98'}
    for i in range(2):
        task=tasks.enqueue(env['pid'],'fixture_bill',{});identity=db.uid();meta={'diagnostic_comparison_id':'scope-'+str(i),'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':before if i==0 else None,'after':after if i==1 else None}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,task['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),None,2,db.now()))
    recover_closed_group(env['pid'])
    assert scope_charge('diagnostic_comparison_id','scope-0')==pytest.approx(.02)
    assert scope_charge('diagnostic_comparison_id','scope-1')==pytest.approx(.02)
    assert db.one('SELECT SUM(budget_charge) n FROM generation_budget_usage')['n']==pytest.approx(.02)

def test_position_swap_records_bias_without_granting_quality(env,monkeypatch):
    fake_model(monkeypatch);c=comparisons.create(env['pid'],{'profile_id':env['profile']['id'],'factor':'workflow','case_ids':['D06'],'repetitions':1,'budget_limit':2})
    while tasks.run_one():pass
    pair=comparisons.report(env['pid'],c['id'])['paired_samples'][0]
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:envelope({'preferred':'A','reason':'测试固定偏好第一位置，用来确认能发现位置敏感。'}))
    task=tasks.enqueue(env['pid'],'writing_position_swap',{'comparison_id':c['id'],'profile_id':env['profile']['id'],'item_ids':[x['item_id'] for x in pair['arms'].values()]})
    tasks.run_one();result=db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'])
    assert result['position_consistent'] is False and result['human_acceptance'] is False

def test_unreviewed_outputs_cannot_freeze_calibration_or_qualify(env,monkeypatch):
    run=diagnostic(env,monkeypatch)
    with pytest.raises(HTTPException):scoring.freeze_policy(env['pid'],run['id'],{'thresholds':{k:2 for k in scoring.DIMENSIONS},'note':'没有人工评分时不能冻结有效质量政策，即使已经输出了JSON。'})
    with pytest.raises(HTTPException):evaluations.qualify(env['pid'],run['id'],'样本尚未人工核对，不能授予资格。')

def test_frozen_quality_policy_requires_critical_scores_and_stays_assisted(env,monkeypatch):
    data=evaluations.dataset();fixture={**data,'cases':[c for c in data['cases'] if c['id'] in ('D02','D06')]};monkeypatch.setattr(evaluations,'dataset',lambda:fixture);fake_model(monkeypatch)
    run=evaluations.create(env['pid'],{'profile_id':env['profile']['id'],'repetitions':3,'budget_limit':2})
    while tasks.run_one():pass
    run=evaluations.get(env['pid'],run['id']);bad=next(i for i in run['items'] if i['case_id']=='D02')
    for item in run['items']:
        dimensions=scores()
        if item['id']==bad['id']:dimensions['condition_truth']={'score':None,'applicable':False,'rationale':'测试故意漏掉关键条件评分。'}
        scoring.review(env['pid'],run['id'],{'item_id':item['id'],'evaluator_id':'synthetic-fixture-author','decision':'pass','note':'只模拟人工提交接口来验证资格门槛，不代表真实模型人工评分。','dimensions':dimensions})
    policy=scoring.freeze_policy(env['pid'],run['id'],{'thresholds':{key:1 for key in scoring.DIMENSIONS},'note':'本合成fixture显式冻结阈值，验证已记录评分能否满足关键条件门槛。'})
    with pytest.raises(HTTPException) as error:evaluations.qualify(env['pid'],run['id'],'合成fixture资格控制测试。',policy['id'])
    assert '必需评分' in error.value.detail['message']
    scoring.review(env['pid'],run['id'],{'item_id':bad['id'],'evaluator_id':'synthetic-fixture-author','decision':'pass','note':'合成fixture补齐必需条件评分，以验证受限资格登记路径。','dimensions':scores()})
    result=evaluations.qualify(env['pid'],run['id'],'合成fixture资格控制测试。',policy['id'])
    assert result['qualification_scope']=='assisted_writing_requires_current_human_adoption_review' and result['no_truth_guarantee'] is True

def test_saved_response_can_reparse_without_repeating_paid_call(env,monkeypatch):
    from app.writing_agent import judgment
    calls=fake_model(monkeypatch);session=start(env);tasks.run_one();original=judgment.validate
    def broken(*args):raise ValueError('JSON已解析后的本地校验崩溃注入')
    monkeypatch.setattr(judgment,'validate',broken);tasks.run_one()
    assert coordinator.get(env['pid'],session['id'])['status']=='failed'
    count=sum('返回 {"schema_version"' in system for system,_ in calls)
    monkeypatch.setattr(judgment,'validate',original);coordinator.resume(env['pid'],session['id']);done=finish(env,session)
    assert done['status']=='needs_review'
    assert sum('返回 {"schema_version"' in system for system,_ in calls)==count==1

def test_editing_base_text_stops_before_another_paid_stage(env,monkeypatch):
    calls=fake_model(monkeypatch);session=start(env);tasks.run_one();before=len(calls)
    documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('执行中用户修改了基础正文。'),None)
    tasks.run_one()
    assert coordinator.get(env['pid'],session['id'])['status']=='stale' and len(calls)==before

def test_paid_draft_local_reparse_preserves_raw_response_and_calls_zero_models(env,monkeypatch):
    from app.writing_agent import drafting
    calls=fake_model(monkeypatch);original=drafting.document
    monkeypatch.setattr(drafting,'document',lambda *a,**kw:(_ for _ in ()).throw(ValueError('模拟本地字段解析失败')))
    session=finish(env,start(env));assert session['status']=='failed'
    before=len(calls);run=db.one("SELECT * FROM generation_runs WHERE json_extract(manifest,'$.stage')='draft'");raw=db.loads(run['manifest'])['output_blob']
    monkeypatch.setattr(drafting,'document',original)
    reparsed=coordinator.reparse_saved_draft(env['pid'],session['id']);assert len(calls)==before
    cid=reparsed['candidates'][-1]['id'];decision=unblob(adoption.candidate(cid,env['pid'])['decision_blob'])
    assert decision['reparsed_generation_run_id']==run['id']
    assert db.loads(db.one('SELECT manifest FROM generation_runs WHERE id=?',(run['id'],))['manifest'])['output_blob']==raw
    assert finish(env,reparsed)['status']=='needs_review'

def test_recheck_cannot_bypass_unknown_remote_stage(env,monkeypatch):
    cid,_=reviewed_candidate(env,monkeypatch);session=adoption.candidate(cid,env['pid'])['session_id']
    db.execute("UPDATE writing_sessions SET status='outcome_unknown' WHERE id=?",(session,))
    with pytest.raises(HTTPException) as error:coordinator.recheck_candidate(env['pid'],cid)
    assert error.value.detail['code']=='RECHECK_NOT_ALLOWED'

def test_actual_wire_input_is_frozen_with_tool_schema_and_exact_budget(env,monkeypatch):
    fake_model(monkeypatch);session=finish(env,start(env))
    rows=db.rows('SELECT c.* FROM writing_contexts c JOIN writing_steps s ON s.id=c.step_id WHERE s.session_id=?',(session['id'],));actual=[r for r in rows if unblob(r['manifest_blob']).get('context_kind')=='actual_wire_frame']
    assert len(actual)==7
    from app.source_payload import token_upper_bound
    for row in actual:
        content=unblob(row['payload_blob']);budget=db.loads(row['budget_json'])
        assert budget['input_estimate']==token_upper_bound(content['system'],db.dumps(content['frame']))
        assert budget['input_estimate']+budget['reserved_generated_total']<=budget['context_limit']

def test_scoped_identifier_aliases_preserve_prose_and_roundtrip_only_identity_fields():
    from app.writing_agent.identifiers import encode,decode
    source='a'*32;node='b'*32;sentence='c'*32
    payload={'evidence':[{'id':source,'payload':{'text':source}}],'sentences':[{'id':sentence,'node_id':node,'text':'原文中的32位值 '+source}],'passages':[{'node_id':node,'text':'段落原文'}]}
    encoded,aliases=encode(payload,{'stage':'annotation','evidence_versions':{source:'v1'}})
    assert encoded['evidence'][0]['id']=='E1' and encoded['sentences'][0]['id']=='S1' and encoded['passages'][0]['node_id']=='P1'
    assert encoded['evidence'][0]['payload']['text']==source
    assert decode({'node_ids':['P1'],'evidence_ids':['E1'],'sentence_id':'S1','statement':'E1出现在说明中'},aliases)=={'node_ids':[node],'evidence_ids':[source],'sentence_id':sentence,'statement':'E1出现在说明中'}

def test_complete_invalid_json_gets_one_recorded_repair_without_hidden_loops(env,monkeypatch):
    fake_model(monkeypatch);original=provider.generate;bad=[]
    def generate(system,user,images=None):
        if '独立阅读冻结候选' in system and '上一次完整响应不是合法JSON' not in system:
            bad.append(1);return envelope({'placeholder':'known response'}) | {'text':db.dumps({'agent_transport':True,'text':'{"sentences":[invalid JSON','tool_calls':[],'message':None,'items':[],'finish':'stop'})}
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);session=finish(env,start(env))
    assert session['status']=='needs_review' and len(bad)==1
    rows=db.rows("SELECT manifest FROM generation_runs WHERE json_extract(manifest,'$.stage')='annotation'")
    assert len(rows)==2
    assert any(db.loads(r['manifest']).get('structured_json_attempt')==1 for r in rows)

def test_cancel_after_complete_invalid_json_prevents_paid_repair(env,monkeypatch):
    fake_model(monkeypatch);original=provider.generate;bad=[]
    def generate(system,user,images=None):
        if '独立阅读冻结候选' in system:
            bad.append(1);db.execute("UPDATE tasks SET cancel_requested=1 WHERE status='running' AND kind='agent_step'")
            return envelope({}) | {'text':db.dumps({'agent_transport':True,'text':'{invalid','tool_calls':[],'message':None,'items':[],'finish':'stop'})}
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);session=finish(env,start(env))
    assert session['status']=='cancelled' and len(bad)==1
    assert db.one("SELECT COUNT(*) n FROM generation_runs WHERE json_extract(manifest,'$.stage')='annotation'")['n']==1

@pytest.mark.parametrize('kind,unknown',[('ConnectError',False),('ConnectTimeout',False),('ReadTimeout',True),('WriteError',True)])
def test_transport_distinguishes_connection_setup_from_unknown_sent_requests(env,monkeypatch,kind,unknown):
    import httpx
    def fail(request):raise getattr(httpx,kind)('isolated transport fixture',request=request)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(fail)))
    session=start(env);tasks.run_one();state_now=coordinator.get(env['pid'],session['id'])
    assert state_now['status']==('outcome_unknown' if unknown else 'failed')
    run=db.one('SELECT * FROM generation_runs WHERE project_id=?',(env['pid'],))
    assert run['status']==('outcome_unknown' if unknown else 'failed_before_send')
    assert len(db.rows('SELECT * FROM generation_runs'))==1
    if not unknown:assert db.loads(run['manifest'])['transport_failure']['http_request_sent'] is False

def test_failed_before_send_does_not_break_later_closed_billing_interval(env):
    from app.writing_agent.billing import recover_closed_group
    cfg=provider.config();before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};after={**before,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98'}
    task=tasks.enqueue(env['pid'],'fixture_bill',{})
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),task['id'],env['pid'],'failed_before_send',cfg['model'],cfg['protocol'],'{}',2,db.now()))
    for i in range(2):
        t=tasks.enqueue(env['pid'],'fixture_bill',{});meta={'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':before if i==0 else None,'after':after if i==1 else None}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),t['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),2,db.now()))
    assert recover_closed_group(env['pid'])['matched_closed_intervals'][0]['requests']==2
    assert db.one('SELECT SUM(budget_charge) n FROM generation_budget_usage WHERE status NOT IN (\'rejected\',\'failed_before_send\')')['n']==pytest.approx(.02)

def test_oversized_code_tool_returns_explicit_narrowing_without_fake_observation(env):
    from app.writing_agent import repository,tools
    (env['root']/'large.ts').write_text('export const x = "'+('x'*8000)+'";\n',encoding='utf-8');snapshot=repository.capture(env['pid'],env['binding']['id'])
    from app.writing_agent.common import update_state
    update_state(env['pid'],state(env['pid'])['revision'],target_snapshot_id=snapshot['id'])
    session=start(env);step=session['steps'][0];before=db.one('SELECT COUNT(*) n FROM evidence_items')['n']
    result=tools.execute(env['pid'],step['id'],'read_code',{'snapshot_id':snapshot['id'],'relative_path':'large.ts','line_start':1,'line_end':1},'oversized-read',snapshot['id'])
    assert result['status']=='needs_narrower_range' and result['data']['text_not_provided']
    assert result['data']['requested_range_complete'] is False and 'text' not in result['data']
    assert db.one('SELECT COUNT(*) n FROM evidence_items')['n']==before

def test_revision_only_rule_is_loaded_for_the_actual_revision_operation(env,monkeypatch):
    from app import rules
    text='修订时必须保留原有否定条件，不能把未执行改成已通过。'
    env['client'].post(f'/api/v1/projects/{env["pid"]}/rules',json={'statement':text,'scope':'global','modality':'mandatory','operations':['revise']});rules.publish(env['pid'])
    cid,_=reviewed_candidate(env,monkeypatch);parent=adoption.candidate(cid,env['pid'])['session_id']
    session=revision.revise(env['pid'],parent,{'instructions':'仅修订表达，保留原有否定条件。'});tasks.run_one()
    rows=db.rows('SELECT c.* FROM writing_contexts c JOIN writing_steps s ON s.id=c.step_id WHERE s.session_id=? ORDER BY c.rowid',(session['id'],));original=next(unblob(r['payload_blob']) for r in rows if 'rules' in unblob(r['payload_blob']))
    assert any(r['statement']==text for r in original['rules'])
    assert session['base_versions']['operation']=='revise'

def test_core_author_statement_cannot_bypass_revoked_parent_claim(env):
    from app.writing_agent import claims,context,repository
    claim=claims.create(env['pid'],{'kind':'fact','proposition':{'statement':'代码条件同时要求两项成立。'},'evidence':[{'evidence_id':env['eid']}]})
    core=contracts.save_core(env['pid'],{'research_question':'根据该命题确定研究范围。','claim_versions':[claim['id']]});contracts.publish_core(env['pid'],core['id'],state(env['pid'])['revision'])
    context.assemble(env['pid'],env['sid'],env['contract']['id'],env['profile'])
    author=db.one("SELECT id FROM evidence_items WHERE origin_type='author' AND origin_id=?",(core['id'],))['id'];before=evidence.permission_stamp(env['pid'],author)
    repository.set_egress(env['pid'],env['binding']['id'],'local_only')
    assert evidence.allowed_remote(env['pid'],author) is False
    repository.set_egress(env['pid'],env['binding']['id'],'selected_content')
    assert evidence.permission_stamp(env['pid'],author)!=before

def test_credential_change_invalidates_profile_without_exposing_key(env,monkeypatch):
    from app.writing_agent import profiles
    assert profiles.get(env['pid'],env['profile']['id'])['configuration_current']
    replacement='isolated-replacement-secret-credential-123456';monkeypatch.setattr(security,'get_key',lambda profile='':replacement)
    assert profiles.get(env['pid'],env['profile']['id'])['configuration_current'] is False
    fresh=profiles.create(env['pid'],{'role':'verify','configuration':{'max_output_tokens':2048}})
    assert fresh['configuration_current'] and replacement not in db.dumps(fresh)

def test_closed_bill_group_rejects_current_sample_from_different_credential(env):
    from app.writing_agent.billing import recover_closed_group,reconcile
    cfg=provider.config();before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'}
    after={**before,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98','credential_hash':'other-account','base_url':cfg['base_url']}
    for i in range(2):
        task=tasks.enqueue(env['pid'],'fixture_bill',{});meta={'credential_hash':'original-account','provider':cfg,'billing_interval_observation':{'before':before if i==0 else None,'after':None}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),task['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),2,db.now()))
    assert not recover_closed_group(env['pid'],after)['matched_closed_intervals']
    assert not db.rows('SELECT * FROM generation_billing_groups')
    assert recover_closed_group(env['pid'],{**after,'credential_hash':'original-account'})['matched_closed_intervals'][0]['requests']==2
    assert reconcile({**before,'credential_hash':'a','base_url':cfg['base_url']},{**after,'credential_hash':'b'},{'input_tokens':60,'output_tokens':20},2) is None

def test_explicit_complete_human_review_supersedes_incomplete_models_without_erasing_them(env,monkeypatch):
    fake_model(monkeypatch);session=finish(env,start(env));cid=session['candidates'][-1]['id'];a=db.one('SELECT * FROM document_analyses WHERE candidate_id=?',(cid,));rr=verification.runs(env['pid'],a['id']);manifest=rr[0]['input_manifest']
    # Retain a separate incomplete analysis as the historical model outcome.
    for run in rr:db.execute("UPDATE writing_verification_runs SET status='stale' WHERE id=?",(run['id'],))
    incomplete=verification.save_run(env['pid'],a['id'],'annotation',manifest,{'findings':[],'coverage_complete':False,'summary':'合成模型未完成整体覆盖'})
    body={'decision':'approved','note':'在合成fixture中明确声明已逐句对照全部来源、条件与上下文，只测试人工优先策略。','confirmed_full_scope':True,'reviewed_text_hash':a['text_hash']}
    verification.human_review(env['pid'],a['id'],body)
    assert next(r for r in verification.runs(env['pid'],a['id']) if r['id']==incomplete)['result']['coverage_complete'] is False
    assert adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'explicit-human-scope-key')['review_state']=='human_reviewed'

def test_known_locator_key_typo_is_repaired_without_changing_raw_response_or_issue(env,monkeypatch):
    fake_model(monkeypatch);original=provider.generate
    def generate(system,user,images=None):
        if 'context_requests列表' in system:
            data=json.loads(json.loads(user)['messages'][0]['content']);node=data['candidate_paragraphs'][0]['node_id']
            return envelope({'findings':[{'_node_id':node,'kind':'coherence','severity':'warning','message':'这是保留原始问题内容的定位字段拼写测试。'}],'coverage_complete':True,'summary':'定位字段拼写有误，内容不变'})
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate);s=finish(env,start(env));assert s['status']=='needs_review',s
    step=next(step for step in s['steps'] if step['stage']=='coherence');finding=step['output']['result']['findings'][0]
    row=db.one("SELECT manifest FROM generation_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1",(step['task_id'],));meta=db.loads(row['manifest'])
    assert meta['output_identifier_key_repairs'][0]['semantic_support']=='not_granted'
    assert '_node_id' in unblob(meta['output_blob'])['text'] and finding['node_id']

def test_truncated_known_response_keeps_reasoning_observation_and_reconciles_real_interval(env,monkeypatch):
    from app.writing_agent import billing,profiles
    cfg=provider.config();before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};after={**before,'requests':8,'input_tokens':110,'output_tokens':4136,'amounts':{'cost':'1.02'},'remaining':'9.98'};samples=iter([before,after]);monkeypatch.setattr(billing,'sample',lambda cfg:next(samples))
    profile=profiles.create(env['pid'],{'role':'draft','configuration':{'max_output_tokens':2048,'thinking_enabled':False,'reconcile_billing':True}});env['profile']=profile
    def generate(*args,**kwargs):
        result=envelope({});result['usage']={'input_tokens':10,'output_tokens':4096,'raw':{'completion_tokens_details':{'reasoning_tokens':4096}}};result['cost']=None
        raise provider.ProviderError('预算用完，保存已知的不完整响应',response_result=result)
    monkeypatch.setattr(provider,'generate',generate);s=finish(env,start(env));assert s['status']=='failed'
    row=db.one('SELECT * FROM generation_runs');assert row['status']=='completed_invalid' and row['cost']==pytest.approx(.02)
    caps=profiles.get(env['pid'],profile['id'])['capabilities']
    assert caps['reasoning_parameter']['status']=='unknown' and caps['reasoning_usage']['evidence']['reported_reasoning_tokens']==4096

def test_json_tool_actions_with_changed_arguments_do_not_reuse_conflicting_turn_id(env,monkeypatch):
    from app.writing_agent import tools
    fake_model(monkeypatch);original=provider.generate;session=start(env);step=session['steps'][0]
    old={'snapshot_id':env['snapshot']['id'],'relative_path':'gate.ts','line_start':1,'line_end':1}
    tools.execute(env['pid'],step['id'],'read_code',old,'0-json-'+digest(['read_code',old])[:24],env['snapshot']['id'])
    count=[]
    def generate(system,user,images=None):
        if 'JSON动作模式' in system:
            count.append(1)
            if len(count)==1:return envelope({'action':'tool','name':'read_code','arguments':{**old,'line_end':2}})
            return envelope({'action':'done','summary':'本次读取不同范围，独立记录工具观察。'})
        return original(system,user,images)
    monkeypatch.setattr(provider,'generate',generate)
    assert finish(env,session)['status']=='needs_review'
    assert db.one('SELECT COUNT(*) n FROM tool_observations WHERE step_id=?',(step['id'],))['n']==2

def test_overlapping_bill_interval_includes_settled_neighbor_without_double_counting(env):
    from app.writing_agent.billing import recover_overlapping_intervals
    from app.writing_agent.budget import scope_charge
    cfg=provider.config();before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};after={**before,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98'};ids=[]
    for i in range(2):
        task=tasks.enqueue(env['pid'],'fixture_bill',{});identity=db.uid();ids.append(identity);meta={'diagnostic_run_id':'run-'+str(i),'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':before if i==0 else None,'after':after if i==0 else None}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,task['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),None if i==0 else .005,2,db.now()))
    result=recover_overlapping_intervals(env['pid']);assert result['matched_overlapping_intervals'][0]['requests']==2
    assert db.one('SELECT cost FROM generation_runs WHERE id=?',(ids[0],))['cost'] is None
    assert db.one('SELECT cost FROM generation_runs WHERE id=?',(ids[1],))['cost']==.005
    assert db.one('SELECT SUM(budget_charge) n FROM generation_budget_usage')['n']==pytest.approx(.02)
    assert scope_charge('diagnostic_run_id','run-0')==pytest.approx(.02) and scope_charge('diagnostic_run_id','run-1')==pytest.approx(.02)
    assert not recover_overlapping_intervals(env['pid'])['matched_overlapping_intervals']

def test_adjacent_observed_boundary_can_close_overlap_with_an_existing_group(env):
    from app.writing_agent.billing import recover_adjacent
    cfg=provider.config();left={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};middle={**left,'requests':8,'input_tokens':130,'output_tokens':50,'amounts':{'cost':'1.005'},'remaining':'9.995'};right={**left,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98'};ids=[]
    for i in range(2):
        t=tasks.enqueue(env['pid'],'fixture_bill',{});identity=db.uid();ids.append(identity);meta={'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':left,'after':middle if i==0 else right}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,t['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),.005 if i==0 else None,2,db.now()))
    result=recover_adjacent(env['pid']);assert result['settled_run_ids']==[ids[1]]
    assert db.one('SELECT cost FROM generation_runs WHERE id=?',(ids[1],))['cost']==pytest.approx(.015)

def test_out_of_creation_order_bill_boundary_requires_exact_token_match(env):
    from app.writing_agent.billing import recover_adjacent
    cfg=provider.config();b={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};m={**b,'requests':8,'input_tokens':130,'output_tokens':50,'amounts':{'cost':'1.005'},'remaining':'9.995'};late={**b,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.011'},'remaining':'9.989'};end={**b,'requests':10,'input_tokens':190,'output_tokens':70,'amounts':{'cost':'1.02'},'remaining':'9.98'};ids=[]
    for i,(before,after,cost) in enumerate(((b,late,.006),(b,m,.005),(m,end,None))):
        t=tasks.enqueue(env['pid'],'fixture_bill',{});identity=db.uid();ids.append(identity);meta={'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':before,'after':after}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,t['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),cost,2,db.now()))
    assert recover_adjacent(env['pid'])['settled_run_ids']==[ids[2]]
    assert db.one('SELECT cost FROM generation_runs WHERE id=?',(ids[2],))['cost']==pytest.approx(.009)

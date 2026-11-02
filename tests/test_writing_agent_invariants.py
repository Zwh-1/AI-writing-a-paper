import json,zipfile,sqlite3,io
from pathlib import Path
import httpx,pytest
from fastapi import HTTPException
from test_writing_agent import env,start,finish,fake_model,reviewed_candidate
from app import db,security,documents,provider,tasks,exports
from app.writing_agent import repository,adoption,sentences,verification,coordinator,context,paper_review,profiles
from app.writing_agent.common import state,blob,unblob,digest

def test_migration_rollback_keeps_old_version_and_no_partial_tables(env,monkeypatch):
    from app.writing_agent import migrations
    original=migrations.statements
    root=env['path']/'failed-migration';root.mkdir();monkeypatch.setattr(db,'DB_PATH',root/'workbench.sqlite3')
    def broken(script):
        yield next(iter(original(script)))
        raise RuntimeError('故障注入')
    monkeypatch.setattr(migrations,'statements',broken)
    with pytest.raises(RuntimeError):db.init()
    with db.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==3
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='writing_project_state'").fetchone()
    monkeypatch.setattr(migrations,'statements',original);db.init()
    assert db.one('PRAGMA user_version')['user_version']==migrations.CURRENT

def test_syntax_index_uses_real_typescript_and_literal_routes(env):
    units=db.rows('SELECT * FROM code_units WHERE snapshot_id=? AND symbol=?',(env['snapshot']['id'],'allowed'))
    assert units and units[0]['parser_version'].startswith('typescript-syntax.')
    assert units[0]['line_start']==1 and units[0]['line_end']==1

def test_search_does_not_silently_ignore_after_line_2000(env):
    (env['root']/'long.ts').write_text('\n'*2100+'export const late_marker = 7;\n',encoding='utf-8')
    snapshot=repository.capture(env['pid'],env['binding']['id'])
    hits=repository.search(env['pid'],snapshot['id'],'late_marker')
    assert any(h['line_start']>2000 and 'late_marker' in h['text'] for h in hits)

def test_working_scope_addition_is_not_claimed_stable(env,monkeypatch):
    original=Path.read_bytes;added=False
    def changing(p):
        nonlocal added
        raw=original(p)
        if p==env['root']/'gate.ts' and not added:
            added=True;(env['root']/'late.ts').write_text('export const changed=1;',encoding='utf-8')
        return raw
    monkeypatch.setattr(Path,'read_bytes',changing)
    snapshot=repository.capture(env['pid'],env['binding']['id'])
    assert snapshot['state']=='unstable'
    assert any(g['reason']=='scope_changed_during_capture' for g in snapshot['manifest']['gaps'])

def test_adoption_failure_rolls_back_text_mapping_and_receipt(env,monkeypatch):
    cid,a=reviewed_candidate(env,monkeypatch)
    def fault(*args,**kwargs):raise RuntimeError('依赖保存失败')
    monkeypatch.setattr(adoption,'dependency',fault)
    with pytest.raises(RuntimeError):adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'rollback-test-request')
    assert not db.rows('SELECT * FROM revisions')
    assert not db.rows('SELECT * FROM writing_adoptions')
    assert db.one('SELECT current_revision_id FROM sections WHERE id=?',(env['sid'],))['current_revision_id'] is None

def test_old_worker_cannot_reclaim_new_lease(env):
    s=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(s['id'],));db.execute("UPDATE tasks SET status='running',attempt=2 WHERE id=?",(step['task_id'],));db.execute("UPDATE writing_steps SET state='running',lease_epoch=2 WHERE id=?",(step['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(step['task_id'],));task['attempt']=1
    with pytest.raises(HTTPException):coordinator.process_step(task,{'step_id':step['id']})
    assert db.one('SELECT lease_epoch FROM writing_steps WHERE id=?',(step['id'],))['lease_epoch']==2

def test_cancel_prevents_any_model_request(env,monkeypatch):
    calls=fake_model(monkeypatch);s=start(env);coordinator.cancel(env['pid'],s['id']);tasks.run_one();assert not calls
    assert coordinator.get(env['pid'],s['id'])['status']=='cancelled'

def test_partial_merge_cannot_inherit_strict_review_without_recheck(env,monkeypatch):
    base=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('原有范围描述。'),None)
    cid,a=reviewed_candidate(env,monkeypatch);doc=unblob(adoption.candidate(cid,env['pid'])['document_blob']);node=doc['content'][0]['attrs']['nodeId']
    with pytest.raises(HTTPException):adoption.adopt(env['pid'],cid,{'base_revision_id':base['id'],'accept_policy':'strict','placement':'append','selected_node_ids':[node]},'strict-partial-not-reviewed')

def test_backup_has_new_objects_and_removes_local_root(env):
    b=exports.backup();raw=(env['path']/'backups'/b['filename']).read_bytes();restored=exports.restore_backup(raw);p=Path(restored['restored_data_directory'])
    c=sqlite3.connect(p/'workbench.sqlite3')
    try:
        row=c.execute('SELECT root_path_local,egress_policy FROM repository_bindings').fetchone();assert row==('','local_only')
        assert c.execute('SELECT count(*) FROM repository_snapshot_files').fetchone()[0]>0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    finally:c.close()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:assert not any(n.endswith('.dpapi') for n in z.namelist())

def test_old_sync_explicitly_rejects_new_objects_instead_of_dropping(env):
    from app.sync_bundle import make
    with pytest.raises(HTTPException) as caught:make(env['pid'])
    assert caught.value.status_code==409 and '不会静默丢弃' in str(caught.value.detail)

def test_strict_export_requires_current_full_review(env,monkeypatch):
    cid,a=reviewed_candidate(env,monkeypatch);adoption.adopt(env['pid'],cid,{'base_revision_id':None,'accept_policy':'strict'},'strict-export-adoption')
    with pytest.raises(HTTPException):paper_review.export_report(env['pid'],True)
    tasks.enqueue(env['pid'],'agent_paper_review',{'profile_id':env['profile']['id']});assert tasks.run_one()
    with pytest.raises(HTTPException):paper_review.export_report(env['pid'],True)
    current=paper_review.coverage(env['pid'])
    paper_review.human_paper_review(env['pid'],{'all_sections_reviewed':True,'reviewed_revisions':{s['section_id']:s['revision_id'] for s in current['sections']},'note':'测试明确模拟已通读全部当前章节并核对跨章前提，仅用于验证严格导出政策。'})
    report=paper_review.export_report(env['pid'],True);assert not report['unresolved']
    repository.set_egress(env['pid'],env['binding']['id'],'local_only')
    with pytest.raises(HTTPException):paper_review.export_report(env['pid'],True)

def test_transport_chat_tools_keeps_provider_message_state(env,monkeypatch):
    from app.writing_agent.provider_adapter import transport
    seen=[]
    def respond(request):
        body=json.loads(request.content);seen.append(body)
        return httpx.Response(200,json={'id':'r1','model':'fixture','choices':[{'message':{'role':'assistant','content':None,'reasoning_content':'protocol-state','tool_calls':[{'id':'call-1','type':'function','function':{'name':'read_code','arguments':'{}'}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':10,'completion_tokens':5}})
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(respond)))
    cfg=provider.config();cfg['thinking_enabled']=True
    with provider.request_scope(cfg,'test-key'):
        result=transport('调查','{"messages":[{"role":"user","content":"read"}],"tools":[]}',cfg)
    parsed=json.loads(result['text']);assert parsed['tool_calls'][0]['id']=='call-1' and parsed['message']['reasoning_content']=='protocol-state'
    assert seen[0]['thinking']['type']=='enabled'

def test_responses_transport_sets_reasoning_and_preserves_function_items(env,monkeypatch):
    from app.writing_agent.provider_adapter import transport
    seen=[]
    def respond(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'id':'r','model':'fixture','status':'completed','output':[{'type':'reasoning','id':'opaque'},{'type':'function_call','call_id':'c1','name':'list_repository','arguments':'{}'}],'usage':{'input_tokens':10,'output_tokens':8,'output_tokens_details':{'reasoning_tokens':5}}})
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(respond)))
    cfg={**provider.config(),'protocol':'responses','reasoning_effort':'low'}
    with provider.request_scope(cfg,'test-key'):r=transport('调查',json.dumps({'messages':[{'role':'user','content':'read'}],'tools':[]}),cfg)
    assert seen[0]['reasoning']=={'effort':'low'} and json.loads(r['text'])['items'][0]['id']=='opaque'

def test_configured_secret_is_blocked_before_context_persistence(env,monkeypatch):
    session=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],));key='this-is-a-synthetic-configured-secret';monkeypatch.setattr(security,'get_key',lambda profile='':key)
    with pytest.raises(HTTPException):context.save_context(step['id'],{'accidental_key':key},{},'system',provider.config())
    assert not db.rows('SELECT * FROM writing_contexts')

def test_sentence_results_preserve_document_order_not_random_ids(env):
    doc={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':'z-first'},'content':[{'type':'text','text':'第一段。'}]},{'type':'paragraph','attrs':{'nodeId':'a-second'},'content':[{'type':'text','text':'第二段。'}]}]}
    r=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),doc,None);a=sentences.analyze(env['pid'],db.loads(r['document']),revision_id=r['id'])
    assert [x['text'] for x in a['sentences']]==['第一段。','第二段。']

def test_requested_excerpt_is_not_mislabeled_as_truncated_snapshot(env):
    (env['root']/'multi.ts').write_text('const first=1;\nconst second=2;\n',encoding='utf-8');snapshot=repository.capture(env['pid'],env['binding']['id']);data=repository.read(env['pid'],snapshot['id'],'multi.ts',1,1)
    assert data['requested_range_complete'] and not data['truncated'] and data['more_lines_available'] and data['next_line']==2

def test_valid_utf8_original_replacement_characters_are_preserved_with_warning(env):
    raw='正文原样保留\ufffd，测试也可能故意包含该字符。'.encode('utf-8');(env['root']/'README.md').write_bytes(raw);snapshot=repository.capture(env['pid'],env['binding']['id']);data=repository.read(env['pid'],snapshot['id'],'README.md')
    assert data['text'].encode('utf-8')==raw and data['text_warnings']
    assert any(x['path']=='README.md' and x['text_warnings'] for x in snapshot['manifest']['files'])

def test_optional_missing_results_do_not_force_fabricated_runtime_evidence():
    from app.writing_agent.judgment import validate
    data={'task_goal':'仅描述源码','claims':[{'local_id':'J1','statement':'源码有合取条件','evidence_ids':['E1']}],'missing_evidence':['不计划评价的部署和性能记录尚未提供']}
    assert not validate(data,{'evidence_versions':{'E1':'v1'}})['blocking_gaps']
    data['claims'][0]['evidence_ids']=[];assert validate(data,{'evidence_versions':{'E1':'v1'}})['blocking_gaps']

def test_whole_paper_review_uses_bounded_original_text_chunks(env,monkeypatch):
    calls=fake_model(monkeypatch);profile=profiles.create(env['pid'],{'role':'verify','configuration':{'context_limit':12000,'max_output_tokens':1024}})
    text='\n'.join(f'第{i}段。'+('说明角色与启用条件，保留研究范围。'*65) for i in range(10));documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document(text),None)
    tasks.enqueue(env['pid'],'agent_paper_review',{'profile_id':profile['id']});tasks.run_one()
    row=db.one("SELECT * FROM writing_verification_runs WHERE stage='paper_review' ORDER BY rowid DESC LIMIT 1");assert row
    result=unblob(row['result_blob']);assert len(result['chunks'])>1 and result['navigation_summaries_are_evidence'] is False
    saved=documents.current(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)))
    expected={n['attrs']['nodeId'] for n in db.loads(saved['document'])['content']}
    assert {nid for chunk in result['chunks'] for nid in chunk['node_ids']}==expected

def test_indivisible_scope_over_budget_is_explicitly_incomplete(env,monkeypatch):
    calls=fake_model(monkeypatch);profile=profiles.create(env['pid'],{'role':'verify','configuration':{'context_limit':6000,'max_output_tokens':1024}})
    documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('大段原文'*10000),None)
    tasks.enqueue(env['pid'],'agent_paper_review',{'profile_id':profile['id']});tasks.run_one();r=db.one("SELECT * FROM writing_verification_runs WHERE stage='paper_review' ORDER BY rowid DESC LIMIT 1");result=unblob(r['result_blob'])
    assert not result['coverage_complete'] and result['unresolved_scopes'] and not calls

def test_manual_whole_paper_review_cannot_apply_to_changed_revisions(env):
    r=documents.save(db.one('SELECT * FROM sections WHERE id=?',(env['sid'],)),documents.text_document('当前文字。'),None)
    with pytest.raises(HTTPException):paper_review.human_paper_review(env['pid'],{'all_sections_reviewed':True,'note':'已经核对了研究问题、角色条件、章节关系和全部未决问题。','reviewed_revisions':{env['sid']:'outdated'}})
    assert not db.rows("SELECT * FROM writing_verification_runs WHERE stage='paper_review'")

def test_native_citation_requires_selected_original_source_and_survives_adoption_shape(env):
    from app.writing_agent.drafting import document
    ref='allowed-bibliography-id';data={'paragraphs':[{'local_id':'p1','text':'研究文献提出了有关观点。','reference_ids':[ref]}]}
    with pytest.raises(HTTPException):document(data)
    doc,_=document(data,references=[{'id':ref,'source_ids':['source-A']}]);p=doc['content'][0]
    assert p['attrs']['sourceIds']==['source-A'] and p['content'][-1]['type']=='citation' and p['content'][-1]['attrs']['citationId']==ref
    assert sentences.paragraph_text(p).endswith('\ufffc')

def test_annotations_batch_output_without_losing_any_sentence(env,monkeypatch):
    calls=fake_model(monkeypatch);original=provider.generate
    def longer(system,user,images=None):
        if '返回 {"paragraphs"' not in system:return original(system,user,images)
        answer={'paragraphs':[{'local_id':'p1','text':'。'.join('只有发行方且启用时允许操作'+str(i) for i in range(14))+'。'}],'missing_materials':[]}
        envelope={'agent_transport':True,'text':json.dumps(answer,ensure_ascii=False),'tool_calls':[],'message':None,'items':[],'finish':'stop','reported_model':'fixture'}
        return {'text':json.dumps(envelope,ensure_ascii=False),'usage':{'input_tokens':50,'output_tokens':100},'cost':0,'remote_id':'long-fixture','request_hash':digest(user)}
    monkeypatch.setattr(provider,'generate',longer);s=finish(env,start(env));assert s['status']=='needs_review',s
    a=db.one('SELECT * FROM document_analyses WHERE candidate_id=?',(s['candidates'][-1]['id'],));analysis=sentences.get(env['pid'],a['id'])
    assert analysis['coverage']['total']==14 and analysis['coverage']['unclassified']==0
    batches=[payload for system,payload in calls if '独立阅读冻结候选' in system]
    assert len(batches)==7 and all(len(b['sentences'])<=2 for b in batches)

def test_nested_sentence_findings_are_validated_and_preserved(env,monkeypatch):
    cid,a=reviewed_candidate(env,monkeypatch);doc=unblob(adoption.candidate(cid,env['pid'])['document_blob']);new=sentences.analyze(env['pid'],doc,candidate_id=cid);ids=[r['id'] for r in new['sentences']]
    c=adoption.candidate(cid,env['pid']);manifest=unblob(c['decision_blob'])['manifest'];data={'sentences':[{'sentence_id':identity,'role':'fact','claims':[{'statement':'代码条件描述','evidence_ids':[env['eid']],'support':'supported'}],'findings':[{'sentence_id':identity,'kind':'scope','message':'需保留静态观察边界','severity':'warning'}]} for identity in ids],'findings':[],'coverage_complete':True,'summary':'全部句子'}
    _,result=verification.apply_annotation(env['pid'],new['id'],data,manifest);assert len(result['findings'])==len(ids)

def test_human_approval_cannot_bypass_missing_evidence(env,monkeypatch):
    fake_model(monkeypatch);s=finish(env,start(env));cid=s['candidates'][-1]['id'];a=db.one('SELECT * FROM document_analyses WHERE candidate_id=?',(cid,));link=db.one('SELECT l.claim_version_id FROM sentence_claim_links l JOIN sentence_spans s ON s.id=l.sentence_id WHERE s.analysis_id=? LIMIT 1',(a['id'],));db.execute('DELETE FROM claim_evidence_links WHERE claim_version_id=?',(link['claim_version_id'],))
    with pytest.raises(HTTPException) as caught:verification.human_review(env['pid'],a['id'],{'decision':'approved','note':'即使人工给出足够长说明，也不能绕过事实论断缺少来源这一结构错误。'})
    assert caught.value.detail['code']=='REQUIRED_EVIDENCE_MISSING'

def test_closed_bill_group_preserves_unknown_per_request_costs(env):
    from app.writing_agent.billing import recover_closed_group
    cfg=provider.config();ids=[];before={'model':cfg['model'],'unit':'USD','requests':7,'input_tokens':100,'output_tokens':40,'amounts':{'cost':'1'},'remaining':'10'};after={**before,'requests':9,'input_tokens':160,'output_tokens':60,'amounts':{'cost':'1.02'},'remaining':'9.98'}
    for i in range(2):
        task=tasks.enqueue(env['pid'],'fixture_bill',{});identity=db.uid();ids.append(identity);meta={'credential_hash':'same','provider':cfg,'billing_interval_observation':{'before':before if i==0 else None,'after':after if i==1 else None}}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,task['id'],env['pid'],'completed',cfg['model'],cfg['protocol'],db.dumps(meta),db.dumps({'input_tokens':30,'output_tokens':10,'raw':{}}),None,2,db.now()))
    proof=recover_closed_group(env['pid']);assert proof['matched_closed_intervals'][0]['requests']==2
    assert all(db.one('SELECT cost FROM generation_runs WHERE id=?',(i,))['cost'] is None for i in ids)
    assert db.one('SELECT SUM(budget_charge) n FROM generation_budget_usage WHERE project_id=?',(env['pid'],))['n']==pytest.approx(.02)

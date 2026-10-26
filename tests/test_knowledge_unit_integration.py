import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,save
from test_writing_agent import env,start
from app import db,security,model_install,tasks,documents
from app import knowledge_units as units,knowledge_service as service
from app.writing_agent import tools,evidence,drafting,verification


@pytest.fixture(autouse=True)
def local_only_models(monkeypatch):
    old=model_install.status
    monkeypatch.setattr(model_install,'status',lambda name:{**old(name),'ready':False})


def library(c,pid,text='该方法仅适用于单用户环境。\n\n并发测试仍需额外验证。'):
    original=file(c,pid,text);parsed=units.build(pid,original['id']);return original,parsed,units.get(pid,parsed['units'][0]['id'])


def test_agent_reads_structured_literature_and_caches_versioned_evidence(env):
    original,parsed,item=library(env['client'],env['pid'],'credential identity only supports single-user cases.\n\nConcurrent tests are not run.')
    session=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    result=tools.execute(env['pid'],step['id'],'search_literature',{'query':'credential identity'},'structured-search',env['snapshot']['id'])
    hit=next(h for h in result['data']['hits'] if h.get('knowledge_unit_id'))
    read=tools.execute(env['pid'],step['id'],'read_literature',{'source_id':hit['id']},'structured-read',env['snapshot']['id'])
    observed=evidence.get(env['pid'],read['data']['evidence_id'])
    assert observed['origin_type']=='knowledge_unit' and observed['kind']=='literature'
    assert 'Concurrent tests are not run' in read['data']['text'] and read['data']['complete']
    assert tools.execute(env['pid'],step['id'],'read_literature',{'source_id':hit['id']},'structured-read',env['snapshot']['id'])==read
    units.dispute(env['pid'],item['id'],{'kind':'interpretation','note':'结论适用条件存在疑问','reviewer':'核对人'})
    with pytest.raises(HTTPException):tools.execute(env['pid'],step['id'],'read_literature',{'source_id':hit['id']},'structured-read',env['snapshot']['id'])


def test_agent_budget_limit_does_not_mint_evidence_for_unread_text(env):
    original,parsed,item=library(env['client'],env['pid'],'仅适用于单用户环境。'*200)
    session=start(env);step=db.one('SELECT * FROM writing_steps WHERE session_id=?',(session['id'],))
    result=tools.execute(env['pid'],step['id'],'read_literature',{'source_id':'unit:'+item['id'],'max_chars':200},'budget-read',env['snapshot']['id'])
    assert result['data']['status']=='needs_narrower_range' and 'evidence_id' not in result['data'] and 'text' not in result['data']


def test_agent_bibliographic_draft_keeps_first_class_unit_links(client):
    pid=project(client);original,parsed,item=library(client,pid);sid=section(client,pid)
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'条件研究'},'file_id':original['id']}).json()
    draft,_=drafting.document({'paragraphs':[{'local_id':'p','text':'仅限单用户。','reference_ids':[ref['id']]}]},references=[{**ref,'source_ids':[],'knowledge_unit_ids':[item['id']]}])
    assert draft['content'][0]['attrs']['knowledgeUnitIds']==[item['id']]
    documents.save(security.require_section(sid),draft,None)
    assert db.one('SELECT unit_id FROM citation_knowledge_links')['unit_id']==item['id']


def test_page_revocation_invalidates_saved_support_and_evidence(client):
    pid=project(client);original,parsed,item=library(client,pid);sid=section(client,pid)
    observed=evidence.sync_unit(pid,item['id'])
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'条件研究'},'file_id':original['id']}).json()
    draft,_=drafting.document({'paragraphs':[{'local_id':'p','text':'仅限单用户。','reference_ids':[ref['id']]}]},references=[{**ref,'source_ids':[],'knowledge_unit_ids':[item['id']]}])
    documents.save(security.require_section(sid),draft,None)
    db.execute("UPDATE citations SET support='supported'")
    page=db.one('SELECT id FROM pages WHERE file_id=?',(original['id'],))
    response=client.patch('/api/v1/pages/'+page['id'],json={'review_state':'not_applicable','review_note':'原页不再适用'})
    assert response.status_code==200
    assert db.one('SELECT support FROM citations')['support']=='unknown'
    assert evidence.get(pid,observed['id'])['validity']=='stale'


def test_selection_changes_do_not_revoke_other_chapters_evidence(client):
    pid=project(client);original,parsed,item=library(client,pid);sid=section(client,pid)
    observed=evidence.sync_unit(pid,item['id'])
    units.select_units(pid,sid,{'unit_ids':[item['id']]})
    assert evidence.get(pid,observed['id'])['validity']=='current'


def test_automatic_rebuild_respects_manual_head(client):
    pid=project(client);original,parsed,item=library(client,pid)
    changed=units.revise(pid,item['id'],{'expected_hash':item['content_hash'],'text':item['text'],'reviewer':'核对人','note':'人工选择的分段版本'})
    service.build_project(pid,{'file_id':original['id'],'preserve_head':True})
    assert units.get(pid,changed['id'])['current']


def test_version_comparison_does_not_pick_semantic_winner(client):
    pid=project(client);original,parsed,item=library(client,pid)
    alternate=units.build(pid,original['id'],strategy='page',activate=False)
    response=client.get(f'/api/v1/projects/{pid}/evidence-knowledge/versions/{parsed["id"]}/compare',params={'other':alternate['id']})
    assert response.status_code==200 and response.json()['semantic_winner'] is None
    assert len(response.json()['left']['units'])==2 and len(response.json()['right']['units'])==1


def test_bad_api_types_return_validation_error(client):
    pid=project(client);original,parsed,item=library(client,pid);base=f'/api/v1/projects/{pid}/evidence-knowledge'
    for route,body in [('/build',{'strategy':[]}),('/search',{'query':'单用户','limit':False}),('/units/'+item['id']+'/read',{'before':[]})]:
        assert client.post(base+route,json=body).status_code==422


def test_literature_cannot_certify_this_projects_execution(client):
    pid=project(client);original,parsed,item=library(client,pid)
    observed=evidence.sync_unit(pid,item['id'])
    assert verification.execution_evidence_missing(pid,'本项目测试已通过。',[observed['id']])
    assert not verification.execution_evidence_missing(pid,'该文献报告测试已通过。',[observed['id']])


def test_old_overlong_source_returns_no_partial_text(client):
    pid=project(client);original=file(client,pid,'普通文献原文。')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(original['id'],))
    db.execute('UPDATE sources SET text=? WHERE id=?',('长原文。'*1000,source['id']))
    result=service.read_source(pid,source['id'],max_chars=200)
    assert result['status']=='needs_narrower_range' and 'text' not in result


def test_agent_context_preserves_unit_and_bibliographic_provenance(env):
    from app.writing_agent import context
    original,parsed,item=library(env['client'],env['pid'])
    ref=env['client'].post(f'/api/v1/projects/{env["pid"]}/references',json={'metadata':{'title':'文献依据'},'file_id':original['id']}).json()
    observed=evidence.sync_unit(env['pid'],item['id'])
    payload,manifest=context.assemble(env['pid'],env['sid'],env['contract']['id'],env['profile'],evidence_ids=[observed['id']])
    book=next(r for r in payload['references'] if r['id']==ref['id'])
    assert book['knowledge_unit_ids']==[item['id']]
    assert manifest['source_evidence'][observed['id']]['knowledge_unit_id']==item['id']
    assert item['id'] in manifest['knowledge_unit_versions']


def test_open_source_dispute_cannot_be_overridden_by_models_support_label(client):
    from app.writing_agent import sentences
    pid=project(client);original,parsed,item=library(client,pid)
    units.dispute(pid,item['id'],{'kind':'boundary','note':'适用条件是否覆盖下段存在争议','reviewer':'核对人'})
    observed=evidence.sync_unit(pid,item['id'])
    doc=documents.text_document('文献的方法仅适用于单用户环境。');sid=section(client,pid)
    revision=documents.save(security.require_section(sid),doc,None)
    analysis=sentences.analyze(pid,db.loads(revision['document']),revision_id=revision['id'])
    spans=sentences.get(pid,analysis['id'])['sentences']
    data={'sentences':[{'sentence_id':spans[0]['id'],'role':'fact','claims':[{'statement':'仅适用于单用户','evidence_ids':[observed['id']],'support':'supported'}]}],'coverage_complete':True}
    identity,result=verification.apply_annotation(pid,analysis['id'],data,{'evidence_versions':{observed['id']:observed['origin_version']}})
    assert any(f['kind']=='unresolved_source_context' and f['severity']=='blocker' for f in result['findings'])
    assert result['sentences'][0]['claims'][0]['support']=='unknown'


def test_export_snapshot_freezes_unit_scope_and_claim_records(client):
    from app.export_checks import freeze
    pid=project(client);original,parsed,item=library(client,pid);sid=section(client,pid)
    ref=client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'条件研究'},'file_id':original['id']}).json()
    draft,_=drafting.document({'paragraphs':[{'local_id':'p','text':'仅限单用户。','reference_ids':[ref['id']]}]},references=[{**ref,'source_ids':[],'knowledge_unit_ids':[item['id']]}])
    documents.save(security.require_section(sid),draft,None)
    frozen=freeze(pid)
    assert frozen['knowledge_units'][item['id']]['text']==item['text']
    assert frozen['knowledge_links'][0]['unit_hash']==item['content_hash']


def test_cross_encoder_scores_index_excerpt_without_replacing_full_original(client,monkeypatch):
    from app import retrieval
    pid=project(client);text='适用范围需要保留。'*80+'尾部关键词验证。';original,parsed,item=library(client,pid,text)
    old=model_install.status;monkeypatch.setattr(model_install,'status',lambda name:{**old(name),'ready':'reranker' in name})
    captured=[]
    def score(query,candidates,model):
        captured.extend(candidates)
        return [{**r,'rerank_score':1.0,'rerank_rank':i+1} for i,r in enumerate(candidates)]
    monkeypatch.setattr(retrieval,'cross_encoder_rerank',score)
    result=service.search(pid,'尾部关键词')
    assert result['unit_pipeline']['rerank']['status']=='completed'
    assert any('尾部关键词' in r['text'] and len(r['text'])<=240 for r in captured)
    assert next(r for r in result['items'] if r.get('knowledge_unit_id')==item['id'])['text']==text


def test_rerank_failure_is_explicit_and_does_not_discard_original(client,monkeypatch):
    from app import retrieval
    pid=project(client);original,parsed,item=library(client,pid)
    old=model_install.status;monkeypatch.setattr(model_install,'status',lambda name:{**old(name),'ready':'reranker' in name})
    def fail(*a,**k):raise ValueError('合成重排故障')
    monkeypatch.setattr(retrieval,'cross_encoder_rerank',fail)
    result=service.search(pid,'单用户')
    assert result['items'] and result['unit_pipeline']['rerank']['status']=='failed'
    assert any('合成重排故障' in w for w in result['warnings'])


def test_excluded_unit_cannot_be_bypassed_via_legacy_source(client):
    pid=project(client);original,parsed,item=library(client,pid)
    source=db.one('SELECT id FROM sources WHERE file_id=?',(original['id'],))
    observed=evidence.sync_source(pid,source['id'],'literature')
    units.review(pid,item['id'],{'state':'excluded','expected_hash':item['content_hash'],'reviewer':'核对人','note':'该段不适用于本研究'})
    assert not service.search(pid,'单用户',remote=True)['items']
    with pytest.raises(HTTPException):service.read_source(pid,source['id'],remote=True)
    assert not evidence.allowed_remote(pid,observed['id'])
    assert service.search(pid,'单用户',collection='originals')['items']


def test_excluded_neighbour_is_reported_as_missing_context(client):
    pid=project(client);original,parsed,item=library(client,pid)
    second=units.get(pid,parsed['units'][1]['id'])
    units.review(pid,item['id'],{'state':'excluded','expected_hash':item['content_hash'],'reviewer':'核对人','note':'需要排除原段落'})
    read=units.read(pid,second['id'],remote=True)
    assert not read['context_complete'] and item['id'] in read['omitted_context_ids']
    assert item['id'] not in {r['id'] for r in read['passages']}


def test_remote_send_checks_exclusion_again_before_budget_and_provider(client,monkeypatch):
    from app import provider
    pid=project(client);original,parsed,item=library(client,pid)
    source=db.one('SELECT id FROM sources WHERE file_id=?',(original['id'],))
    units.review(pid,item['id'],{'state':'excluded','expected_hash':item['content_hash'],'reviewer':'核对人','note':'禁止继续用于写作'})
    calls=[];monkeypatch.setattr(provider,'generate',lambda *a,**k:calls.append(1));monkeypatch.setattr(provider,'get_key',lambda:'test-key')
    task=tasks.enqueue(pid,'write',{})
    with pytest.raises(ValueError,match='已被排除'):tasks._model_call(task,'核对','原文',{'source_ids':[source['id']],'file_ids':[original['id']]})
    assert not calls and not db.rows('SELECT * FROM generation_runs')


def test_combined_retrieval_cross_encoder_runs_once(client,monkeypatch):
    from app import retrieval
    from test_reference_knowledge import fake_neural
    pid=project(client);original,parsed,item=library(client,pid,'credential identity is limited to single-user use.')
    fake_neural(monkeypatch)
    retrieval.build_index(pid,knowledge=True);service.index_units(pid)
    calls=[];scorer=retrieval.cross_encoder_rerank
    def counted(*args):calls.append(1);return scorer(*args)
    monkeypatch.setattr(retrieval,'cross_encoder_rerank',counted)
    result=service.search(pid,'credential identity')
    assert result['items'] and len(calls)==1
    assert result['unit_pipeline']['rerank']['status']=='completed'


def test_writing_preparation_uses_one_shared_retrieval(client,monkeypatch):
    pid=project(client);original,parsed,item=library(client,pid);sid=section(client,pid)
    calls=[];search=service.search
    def counted(*args,**kwargs):calls.append(1);return search(*args,**kwargs)
    monkeypatch.setattr(service,'search',counted)
    result=service.prepare_writing(pid,'单用户',sid)
    assert result['knowledge_units'] and len(calls)==1


def test_native_text_never_inherits_appended_manual_transcript(client):
    pid=project(client);original=file(client,pid,'这是不可改写的原生文字。')
    page=db.one('SELECT id FROM pages WHERE file_id=?',(original['id'],))
    response=client.patch('/api/v1/pages/'+page['id'],json={'transcript':'人工补充尚待核对，不是原生文字。','review_state':'unreviewed'})
    assert response.status_code==200
    parsed=units.build(pid,original['id'])
    rows=[units.get(pid,r['id']) for r in parsed['units']]
    native=next(r for r in rows if r['origin']=='native_extraction')
    transcript=next(r for r in rows if r['origin']=='manual')
    assert '人工补充' not in native['text'] and '人工补充' in transcript['text']
    assert transcript['review_state']=='pending'

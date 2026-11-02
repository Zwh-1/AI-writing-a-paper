"""Regression tests for latency fixes and document coverage, using original ACLs."""
import sqlite3
import pytest
from test_workbench import client,project,file
from app import db,retrieval,knowledge_router as router,knowledge_units as units
from app import knowledge_lineage as lineage,knowledge_compiler as compiler,knowledge_wiki as wiki
from fastapi import HTTPException


def test_read_session_reuses_and_closes_connection(client,monkeypatch):
    project(client);original=db.connect;connections=[]
    def connect():
        value=original();connections.append(value);return value
    monkeypatch.setattr(db,'connect',connect)
    with db.read_session():
        assert db.one('SELECT 1 AS n')['n']==1
        with db.read_session():
            assert db.one('SELECT 2 AS n')['n']==2
        assert len(connections)==1
        assert not connections[0].in_transaction
    with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')
    assert db._READ_SESSION.get() is None


def test_read_session_sees_separate_commits_and_preserves_rollback(client):
    pid=project(client)
    with db.read_session():
        assert db.setting('read-test') is None
        db.set_setting('read-test','committed')
        assert db.setting('read-test')=='committed'
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.set_setting('read-test','rolled-back')
                assert db.setting('read-test')=='rolled-back'
                raise RuntimeError()
        assert db.setting('read-test')=='committed'
    assert db.one('SELECT id FROM projects WHERE id=?',(pid,))


def test_read_session_exception_closes_and_next_request_is_fresh(client,monkeypatch):
    original=db.connect;seen=[]
    def connect():
        value=original();seen.append(value);return value
    monkeypatch.setattr(db,'connect',connect)
    with pytest.raises(ValueError):
        with db.read_session():
            db.one('SELECT 1');raise ValueError()
    with pytest.raises(sqlite3.ProgrammingError):seen[0].execute('SELECT 1')
    with db.read_session():assert db.one('SELECT 2 AS n')['n']==2
    assert len(seen)==2


def test_limited_reading_plan_does_not_cut_off_other_documents():
    items=[{'file_id':'long-paper','unit_id':'unit-'+str(i),'intent':'research','char_start':0,'char_end':100} for i in range(24)]
    items+=[{'file_id':'other-'+str(i),'unit_id':'other-unit-'+str(i),'intent':'research','char_start':0,'char_end':100} for i in range(5)]
    selected=router.balanced_plan(items,16)
    assert len(selected)==16 and len({p['file_id'] for p in selected})==6
    assert selected[0]['unit_id']=='unit-0' and selected[1]['unit_id']=='other-unit-0'
    assert len(router.balanced_plan(items+[items[0]],40))==29


def test_project_records_receive_independent_reading_slots():
    items=[{'file_id':None,'evidence_id':str(i),'intent':'project','char_start':0,'char_end':10} for i in range(20)]
    assert len(router.balanced_plan(items,16))==16
    assert router.balanced_plan(items,16)[-1]['evidence_id']=='15'


def test_query_vector_cache_is_bounded_and_model_versioned(client,monkeypatch):
    import numpy as np
    version=['v1'];calls=[]
    monkeypatch.setattr(retrieval,'model_key',lambda name:name+':'+version[0])
    def embed(texts,name):calls.extend(texts);return [np.asarray([1.,2.],dtype=np.float32) for _ in texts]
    monkeypatch.setattr(retrieval,'embed',embed)
    first=retrieval.query_embedding('条件','model');first[0]=999
    second=retrieval.query_embedding('条件','model')
    assert second[0]==1 and calls==['条件']
    version[0]='v2';retrieval.query_embedding('条件','model')
    assert calls==['条件','条件']
    for i in range(140):retrieval.query_embedding(str(i),'model')
    assert len(retrieval._QUERY_VECTORS)==128
    monkeypatch.setattr(retrieval,'model_key',lambda name:(_ for _ in ()).throw(ValueError('model unavailable')))
    with pytest.raises(ValueError):retrieval.query_embedding('条件','model')


def test_rerank_scores_reused_only_for_identical_model_question_and_text(client,monkeypatch):
    calls=[];version=['1']
    class Scorer:
        def rerank(self,query,texts,**kwargs):calls.append((query,texts));return list(range(1,len(texts)+1))
    monkeypatch.setattr(retrieval,'model_instance',lambda *args,**kwargs:Scorer())
    monkeypatch.setattr(retrieval,'model_key',lambda name:name+':'+version[0])
    candidates=[{'id':'a','text':'单用户条件','fusion_rank':1},{'id':'b','text':'并发条件','fusion_rank':2}]
    first=retrieval.cross_encoder_rerank('条件',candidates,'model')
    second=retrieval.cross_encoder_rerank('条件',candidates,'model')
    assert [r['id'] for r in first]==[r['id'] for r in second]
    assert len(calls)==1 and all(r['rerank_cache_hit'] for r in second)
    modified=[{**candidates[0],'text':'已更新条件'},candidates[1]]
    retrieval.cross_encoder_rerank('条件',modified,'model')
    assert len(calls[-1][1])==1 and '已更新条件' in calls[-1][1][0]
    version[0]='2';retrieval.cross_encoder_rerank('条件',modified,'model')
    assert len(calls[-1][1])==2
    retrieval.cross_encoder_rerank('不同问题',modified,'model')
    assert calls[-1][0]=='不同问题'


def test_invalid_rerank_response_not_cached(client,monkeypatch):
    class Scorer:
        def rerank(self,*args,**kwargs):return [float('nan')]
    monkeypatch.setattr(retrieval,'model_instance',lambda *args,**kwargs:Scorer())
    monkeypatch.setattr(retrieval,'model_key',lambda name:'fixture-model')
    with pytest.raises(ValueError):retrieval.cross_encoder_rerank('条件',[{'id':'a','text':'原文','fusion_rank':1}],'model')
    assert not retrieval._RERANK_SCORES


def test_query_cache_does_not_cache_source_access_decisions(client,monkeypatch):
    pid=project(client);source=file(client,pid,'单用户场景下采用分层方法。')
    parsed=units.build(pid,source['id']);obs=compiler.observe_unit(pid,parsed['units'][0]['id']);ref=lineage.ref(pid,'observation',obs['id'])
    page=wiki.create_page(pid,{'slug':'access-test','title':'单用户','kind':'topic','namespace':'literature'})
    revision=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'one','kind':'excerpt','text':obs['text'],'sources':[ref]}]},'literal')
    wiki.publish(pid,page['id'],{'base_version_id':None,'version_id':revision['version']['id']})
    assert wiki.get_page(pid,page['id'],remote=True)['current']
    client.patch('/api/v1/files/'+source['id'],json={'egress':'local_only'})
    with pytest.raises(HTTPException):wiki.get_page(pid,page['id'],remote=True)
    assert wiki.get_page(pid,page['id'])['current']


def test_read_only_query_reports_actual_source_files_and_timings(client,monkeypatch):
    pid=project(client);source=file(client,pid,'明确限制：该方法只适用于单用户环境。')
    units.build(pid,source['id'])
    monkeypatch.setattr('app.knowledge_service.search',lambda *args,**kwargs:{'items':[{'file_id':source['id'],'knowledge_unit_id':db.one('SELECT id FROM knowledge_units')['id']}]})
    result=router.query(pid,{'query':'单用户','namespace':'literature','variant':'A'})
    assert result['file_ids']==[source['id']]
    assert result['reading_plan'] and result['timings_ms']['total']>=0


def test_claim_selection_balances_before_applying_limit(client):
    pid=project(client)
    a=file(client,pid,'第一篇研究的单用户条件。');b=file(client,pid,'第二篇研究的并发条件。')
    pa=units.build(pid,a['id']);pb=units.build(pid,b['id'])
    oa=compiler.observe_unit(pid,pa['units'][0]['id']);ob=compiler.observe_unit(pid,pb['units'][0]['id'])
    make=lambda identity,obs:{'claim_version_id':identity,'sources':[{'type':'observation','id':obs['id'],'version':obs['content_hash']}]}
    items=[make(str(i),oa) for i in range(25)]+[make('other',ob)]
    selected=router.balanced_claims(pid,items,20)
    assert len(selected)==20 and selected[1]['claim_version_id']=='other'


@pytest.mark.parametrize('block',[{'kind':'synthesis'}, {'kind':'unknown','text':'文字'}, {'kind':'synthesis','text':' '}, 'invalid'])
def test_malformed_generated_answer_does_not_create_knowledge_artifact(client,monkeypatch,block):
    pid=project(client);source=file(client,pid,'原始研究只适用于单用户环境。');parsed=units.build(pid,source['id'])
    uid=parsed['units'][0]['id'];task=__import__('app.tasks',fromlist=['enqueue']).enqueue(pid,'knowledge_answer',{})
    def response(task,operation,payload,refs):
        supplied={**block,'sources':payload['allowed_sources']} if isinstance(block,dict) else block
        return {'blocks':[supplied]},'completed-existing-model-run'
    monkeypatch.setattr('app.knowledge_model_bridge.json_call',response)
    context={'reading_plan':[{'file_id':source['id'],'unit_id':uid,'char_start':0,'char_end':len(units.get(pid,uid)['text']),'intent':'research'}],'wiki':[],'knowledge_refs':[]}
    with pytest.raises(HTTPException) as error:router.answer(task,{'query':'限制'},context)
    assert error.value.detail['code']=='KNOWLEDGE_OUTPUT_INVALID'
    assert not db.rows("SELECT * FROM knowledge_artifacts WHERE kind='knowledge_answer'")


def test_knowledge_status_explains_billing_gate_and_keeps_local_work(client,monkeypatch):
    from app import expense_evidence,provider,knowledge_api
    pid=project(client)
    monkeypatch.setattr(expense_evidence,'DEFAULT_POLICY',{'require_pricing_basis':True,'block_on_unresolved':True})
    monkeypatch.setattr(provider,'get_key',lambda:'fixture-gate-key')
    stamp=db.now();db.execute('INSERT INTO generation_runs(id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (db.uid(),pid,'outcome_unknown','fixture-model','openai','{}',2,stamp))
    status=knowledge_api.status(pid)
    assert status['billing_gate']['paid_requests_allowed'] is False
    assert status['billing_gate']['local_work_allowed']
    assert any('待核实' in r for r in status['billing_gate']['reasons'])
    assert status['budget']['unknown_reserved']==2


@pytest.mark.parametrize('change',['permission','content'])
def test_remote_query_rechecks_source_after_slow_original_retrieval(client,monkeypatch,change):
    pid=project(client);source=file(client,pid,'该研究仅适用于单用户场景。');parsed=units.build(pid,source['id'])
    obs=compiler.observe_unit(pid,parsed['units'][0]['id']);ref=lineage.ref(pid,'observation',obs['id'])
    page=wiki.create_page(pid,{'slug':'late-recheck','title':'单用户','kind':'topic','namespace':'literature'})
    revision=wiki.revise(pid,page['id'],{'base_version_id':None,'blocks':[{'key':'x','kind':'excerpt','text':obs['text'],'sources':[ref]}]},'literal')
    wiki.publish(pid,page['id'],{'base_version_id':None,'version_id':revision['version']['id']})
    def after_inference(*args,**kwargs):
        if change=='permission':db.execute("UPDATE files SET egress='local_only' WHERE id=?",(source['id'],))
        else:db.execute("UPDATE sources SET text='原文内容已经变化' WHERE file_id=?",(source['id'],))
        return {'items':[]}
    monkeypatch.setattr('app.knowledge_service.search',after_inference)
    with lineage.validation_scope():
        assert lineage.version_of(pid,'observation',obs['id'],True)==ref['version']
        with pytest.raises(HTTPException):router.query(pid,{'query':'单用户','namespace':'literature','variant':'C'},True)


def test_disabled_semantic_retrieval_never_runs_unit_vector_inference(client,monkeypatch):
    from test_reference_knowledge import fake_neural
    from app import reference_knowledge,knowledge_service
    fake_neural(monkeypatch)
    pid=project(client);source=file(client,pid,'credential identity explicit condition')
    units.build(pid,source['id']);reference_knowledge.build_index(pid);knowledge_service.index_units(pid)
    db.set_setting('reference_knowledge:'+pid,{'semantic_enabled':False,'rerank_enabled':False})
    def forbidden(*args,**kwargs):raise AssertionError('Disabled semantic retrieval must not run local inference')
    monkeypatch.setattr(retrieval,'query_embedding',forbidden)
    monkeypatch.setattr(retrieval,'model_instance',forbidden)
    result=knowledge_service.search(pid,'credential identity')
    assert result['items'] and result['mode']=='keyword'


@pytest.mark.parametrize('mapping',[{},None,{'irrelevant':None},'string',[]])
def test_supersession_filter_preserves_corpus_with_empty_or_scalar_metadata(client,mapping):
    from app.document_observations import current_source_sql
    pid=project(client);source=file(client,pid,'真实原文应保留。')
    db.set_setting('document_observation_supersessions',mapping)
    assert db.rows('SELECT s.id FROM sources s WHERE s.file_id=? AND '+current_source_sql(),(source['id'],))


def test_supersession_filter_changes_immediately_and_never_restores_old_source(client):
    from app.document_observations import current_source_sql
    pid=project(client);source=file(client,pid,'旧原文和完整原文保持各自出处。')
    sid=db.one('SELECT id FROM sources WHERE file_id=?',(source['id'],))['id']
    with db.read_session():
        assert db.rows('SELECT s.id FROM sources s WHERE s.id=? AND '+current_source_sql(),(sid,))
        db.set_setting('document_observation_supersessions',{sid:None})
        assert not db.rows('SELECT s.id FROM sources s WHERE s.id=? AND '+current_source_sql(),(sid,))


def test_failed_legacy_answer_import_does_not_leave_an_empty_wiki_page(client):
    from app import config,tasks
    from app.knowledge_api import save_answer_api
    pid=project(client);task=tasks.enqueue(pid,'knowledge_answer',{});identity=db.uid()
    folder=config.DATA/'knowledge'/'answers';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(identity+'.json');data={'blocks':[{'kind':'synthesis','sources':[]}]};path.write_text(db.dumps(data),encoding='utf-8')
    db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,task['id'],'knowledge_answer',lineage.digest(data),str(path),'{}','ready',db.now()))
    with pytest.raises(HTTPException):save_answer_api(pid,identity,{'namespace':'literature','kind':'topic','title':'应回滚的空页','slug':'failed-import'})
    assert not db.one("SELECT id FROM wiki_pages WHERE project_id=? AND slug='failed-import'",(pid,))


def test_writing_model_receives_only_graph_claims_covered_by_manifest(client,monkeypatch):
    from test_workbench import section
    pid=project(client);sid=section(client,pid);db.set_setting('graph_wiki_enabled:'+pid,True)
    refs=[{'type':'assertion','id':'selected','version':'version-1'}]
    graph={'assertions':[{'claim_version_id':'selected','subject_id':'node-1','object_id':None},{'claim_version_id':'other','subject_id':'node-2','object_id':None}],
           'nodes':[{'id':'node-1'},{'id':'node-2'}],'edges':[{'claim_id':'selected'},{'claim_id':'other'}]}
    monkeypatch.setattr(router,'query',lambda *args,**kwargs:{'graph':graph,'originals':[],'reading_plan':[],'knowledge_refs':refs,'wiki':[],'route':'relation','scope_note':'test','gaps':[]})
    monkeypatch.setattr(router,'prepare_readings',lambda *args,**kwargs:{'reading_ids':[],'gaps':[]})
    monkeypatch.setattr('app.document_preparation.prepare',lambda *args,**kwargs:{'gaps':[],'document_readings':[]})
    result=router.prepare_writing(pid,'方法',sid)
    assert result['knowledge_refs']==refs
    assert [a['claim_version_id'] for a in result['knowledge_context']['graph']['assertions']]==['selected']
    assert [a['id'] for a in result['knowledge_context']['graph']['nodes']]==['node-1']
    assert result['knowledge_context']['graph']['edges']==[{'claim_id':'selected'}]

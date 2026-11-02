import contextlib
import httpx
import pytest
from test_workbench import client,project,file
from test_graph_wiki import no_remote_models
from app import db,provider,provider_stream,security,tasks,knowledge_units
from app import knowledge_compiler as compiler,knowledge_lineage as lineage

@pytest.mark.parametrize('error_type,unknown',[(httpx.ConnectTimeout,False),(httpx.ConnectError,False),(httpx.ReadTimeout,True),(httpx.WriteTimeout,True)])
def test_transport_phase_classifies_only_proven_connection_failures(client,monkeypatch,error_type,unknown):
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kw):raise error_type('synthetic failure')
    monkeypatch.setattr(provider,'client',lambda c:Connection())
    with provider.request_scope({**provider.config(),'model':'deepseek-test'},'fake-transport-key'):
        with pytest.raises(provider.ProviderError) as raised:provider.generate('system','test')
    assert raised.value.unknown is unknown
    if not unknown:assert raised.value.failure_phase=='connect_before_http'

def test_stream_error_after_response_remains_unknown(client,monkeypatch):
    class Response:
        status_code=200;headers={'content-type':'text/event-stream'}
        def iter_lines(self):raise httpx.ConnectError('after response')
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextlib.contextmanager
        def stream(self,*args,**kw):yield Response()
    monkeypatch.setattr(provider,'client',lambda c:Connection())
    with provider.request_scope({**provider.config(),'model':'deepseek-test'},'fake-transport-key'):
        with pytest.raises(provider.ProviderError) as raised:provider_stream.generate('system','test')
    assert raised.value.unknown

def cache_fixture(client,monkeypatch,unknown=False):
    pid=project(client);original=file(client,pid,'仅适用于单用户环境。');parsed=knowledge_units.build(pid,original['id']);obs=compiler.observe_unit(pid,parsed['units'][0]['id']);refs=[lineage.ref(pid,'observation',obs['id'])];task=tasks.enqueue(pid,'knowledge_test',{});calls=[]
    def model(task,system,user,manifest):
        calls.append(manifest);rid=db.uid();metadata={**manifest,'provider':provider.config(),'output_blob':security.put_blob(b'{"ok":true}')}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,task['id'],pid,'outcome_unknown' if unknown else 'completed','test','openai',db.dumps(metadata),'{}',None if unknown else .001,.02,db.now()))
        if unknown:raise provider.ProviderError('synthetic unknown',unknown=True)
        return {'text':'{"ok":true}','usage':{},'cost':.001,'run_id':rid}
    monkeypatch.setattr(tasks,'model_call',model)
    return pid,original,refs,task,calls

def test_known_response_cache_reused_but_revocation_still_blocks(client,monkeypatch):
    from app.knowledge_model_bridge import complete
    pid,source,refs,task,calls=cache_fixture(client,monkeypatch)
    first=complete(task,'test',{'question':'范围'},refs);second=complete(task,'test',{'question':'范围'},refs)
    assert first['run_id']==second['run_id'] and second['cached'] and len(calls)==1
    client.patch('/api/v1/files/'+source['id'],json={'egress':'local_only'})
    with pytest.raises(__import__('fastapi').HTTPException):complete(task,'test',{'question':'范围'},refs)
    assert len(calls)==1

def test_unknown_response_does_not_send_again(client,monkeypatch):
    from app.knowledge_model_bridge import complete
    pid,source,refs,task,calls=cache_fixture(client,monkeypatch,True)
    with pytest.raises(provider.ProviderError):complete(task,'test',{},refs)
    with pytest.raises(__import__('fastapi').HTTPException):complete(task,'test',{},refs)
    assert len(calls)==1

def test_guidance_ocr_includes_actual_native_slide_context(client):
    pid=project(client);original=file(client,pid,'某系统已完成并发测试。',role='example');parsed=knowledge_units.build(pid,original['id'],allow_material=True);uid=parsed['units'][0]['id'];row=knowledge_units.get(pid,uid,intent='guidance')
    db.execute("UPDATE files SET extension='.pptx' WHERE id=?",(original['id'],));db.execute("UPDATE knowledge_units SET origin='windows_ocr' WHERE id=?",(uid,))
    native=db.uid();text='教学反例：下面展示的实验结果不能直接当成本项目成果。'
    db.execute('INSERT INTO knowledge_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(native,parsed['id'],999,'slide_topic',text,lineage.digest(text),db.dumps(row['locator']),'[]','pptx_slide','[]','pending',None))
    obs=compiler.observe_unit(pid,uid)
    assert '教学反例' in db.loads(obs['metadata_json'])['native_slide_context']['text']
    assert any(r['type']=='unit' and r['id']==native for r in lineage.references(pid,'observation',obs['id']))

def test_syntax_repair_never_executes_or_invents_values():
    from app.knowledge_json import parse
    data,canonical,repaired=parse("{'score': 0, 'items': ['原文'], 'missing': None}")
    assert repaired and data=={'score':0,'items':['原文'],'missing':None}
    import json
    assert json.loads(canonical)==data
    with pytest.raises((ValueError,SyntaxError)):parse("__import__('os').system('not executed')")
    with pytest.raises(ValueError):parse('{"score": NaN}')


def test_reason_quote_normalization_preserves_model_text_and_source_fields():
    from app.knowledge_json import parse
    value='{"observations":[{"observation_id":"source-a","no_claim_reason":"原文在"According to this"处中断","entities":[],"claims":[]}]}'
    parsed,canonical,repaired=parse(value)
    assert repaired and parsed=={'observations':[{'observation_id':'source-a',
        'no_claim_reason':'原文在"According to this"处中断','entities':[],'claims':[]}]}
    with pytest.raises((ValueError,SyntaxError)):
        parse('{"no_claim_reason":"正常","other":"未转义"引句"","claims":[]}')
    with pytest.raises((ValueError,SyntaxError)):
        parse('{"claims":[{"statement":"未转义"引句""}]}')
    with pytest.raises((ValueError,SyntaxError)):
        parse('{"no_claim_reason":"残缺"内容","entities":[],"claims":[')

def test_dual_use_observations_remain_independent(client):
    from app import material_policy
    from app.knowledge_builds import preview
    pid=project(client);f=file(client,pid,'这篇研究可以用于方法讨论，也可以用作写作示例。',role='example')
    db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?)',(f['id'],'research_publication','合成研究原件',db.now()))
    db.execute('INSERT INTO file_usage_additions VALUES(?,?,?,?)',(f['id'],'research','合成追加用途',db.now()))
    parsed=knowledge_units.build(pid,f['id'],allow_material=True);uid=parsed['units'][0]['id']
    research=compiler.observe_unit(pid,uid,intent='research');guidance=compiler.observe_unit(pid,uid,intent='guidance')
    assert research['id']!=guidance['id'] and research['namespace']=='literature' and guidance['namespace']=='guidance'
    assert preview(pid,{'mode':'literal','namespace':'guidance'})['files'][0]['namespace']=='guidance'

def test_project_baseline_reads_original_evidence_without_graph(client):
    from app.writing_agent import evidence
    from app.knowledge_router import query
    pid=project(client);e=evidence.create(pid,{'kind':'author_statement','origin_type':'author','text':'系统实现尚在计划阶段，未执行性能测试。'})
    result=query(pid,{'query':'系统实现和性能测试','namespace':'project','variant':'A','include_originals':False},True)
    assert any(p.get('evidence_id')==e['id'] for p in result['reading_plan'])
    assert e['kind']=='author_statement' and not db.rows("SELECT * FROM evidence_items WHERE kind='test_run'")

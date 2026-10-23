"""Advanced behavior tests: real math/ONNX/statistics plus isolated transport and collaboration fixtures."""
import copy,hashlib,io,json,zipfile,threading
from pathlib import Path
import httpx,pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from docx import Document
from docx.enum.section import WD_SECTION
from lxml import etree as E
from test_workbench import client,project,section,save,file,manual_package
from app import db,security,documents,parsers,provider,tasks,exports,retrieval,synchronization as sync
from app import advanced_statistics,math_office,ooxml_preserve,model_install,sync_bundle,collaboration

def upload(c,p,name,data,role='research'):
 f,new=parsers.register_file(p,name,data,role);parsers.parse_file(f['id']);return f

def test_migration_three_and_nested_transactions_rollback_together(client):
 assert db.one('PRAGMA user_version')['user_version']>=3
 p=project(client);s=section(client,p)
 with pytest.raises(RuntimeError):
  with db.transaction():
   db.execute('UPDATE projects SET title=? WHERE id=?',('must-rollback',p));documents.save(security.require_section(s),documents.text_document('must rollback'),None);raise RuntimeError('rollback')
 assert security.require_project(p)['title']=='测试论文'
 assert security.require_section(s)['current_revision_id'] is None
 assert not db.rows('SELECT * FROM revisions')

@pytest.mark.parametrize('latex,element',[(r'\frac{a+b}{c}','f'),(r'x^{2}+y_{i}','sSup'),(r'\sqrt{x}','rad'),(r'\begin{pmatrix}a&b\\c&d\end{pmatrix}','m'),(r'\sum_{i=1}^{n} i','nary')])
def test_complex_formula_has_native_office_structure(latex,element):
 xml=math_office.convert(latex);root=E.fromstring(xml.encode());assert root.findall('.//{'+ooxml_preserve.M+'}'+element);assert '<script' not in xml

def complex_doc():
 d=Document();d.add_paragraph('学校封面');d.sections[0].header.paragraphs[0].text='封面页眉';d.add_section(WD_SECTION.NEW_PAGE);d.sections[1].header.is_linked_to_previous=False;d.sections[1].header.paragraphs[0].text='正文页眉';d.add_paragraph('可以修改的正文');d.add_paragraph()._p.append(E.fromstring(math_office.convert(r'\frac{1}{x}').encode()));buf=io.BytesIO();d.save(buf);return buf.getvalue()

def test_docx_patch_preserves_math_sections_headers_and_rejects_stale_text():
 raw=complex_doc();info=ooxml_preserve.inspect_docx(raw);run=next(r for r in info['runs'] if r['text']=='可以修改的正文');patch={**run,'expected_text':run['text'],'text':'经过确认的修改正文'};output,receipt=ooxml_preserve.patch_docx(raw,info['package_sha256'],[patch]);after=ooxml_preserve.inspect_docx(output)
 assert receipt['unmodified_parts_byte_identical'];assert after['sections']==info['sections'];assert after['formulas'][0]['sha256']==info['formulas'][0]['sha256']
 with zipfile.ZipFile(io.BytesIO(raw)) as a,zipfile.ZipFile(io.BytesIO(output)) as b:
  for name in a.namelist():
   if name!='word/document.xml':assert a.read(name)==b.read(name)
 with pytest.raises(ValueError):ooxml_preserve.patch_docx(raw,'0'*64,[patch])
 with pytest.raises(ValueError):ooxml_preserve.patch_docx(raw,info['package_sha256'],[{**patch,'expected_text':'stale'}])

def test_footnotes_can_be_edited_without_rewriting_body(tmp_path):
 path=tmp_path/'footnote.docx';path.write_bytes(complex_doc());ooxml_preserve.add_footnotes(path,{100000:'原脚注内容'});raw=path.read_bytes();info=ooxml_preserve.inspect_docx(raw);note=next(r for r in info['runs'] if r['part']=='word/footnotes.xml' and r['text']=='原脚注内容');out,r=ooxml_preserve.patch_docx(raw,info['package_sha256'],[{**note,'expected_text':note['text'],'text':'修订脚注内容'}])
 with zipfile.ZipFile(io.BytesIO(raw)) as a,zipfile.ZipFile(io.BytesIO(out)) as b:assert a.read('word/document.xml')==b.read('word/document.xml')
 assert r['changed_parts']==['word/footnotes.xml']

def test_template_anchor_preserves_cover_and_all_original_sections():
 from app.template_merge import graft
 d=Document(io.BytesIO(complex_doc()));d.add_paragraph('{{PAPER_BODY}}');buf=io.BytesIO();d.save(buf);template=buf.getvalue();body=Document();body.add_heading('研究正文',1);body.add_paragraph('正文材料');b=io.BytesIO();body.save(b);output,receipt=graft(template,b.getvalue());info=ooxml_preserve.inspect_docx(output)
 assert info['sections']==ooxml_preserve.inspect_docx(template)['sections'];assert receipt['cover_sections_retained']
 with zipfile.ZipFile(io.BytesIO(template)) as a,zipfile.ZipFile(io.BytesIO(output)) as z:
  for n in a.namelist():
   if n.startswith('word/header') or n.startswith('word/footer'):assert a.read(n)==z.read(n)
  text=''.join(E.fromstring(z.read('word/document.xml')).itertext());assert '学校封面' in text and '研究正文' in text and '{{PAPER_BODY}}' not in text
 with pytest.raises(ValueError):graft(complex_doc(),b.getvalue())

def test_formula_and_footnote_endpoints_keep_existing_revision_content(client):
 p=project(client);s=section(client,p);save(client,s,'已有人工正文')
 r=client.post(f'/api/v1/projects/{p}/sections/{s}/formula',json={'latex':r'\frac{a}{b}'})
 assert r.status_code==200,r.text
 r=client.post(f'/api/v1/projects/{p}/sections/{s}/footnote',json={'text':'脚注原始出处'})
 assert r.status_code==200,r.text
 current=documents.current(security.require_section(s));assert '已有人工正文' in current['text'];assert '脚注原始出处' in current['text']
 other=project(client,'other');assert client.post(f'/api/v1/projects/{other}/sections/{s}/formula',json={'latex':'x'}).status_code==422

def test_author_year_disambiguation_is_stable():
 refs={'b':{'metadata':db.dumps({'authors':'张三','year':2024,'title':'乙'})},'a':{'metadata':db.dumps({'authors':'张三','year':2024,'title':'甲'})}}
 labels=ooxml_preserve.author_year_labels(refs);assert len(set(labels.values()))==2;assert all(v.startswith('张三，2024') for v in labels.values());assert labels==ooxml_preserve.author_year_labels(dict(reversed(list(refs.items()))))

@pytest.mark.parametrize('method,columns',[('welch_t',['A','B']),('paired_t',['A','B']),('pearson',['A','B']),('spearman',['A','B']),('mann_whitney',['A','B']),('anova',['A','B','C']),('ols',['A','B','C']),('bootstrap_mean',['A']),('correlation_matrix',['A','B','C'])])
def test_real_statistical_procedures_record_data_hash_and_finite_results(client,method,columns):
 p=project(client);f=upload(client,p,'data.csv',b'A,B,C\n1,3,2\n3,5,1\n2,4,4\n5,6,3\n4,9,6\n7,8,5\n8,10,8\n9,9,7\n',role='data');r=advanced_statistics.analyze(p,{'file_id':f['id'],'method':method,'columns':columns,'seed':7,'iterations':200})
 assert r['source_sha256']==f['sha256'];assert r['human_interpretation']=='pending';json.dumps(r,allow_nan=False)
 if method=='welch_t':assert r['mean_difference']==pytest.approx(-1.875);assert r['confidence_interval'][0]<r['mean_difference']<r['confidence_interval'][1]
 if method=='correlation_matrix':assert all(x['p_adjusted_holm']>=x['p_value'] for x in r['pairs'])

def test_statistics_missing_policy_and_project_boundaries(client):
 p=project(client);f=upload(client,p,'data.csv',b'A,B\n1,3\n2,\n,5\n3,7\n4,9\n',role='data')
 with pytest.raises(ValueError):advanced_statistics.analyze(p,{'file_id':f['id'],'method':'welch_t','columns':['A','B']})
 r=advanced_statistics.analyze(p,{'file_id':f['id'],'method':'welch_t','columns':['A','B'],'missing':'groupwise'});assert r['n_by_group']=={'A':4,'B':4}
 with pytest.raises(ValueError):advanced_statistics.analyze(p,{'file_id':f['id'],'method':'paired_t','columns':['A','B'],'missing':'groupwise'})
 other=project(client,'other')
 with pytest.raises(ValueError):advanced_statistics.analyze(other,{'file_id':f['id'],'method':'welch_t','columns':['A','B']})

def real_model_or_skip():
 if not model_install.status(retrieval.DEFAULT_MODEL)['ready']:pytest.skip('真实本地ONNX模型未安装')

def test_actual_neural_index_is_resumable_and_project_scoped(client):
 real_model_or_skip();p=project(client);file(client,p,'参考文献的每个论断必须能定位到原始出处。');file(client,p,'系统使用角色权限控制。');other=project(client,'other');file(client,other,'私有项目的参考文献数据。');r=retrieval.build_index(p);assert r['indexed']==2;assert retrieval.build_index(p)['indexed']==0
 out=retrieval.hybrid(p,'参考文献如何定位原文？','all',force=True);assert out['mode']=='neural_keyword_rrf';assert len(out['items'])==2;assert all(s['file_id'] in {f['id'] for f in db.rows('SELECT id FROM files WHERE project_id=?',(p,))} for s in out['items']);assert db.one('SELECT dimension FROM neural_vectors')['dimension']==512

def test_neural_index_excludes_changed_or_forbidden_sources(client):
 real_model_or_skip();p=project(client);f=file(client,p,'参考文献原文出处核查。');retri=retrieval.build_index(p);db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
 try:out=retrieval.hybrid(p,'原文出处','all',remote=True,force=True)
 except ValueError:out={'items':[]}
 assert out['items']==[]
 db.execute("UPDATE files SET egress='allowed' WHERE id=?",(f['id'],));db.execute("UPDATE sources SET text='研究内容已经改变' WHERE file_id=?",(f['id'],))
 with pytest.raises(ValueError):retrieval.hybrid(p,'原文出处','all',force=True)

def test_rank_metrics_do_not_double_count_and_report_missed_candidates():
 r=retrieval.metrics(['a','a'],{'a':1,'b':1},10);assert r['recall']==.5 and r['ndcg']<=1 and r['mrr']==1
 r=retrieval.metrics(['x'],{'a':3},10);assert r=={'recall':0,'mrr':0,'ndcg':0}

def changed(doc,index,text):
 d=copy.deepcopy(doc);d['content'][index]['content'][0]['text']=text;return d

def test_sync_disjoint_changes_merge_and_overlap_keeps_both(client):
 p=project(client);s=section(client,p);base=save(client,s,'甲段\n乙段').json();doc=db.loads(base['document']);one=sync.apply_operation(p,'alice',{'operation_id':'first','section_id':s,'base_revision_id':base['id'],'document':changed(doc,0,'甲段已修改')});assert one['status_code']==200
 payload={'operation_id':'second','section_id':s,'base_revision_id':base['id'],'document':changed(doc,1,'乙段已修改'),'auto_merge':True};two=sync.apply_operation(p,'bob',payload);assert two['status_code']==200 and two['auto_merged'];assert '甲段已修改' in two['revision']['text'] and '乙段已修改' in two['revision']['text']
 count=db.one('SELECT COUNT(*) n FROM revisions')['n'];assert sync.apply_operation(p,'bob',payload)==two;assert db.one('SELECT COUNT(*) n FROM revisions')['n']==count
 conflict=sync.apply_operation(p,'carol',{'operation_id':'third','section_id':s,'base_revision_id':base['id'],'document':changed(doc,0,'另一种甲段'),'auto_merge':True});assert conflict['status_code']==409;assert security.require_section(s)['current_revision_id']==two['revision']['id'];assert '另一种甲段' in db.one('SELECT remote_document FROM sync_conflicts')['remote_document']
 with pytest.raises(HTTPException):sync.apply_operation(p,'bob',{**payload,'document':doc})

def test_project_bundle_has_resources_but_no_credentials_and_rejects_cross_project_ids(client):
 p=project(client);f=file(client,p,'实际研究原件');s=section(client,p);save(client,s,'研究正文');db.set_setting('sharing:'+p,True);db.set_setting('provider',{'api_key':'fake-must-not-export','base_url':'https://relay.example'});packet=sync_bundle.make(p)
 assert packet['format']=='paper-project-sync-v2' and f['sha256'] in packet['blobs'];assert 'fake-must-not-export' not in db.dumps(packet)
 encoded=sync.encrypt(packet,'fake-sync-password-1234');assert '实际研究原件'.encode() not in encoded
 assert sync.decrypt(encoded,'fake-sync-password-1234')['project_id']==p
 with pytest.raises(ValueError):sync.decrypt(encoded,'wrong-password-1234')
 other=project(client,'other');sid=section(client,other);bad=copy.deepcopy(packet);bad['tables']['sections'][0]['id']=sid
 with pytest.raises(ValueError):sync_bundle.validate(bad)
 db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
 with pytest.raises(HTTPException):sync_bundle.make(p)

def login(c,name,pw='fake-collaboration-password-1234'):
 r=c.post('/collab/v1/login',json={'username':name,'password':pw});assert r.status_code==200,r.text;c.headers['X-CSRF-Token']=r.json()['csrf'];return r.json()

def test_collaboration_roles_csrf_and_no_legacy_key_endpoints(client,monkeypatch):
 monkeypatch.setenv('PAPER_COLLAB_INSECURE','0');owner=collaboration.create_user('owner','fake-collaboration-password-1234');viewer=collaboration.create_user('viewer','fake-collaboration-password-1234');reviewer=collaboration.create_user('reviewer','fake-collaboration-password-1234')
 with TestClient(collaboration.app,base_url='https://testserver') as a,TestClient(collaboration.app,base_url='https://testserver') as b,TestClient(collaboration.app,base_url='https://testserver') as r:
  login(a,'owner');p=a.post('/collab/v1/projects',json={'title':'协作测试'}).json()['id'];sid=a.post(f'/collab/v1/projects/{p}/sections',json={'title':'第一节'}).json()['id'];a.put(f'/collab/v1/projects/{p}/members',json={'username':'viewer','role':'viewer'});a.put(f'/collab/v1/projects/{p}/members',json={'username':'reviewer','role':'reviewer'});login(b,'viewer');login(r,'reviewer')
  assert b.get(f'/collab/v1/projects/{p}/snapshot').status_code==200
  body={'operation_id':'edit','section_id':sid,'base_revision_id':None,'document':documents.text_document('协作正文')}
  assert b.post(f'/collab/v1/projects/{p}/operations',json=body).status_code==403
  assert r.post(f'/collab/v1/projects/{p}/operations',json=body).status_code==403
  assert a.post(f'/collab/v1/projects/{p}/operations',json=body).status_code==200
  assert b.post(f'/collab/v1/projects/{p}/comments',json={'text':'评论'}).status_code==403
  assert r.post(f'/collab/v1/projects/{p}/comments',json={'text':'审阅意见'}).status_code==200
  assert b.get('/api/v1/settings').status_code==404
  assert b.get(f'/collab/v1/projects/{project(client,"other")}/snapshot').status_code==404
  headers=dict(a.headers);a.headers.pop('X-CSRF-Token');assert a.post('/collab/v1/projects',json={'title':'bad'}).status_code==403;a.headers.update(headers)
  assert a.put(f'/collab/v1/projects/{p}/members',json={'username':'owner','role':'viewer'}).status_code==409

@pytest.mark.parametrize('protocol',['openai','responses'])
def test_real_sse_delta_adapters_do_not_simulate_tokenization(client,monkeypatch,protocol):
 from app import provider_stream
 cfg=provider.config();cfg.update(protocol=protocol,model='fake-stream');db.set_setting('provider',cfg);monkeypatch.setattr(provider,'get_key',lambda:'fake-stream-key')
 if protocol=='openai':frames=[{'choices':[{'delta':{'content':'甲'}}]},{'choices':[{'delta':{'content':'乙'},'finish_reason':'stop'}]},{'usage':{'prompt_tokens':2,'completion_tokens':2}}]
 elif protocol=='responses':frames=[{'type':'response.output_text.delta','delta':'甲'},{'type':'response.output_text.delta','delta':'乙'},{'type':'response.completed','response':{'usage':{'input_tokens':2,'output_tokens':2}}}]
 wire=''.join('data: '+json.dumps(f,ensure_ascii=False)+'\n\n' for f in frames)
 monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire.encode()))));pieces=[];result=provider_stream.generate('rules','request',on_delta=pieces.append)
 assert pieces==['甲','乙'] and result['text']=='甲乙' and result['usage']['output_tokens']==2

def test_stream_disconnect_persists_chunks_and_recovery_never_calls_model_again(client,monkeypatch):
 p=project(client);cfg=provider.config();cfg.update(model='fake-stream',streaming=True);db.set_setting('provider',cfg);monkeypatch.setattr(provider,'get_key',lambda:'fake-stream-key');sent=[]
 class Broken(httpx.SyncByteStream):
  def __iter__(self):
   yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
   raise httpx.ReadError('connection lost')
 def handle(request):sent.append(1);return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
 monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)));t=tasks.enqueue(p,'provider_probe',{});tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='outcome_unknown';assert db.one('SELECT text FROM stream_chunks')['text']=='partial';assert db.one('SELECT reserved,cost FROM generation_runs')['cost'] is None
 r=client.post('/api/v1/tasks/'+t['id']+'/recover-stream',json={});assert r.status_code==200,r.text;assert len(sent)==1;f=db.one('SELECT * FROM files WHERE id=?',(r.json()['file_id'],));assert f['egress']=='local_only';assert security.safe_object(f['sha256']).read_text()=='partial';assert not db.rows('SELECT * FROM revisions')

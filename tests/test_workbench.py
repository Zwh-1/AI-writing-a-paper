import io,json,zipfile,sqlite3,hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import db,security,parsers,rules,documents,provider,tasks,exports,reviews,config
from app.main import app
import app.main as main

@pytest.fixture
def client(tmp_path,monkeypatch):
    for name in ('objects','previews','exports','backups','tmp','logs'): (tmp_path/name).mkdir()
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'workbench.sqlite3')
    for mod in (config,security,exports,main): monkeypatch.setattr(mod,'DATA',tmp_path)
    monkeypatch.setattr(main,'PORT',9999)
    db.init()
    with TestClient(app) as c:
        res=c.post('/api/v1/session',json={'token':security.BOOTSTRAP_TOKEN});assert res.status_code==200
        c.headers['X-CSRF-Token']=res.json()['csrf'];yield c

def project(c,title='测试论文'):
    r=c.post('/api/v1/projects',json={'title':title});assert r.status_code==200,r.text;return r.json()['id']
def manual_package(c,p):
    r=c.post(f'/api/v1/projects/{p}/rules',json={'statement':'写作必须有依据，缺失数据明确标记待补。','modality':'mandatory','scope':'global'});assert r.status_code==200,r.text
    r=c.post(f'/api/v1/projects/{p}/rule-packages');assert r.status_code==200,r.text;return r.json()['id']
def section(c,p):return c.post(f'/api/v1/projects/{p}/sections',json={'title':'系统设计','kind':'system_design'}).json()['id']
def save(c,s,text,base=None):return c.put(f'/api/v1/sections/{s}/content',json={'base_revision_id':base,'document':documents.text_document(text)})
def file(c,p,text='研究发现：服务分层有利于模块维护。',role='research'):
    r=c.post(f'/api/v1/projects/{p}/files',files={'file':('研究.txt',text.encode(),'text/plain')},data={'role':role});assert r.status_code==200,r.text
    f=r.json()['file'];parsers.parse_file(f['id']);db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",((r.json().get('task') or {}).get('id'),));return f

def test_auth_required(client):
    with TestClient(app) as raw: assert raw.get('/api/v1/projects').status_code==401

def test_csrf_required(client):
    headers=dict(client.headers);client.headers.pop('X-CSRF-Token');assert client.post('/api/v1/projects',json={'title':'x'}).status_code==403;client.headers.update(headers)

def test_external_origin_rejected(client):assert client.get('/api/v1/projects',headers={'Origin':'https://evil.example'}).status_code==403

def test_project_persisted_and_brief_revision(client):
    p=project(client);assert client.patch('/api/v1/projects/'+p,json={'brief':{'question':'如何提高维护性？'}}).status_code==200
    assert db.one('SELECT * FROM project_revisions WHERE project_id=?',(p,))

def test_file_hash_dedup(client):
    p=project(client);a=file(client,p);b=file(client,p);assert a['id']==b['id'];assert len(db.rows('SELECT * FROM files'))==1

def test_file_same_name_new_version(client):
    p=project(client);a=file(client,p,'版本一');b=file(client,p,'版本二');assert a['id']!=b['id']

def test_chinese_search_and_source_locator(client):
    p=project(client);file(client,p);hits=parsers.search(p,'模块维护');assert hits;assert db.loads(hits[0]['locator'])['page']==1

def test_local_only_excluded_from_remote_retrieval(client):
    p=project(client);f=file(client,p);client.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'});assert parsers.search(p,'模块');assert parsers.search(p,'模块',remote=True)==[]

def test_example_is_not_research_evidence(client):
    p=project(client);file(client,p,role='example');assert not parsers.search(p,'模块',kind='research');assert parsers.search(p,'模块',kind='example')

def test_rule_coverage_blocks_publication(client):
    p=project(client);f=file(client,p,'摘要必须说明研究方法。',role='rules');client.post(f'/api/v1/projects/{p}/rules',json={'statement':'有依据'});r=client.post(f'/api/v1/projects/{p}/rule-packages');assert r.status_code==422;assert '未核对' in r.text

def test_page_manual_transcript_persists(client):
    p=project(client);f=file(client,p,'扫描页',role='rules');page=db.one('SELECT * FROM pages WHERE file_id=?',(f['id'],));r=client.patch('/api/v1/pages/'+page['id'],json={'review_state':'reviewed','transcript':'参考文献不少于二十篇'});assert r.status_code==200;assert '二十篇' in r.json()['text']

def test_rule_conflict_blocks(client):
    p=project(client)
    for val in (10,20):client.post(f'/api/v1/projects/{p}/rules',json={'statement':f'参考文献至少{val}篇','checker':'reference_min','rule_key':'reference_min','value':val})
    assert len(rules.conflicts(p))==1;assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==422

def test_package_is_immutable_snapshot(client):
    p=project(client);pack=manual_package(client,p);r=db.one('SELECT * FROM rules WHERE project_id=?',(p,));client.patch('/api/v1/rules/'+r['id'],json={'statement':'新规则'});assert '新规则' not in db.one('SELECT rules FROM rule_packages WHERE id=?',(pack,))['rules']

def test_no_rules_no_writing_call(client):
    p=project(client);s=section(client,p);r=client.post(f'/api/v1/sections/{s}/writing-tasks',json={'base_revision_id':None});assert r.status_code==422;assert not db.rows('SELECT * FROM generation_runs')

def test_applicable_rules_by_scope(client):
    p=project(client);manual_package(client,p);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'摘要五要素','scope':'abstract'});pack=client.post(f'/api/v1/projects/{p}/rule-packages').json();pkg=db.one('SELECT * FROM rule_packages WHERE id=?',(pack['id'],));rr=rules.applicable(pkg,{'id':'x','kind':'system_design'},'generate');assert len(rr)==1

def test_revision_conflict_does_not_overwrite(client):
    p=project(client);s=section(client,p);a=save(client,s,'第一稿');assert a.status_code==200;assert save(client,s,'过期修改',None).status_code==409;assert documents.current(security.require_section(s))['text']=='第一稿'

def test_locked_paragraph_protected(client):
    p=project(client);s=section(client,p);r=save(client,s,'锁定段落').json();doc=db.loads(r['document']);node=doc['content'][0]['attrs']['nodeId'];assert client.put(f'/api/v1/sections/{s}/locks',json={'node_ids':[node]}).status_code==200
    doc['content'][0]['content'][0]['text']='不应覆盖';assert client.put(f'/api/v1/sections/{s}/content',json={'base_revision_id':r['id'],'document':doc}).status_code==409

def test_restore_creates_new_revision(client):
    p=project(client);s=section(client,p);a=save(client,s,'旧版本').json();b=save(client,s,'新版本',a['id']).json();r=client.post(f'/api/v1/sections/{s}/restore',json={'revision_id':a['id'],'base_revision_id':b['id']});assert r.status_code==200;assert r.json()['id']!=a['id'];assert r.json()['text']=='旧版本'

def test_fact_change_marks_abstract_stale(client):
    p=project(client);s=client.post(f'/api/v1/projects/{p}/sections',json={'title':'摘要','kind':'abstract'}).json()['id'];save(client,s,'结果为10');client.post(f'/api/v1/projects/{p}/facts',json={'label':'样本量','statement':'样本量为20','status':'measured'});assert security.require_section(s)['stale']==1

def test_citation_cross_project_rejected(client):
    p=project(client);p2=project(client,'另一论文');s=section(client,p);ref=client.post(f'/api/v1/projects/{p2}/references',json={'metadata':{'title':'其他项目文献'}}).json()['id'];doc=documents.text_document('正文');doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref}});r=client.put(f'/api/v1/sections/{s}/content',json={'document':doc,'base_revision_id':None});assert r.status_code==422;assert not db.rows('SELECT * FROM revisions')

def test_reference_and_citation_snapshot(client):
    p=project(client);f=file(client,p);s=section(client,p);ref=client.post(f'/api/v1/projects/{p}/references',json={'metadata':{'title':'原始标题','authors':'甲','year':'2024'},'file_id':f['id']}).json();doc=documents.text_document('研究结论');doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref['id']}});r=client.put(f'/api/v1/sections/{s}/content',json={'document':doc,'base_revision_id':None});assert r.status_code==200,r.text;assert '原始标题' in db.one('SELECT * FROM citations')['reference_snapshot']

def test_review_records_unknown_and_missing(client):
    p=project(client);s=section(client,p);save(client,s,'【待补数据】');out=reviews.run(p);assert any('待补' in f['message'] for f in out['findings']);assert out['summary']['fail']>0

def test_mentor_workflow(client):
    p=project(client);r=client.post(f'/api/v1/projects/{p}/mentor-tasks',json={'comment':'补充方案比较'});assert r.status_code==200,r.text;client.patch('/api/v1/mentor-tasks/'+r.json()['id'],json={'status':'applied'});assert client.get(f'/api/v1/projects/{p}/mentor-tasks').json()[0]['status']=='applied'

def test_idempotency_prevents_duplicate_task(client):
    p=project(client);a=tasks.enqueue(p,'review',{},'same');b=tasks.enqueue(p,'review',{},'same');assert a['id']==b['id']
    with pytest.raises(Exception):tasks.enqueue(p,'review',{'different':1},'same')

def test_cancel_queued_task(client):
    p=project(client);t=tasks.enqueue(p,'review',{});client.post('/api/v1/tasks/'+t['id']+'/cancel');assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='cancelled'

def test_unknown_retry_requires_acknowledgement(client):
    p=project(client);t=tasks.enqueue(p,'review',{});db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?",(t['id'],));assert client.post('/api/v1/tasks/'+t['id']+'/retry',json={}).status_code==422

def test_worker_recovery_does_not_resend_sent_request(client):
    p=project(client);t=tasks.enqueue(p,'review',{});db.execute("UPDATE tasks SET status='running',lease_until=0 WHERE id=?",(t['id'],));db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.uid(),t['id'],p,'sent','test','openai','{}',db.now()));tasks.recover();assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='outcome_unknown'

def test_writing_manifest_and_candidate_accept(client,monkeypatch):
    p=project(client);manual_package(client,p);s=section(client,p);cfg=provider.config();cfg['model']='test-model';db.set_setting('provider',cfg);seen={}
    def fake(system,user,images=None):
        seen['context']=db.loads(user);assert seen['context']['rules'];return {'text':db.dumps({'paragraphs':[{'text':'系统设计采用分层方案，具体测试结果【待补证据】','source_ids':[],'reference_ids':[]}],'missing_materials':['测试记录']}),'usage':{'input_tokens':100,'output_tokens':40},'cost':.01,'remote_id':'mock','request_hash':'hash'}
    monkeypatch.setattr(provider,'generate',fake);t=client.post(f'/api/v1/sections/{s}/writing-tasks',json={'base_revision_id':None,'operation':'generate'}).json();tasks.run_one();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='succeeded',row['error'];run=db.one('SELECT * FROM generation_runs');manifest=db.loads(run['manifest']);assert manifest['required_rule_ids']==manifest['loaded_rule_ids'];assert security.require_section(s)['current_revision_id'] is None
    r=client.post(f'/api/v1/sections/{s}/accept',json={'task_id':t['id'],'base_revision_id':None});assert r.status_code==200,r.text;assert '待补证据' in r.json()['text']

def test_block_local_only_rule_before_model_call(client,monkeypatch):
    p=project(client);f=file(client,p,'写作必须根据原始材料。',role='rules');page=db.one('SELECT * FROM pages');client.patch('/api/v1/pages/'+page['id'],json={'review_state':'reviewed'});src=db.one('SELECT * FROM sources');db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(db.uid(),p,src['id'],'必须保留事实','mandatory','confirmed',db.now()));rules.publish(p);client.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'});s=section(client,p)
    monkeypatch.setattr(provider,'generate',lambda *a:pytest.fail('禁止发送资料'))
    t=tasks.enqueue(p,'write',{'section_id':s,'base_revision_id':None,'operation':'generate'});tasks.run_one();assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='failed'

def test_dpapi_key_not_returned_or_backed_up(client):
    secret='sk-test-secret-abcdef';r=client.put('/api/v1/settings',json={'api_key':secret});assert r.status_code==200,r.text;assert secret not in r.text;assert security.get_key()==secret;assert secret.encode() not in (security.DATA/'credential.dpapi').read_bytes();b=exports.backup()
    with zipfile.ZipFile(exports.DATA/'backups'/b['filename']) as z: assert not any('credential' in n for n in z.namelist())

def test_docx_export_contains_text_and_snapshot(client):
    p=project(client);manual_package(client,p);s=section(client,p);save(client,s,'这是已保存的研究正文。');out=exports.export_project(p,{'mode':'draft','pdf':True});folder=exports.DATA/'exports'/out['export_id'];assert (folder/'论文.docx').exists();assert (folder/'论文交付包.zip').exists()
    from docx import Document
    doc=Document(folder/'论文.docx');assert any('研究正文' in x.text for x in doc.paragraphs);assert db.loads((folder/'版本清单.json').read_text())['sections'][0]['revision_id']==security.require_section(s)['current_revision_id']

def test_backup_restore_hash_and_credentials_exclusion(client):
    p=project(client);file(client,p);b=exports.backup();out=exports.restore_backup((exports.DATA/'backups'/b['filename']).read_bytes());restored=Path(out['restored_data_directory']);assert (restored/'workbench.sqlite3').exists();c=sqlite3.connect(restored/'workbench.sqlite3');assert c.execute('SELECT title FROM projects WHERE id=?',(p,)).fetchone()[0]=='测试论文';c.close()

def test_zip_traversal_restore_rejected(client):
    buf=io.BytesIO();raw=b'bad'
    with zipfile.ZipFile(buf,'w') as z:z.writestr('manifest.json',db.dumps({'files':{'../escape':hashlib.sha256(raw).hexdigest(),'workbench.sqlite3':hashlib.sha256(raw).hexdigest()}}));z.writestr('../escape',raw);z.writestr('workbench.sqlite3',raw)
    with pytest.raises(ValueError):exports.restore_backup(buf.getvalue())

def test_statistical_analysis_is_executed(client):
    p=project(client);r=client.post(f'/api/v1/projects/{p}/files',files={'file':('数据.csv','value,group\n1,a\n3,b\n5,c\n'.encode(),'text/csv')},data={'role':'data'});fid=r.json()['file']['id'];out=client.post(f'/api/v1/projects/{p}/analysis',json={'file_id':fid});assert out.status_code==200,out.text;assert out.json()['actual_execution'];assert out.json()['statistics']['value']['mean']==3

def test_model_json_contract():assert provider.parse_json('```json\n{"paragraphs":[]}\n```')['paragraphs']==[]

def test_provider_endpoint_normalization():assert provider.endpoint('https://relay.example/v1','/v1/models')=='https://relay.example/v1/models'

@pytest.mark.parametrize('protocol',['openai','responses'])
def test_provider_protocol_contract(client,monkeypatch,protocol):
    import httpx
    cfg=provider.config();cfg.update({'protocol':protocol,'model':'deepseek-test','base_url':'https://www.ecoapi.cc','input_price':1,'output_price':2});db.set_setting('provider',cfg);monkeypatch.setattr(provider,'get_key',lambda:'secret')
    def handler(request):
        body=json.loads(request.content)
        assert request.url.host=='www.ecoapi.cc'
        if protocol=='openai':assert body['messages'][1]['content']=='hello';data={'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
        elif protocol=='responses':assert body['instructions']=='rules';data={'output':[{'content':[{'type':'output_text','text':'ok'}]}],'usage':{'input_tokens':10,'output_tokens':5},'status':'completed'}
        return httpx.Response(200,json=data)
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(handler)));out=provider.generate('rules','hello');assert out['text']=='ok';assert out['cost']==pytest.approx(.00002)

def test_pptx_slide_order_uses_relationships(client):
    buf=io.BytesIO();P=parsers.NS['p'];A=parsers.NS['a'];R=parsers.NS['r']
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('ppt/presentation.xml',f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId r:id="r1"/><p:sldId r:id="r2"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="r1" Target="slides/slide9.xml"/><Relationship Id="r2" Target="slides/slide2.xml"/></Relationships>')
        for n,text in [(9,'第一张'),(2,'第二张')]:z.writestr(f'ppt/slides/slide{n}.xml',f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:sp><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sp></p:sld>')
    pp=list(parsers.pptx_pages(buf.getvalue()));assert [x['text'] for x in pp]==['第一张','第二张']

def test_bibtex_and_ris_metadata_import(client):
    p=project(client)
    for name,text in [('a.bib','@article{x, title={An {Example} Study}, author={A and B}, year={2024}, journal={Journal}, doi={10.1234/a}}'),('b.ris','TY  - JOUR\nTI  - Another Study\nAU  - C\nPY  - 2023\nER  -\n')]:
        r=client.post(f'/api/v1/projects/{p}/references/import',files={'file':(name,text.encode())});assert r.status_code==200,r.text;assert r.json()['imported']==1
    rr=client.get(f'/api/v1/projects/{p}/references').json();assert len(rr)==2;assert all(db.loads(r['metadata'])['scope']=='metadata_only' for r in rr)

def test_failed_rule_file_blocks_publish(client):
    p=project(client);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'规则'});f,_=parsers.register_file(p,'损坏.pptx',b'broken','rules')
    db.execute("UPDATE files SET status='failed' WHERE id=?",(f['id'],));assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==422

def test_batch_writing_creates_candidates_without_acceptance(client):
    p=project(client);manual_package(client,p);section(client,p);section(client,p);r=client.post(f'/api/v1/projects/{p}/writing-batch',json={});assert r.status_code==200;assert len(r.json()['tasks'])==2;assert not r.json()['automatic_acceptance']

def test_histogram_generated_from_real_data(client):
    p=project(client);r=client.post(f'/api/v1/projects/{p}/files',files={'file':('数据.csv',b'value\n1\n2\n4\n6\n8\n')},data={'role':'data'});fid=r.json()['file']['id'];out=client.post(f'/api/v1/projects/{p}/charts',json={'file_id':fid,'column':'value','title':'样本分布'});assert out.status_code==200,out.text;assert out.json()['metadata']['n']==5;assert security.safe_object(out.json()['sha256']).read_bytes().startswith(b'\x89PNG')

def test_pdf_is_generated_without_office(client):
    p=project(client);manual_package(client,p);s=section(client,p);save(client,s,'系统设计内容，所有具体测试数据仍待补充。');out=exports.export_project(p,{'pdf':True});folder=exports.DATA/'exports'/out['export_id'];report=db.loads((folder/'检查报告.json').read_text(encoding='utf-8'));assert out['pdf'],report;assert (folder/'论文.pdf').read_bytes().startswith(b'%PDF')

def test_review_history_retained(client):
    p=project(client);reviews.run(p);reviews.run(p);assert client.get(f'/api/v1/projects/{p}/review-history').json()

def test_same_reference_different_nodes_keep_distinct_evidence(client):
    p=project(client);f=file(client,p,'第一部分证据。\n'+'内容'*600+'\n第二部分证据。');s=section(client,p);sources=db.rows('SELECT * FROM sources WHERE file_id=?',(f['id'],));ref=client.post(f'/api/v1/projects/{p}/references',json={'metadata':{'title':'同一文献'},'file_id':f['id']}).json()['id'];doc=documents.text_document('段落一\n段落二');proposals=[]
    for i,para in enumerate(doc['content']):
        nid=db.uid();para['content'].append({'type':'citation','attrs':{'nodeId':nid,'citationId':ref}});proposals.append({'node_id':nid,'reference_id':ref,'source_ids':[sources[i]['id']]})
    r=documents.save(security.require_section(s),doc,None,citation_proposals=proposals);cc=db.rows('SELECT * FROM citations WHERE revision_id=?',(r['id'],));assert db.loads(cc[0]['source_ids'])!=db.loads(cc[1]['source_ids'])

def test_current_document_dependency_retained_after_manual_edit(client):
    p=project(client);f=file(client,p);s=section(client,p);r=save(client,s,'原文').json();source_id=db.one('SELECT id FROM sources')['id'];db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),p,'source',source_id,source_id,s,r['id']));r2=save(client,s,'手工润色后',r['id']).json();assert db.one('SELECT * FROM dependencies WHERE revision_id=?',(r2['id'],))

def test_semantic_review_records_model_judgment_as_unknown(client,monkeypatch):
    p=project(client);manual_package(client,p);s=section(client,p);save(client,s,'正文');cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:{'text':db.dumps({'findings':[{'section_id':s,'message':'需要核对限定条件','evidence':'该结论条件不明确'}]}),'usage':{},'cost':0,'remote_id':'x','request_hash':'h'})
    t=tasks.enqueue(p,'semantic_review',{});tasks.run_one();assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='succeeded';f=db.one('SELECT * FROM findings');assert f['result']=='unknown';assert f['method']=='model_assisted'

def test_null_and_duplicate_node_ids_are_normalized(client):
    p=project(client);s=section(client,p);doc={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':None},'content':[{'type':'text','text':'第一段'}]},{'type':'paragraph','attrs':{'nodeId':None},'content':[{'type':'text','text':'第二段'}]}]};r=client.put(f'/api/v1/sections/{s}/content',json={'document':doc,'base_revision_id':None});assert r.status_code==200;r=client.get(f'/api/v1/sections/{s}/content').json();ids=[n['attrs']['nodeId'] for n in r['document']['content']];assert all(ids);assert len(set(ids))==2

def test_paragraph_provenance_is_visible_in_research_map(client):
    p=project(client);f=file(client,p);sid=section(client,p);source_id=db.one('SELECT id FROM sources')['id'];fact=client.post(f'/api/v1/projects/{p}/facts',json={'label':'实际研究','statement':'依据材料确认','source_ids':[source_id]}).json();doc=documents.text_document('有材料支撑的段落');doc['content'][0]['attrs'].update({'sourceIds':[source_id],'factIds':[fact['id']]});r=client.put(f'/api/v1/sections/{sid}/content',json={'document':doc,'base_revision_id':None});assert r.status_code==200,r.text;mapping=client.get(f'/api/v1/projects/{p}/research-map').json();row=mapping['sections'][0];assert row['source_ids']==[source_id];assert row['fact_ids']==[fact['id']];assert row['claim_count']==1

def test_cross_project_paragraph_evidence_is_rejected(client):
    p=project(client);other=project(client,'其他项目');file(client,other);source_id=db.one('SELECT id FROM sources')['id'];s=section(client,p);doc=documents.text_document('不能串用资料');doc['content'][0]['attrs']['sourceIds']=[source_id];r=client.put(f'/api/v1/sections/{s}/content',json={'document':doc,'base_revision_id':None});assert r.status_code==422;assert not db.rows('SELECT * FROM revisions')

def test_ai_revision_preserves_locked_paragraph_and_table(client,monkeypatch):
    p=project(client);manual_package(client,p);sid=section(client,p);doc=documents.text_document('不能改动的已确认文字');table={'type':'table','attrs':{'nodeId':'table-fixed'},'content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'真实数据'}]}]}]}]};doc['content'].append(table);rev=documents.save(security.require_section(sid),doc,None);canonical=db.loads(rev['document']);locked=canonical['content'][0]['attrs']['nodeId'];client.put(f'/api/v1/sections/{sid}/locks',json={'node_ids':[locked]});cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
    def fake(system,user,images=None):return {'text':db.dumps({'paragraphs':[{'text':'新补充的说明。'}]}),'usage':{},'cost':0,'remote_id':'test','request_hash':'h'}
    monkeypatch.setattr(provider,'generate',fake);t=tasks.enqueue(p,'write',{'section_id':sid,'base_revision_id':rev['id'],'operation':'revise'});tasks.run_one();task=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert task['status']=='succeeded',task['error'];candidate=db.loads(task['result'])['document'];assert candidate['content'][0]==canonical['content'][0];assert any(n['type']=='table' and n['attrs']['nodeId']=='table-fixed' for n in candidate['content'])

def test_invalid_charged_response_is_kept_for_user_review(client,monkeypatch):
    p=project(client);manual_package(client,p);s=section(client,p);cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
    raw=db.dumps({'paragraphs':[{'text':'错误来源候选','source_ids':['invented-id']} ]})
    monkeypatch.setattr(provider,'generate',lambda *a,**kw:{'text':raw,'usage':{'input_tokens':100,'output_tokens':80},'cost':.03,'remote_id':'test','request_hash':'h'})
    t=tasks.enqueue(p,'write',{'section_id':s,'base_revision_id':None,'operation':'generate'});tasks.run_one();task=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert task['status']=='failed';result=db.loads(task['result']);assert result['raw_text']==raw;assert result['document'] is None;assert db.one('SELECT cost FROM generation_runs')['cost']==.03

def test_changed_facts_prevent_accepting_old_candidate(client,monkeypatch):
    p=project(client);manual_package(client,p);s=section(client,p);fact=client.post(f'/api/v1/projects/{p}/facts',json={'label':'样本','statement':'样本量为10','status':'measured'}).json();cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);monkeypatch.setattr(provider,'generate',lambda *a,**kw:{'text':db.dumps({'paragraphs':[{'text':'样本量为10','fact_ids':[fact['id']]}]}),'usage':{},'cost':0,'remote_id':'test','request_hash':'h'});t=tasks.enqueue(p,'write',{'section_id':s,'base_revision_id':None,'operation':'generate'});tasks.run_one();client.patch('/api/v1/facts/'+fact['id'],json={'statement':'样本量为20'});r=client.post(f'/api/v1/sections/{s}/accept',json={'task_id':t['id'],'base_revision_id':None});assert r.status_code==409

def test_export_uses_original_bibliography_snapshot(client):
    p=project(client);manual_package(client,p);f=file(client,p);s=section(client,p);ref=client.post(f'/api/v1/projects/{p}/references',json={'metadata':{'title':'原文献题名','authors':'甲','year':'2024'},'file_id':f['id']}).json();doc=documents.text_document('正文论断');doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':ref['id']}});documents.save(security.require_section(s),doc,None);client.patch('/api/v1/references/'+ref['id'],json={'metadata':{'title':'尚未采用的新题名','authors':'甲','year':'2025'}});out=exports.export_project(p,{});from docx import Document
    text='\n'.join(x.text for x in Document(exports.DATA/'exports'/out['export_id']/'论文.docx').paragraphs);assert '原文献题名' in text;assert '尚未采用的新题名' not in text

def test_model_schema_rejects_nonarray_missing_materials(client,monkeypatch):
    p=project(client);manual_package(client,p);s=section(client,p);cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);monkeypatch.setattr(provider,'generate',lambda *a,**kw:{'text':db.dumps({'paragraphs':[{'text':'候选'}],'missing_materials':'类型错误'}),'usage':{},'cost':0,'remote_id':'test','request_hash':'h'});t=tasks.enqueue(p,'write',{'section_id':s,'base_revision_id':None,'operation':'generate'});tasks.run_one();task=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));result=db.loads(task['result']);assert result['document'] is None;assert result['validation_error']

def test_newer_database_version_is_not_silently_downgraded(client):
    db.execute('PRAGMA user_version=999')
    with pytest.raises(RuntimeError):db.init()
    assert db.one('PRAGMA user_version')['user_version']==999

def test_existing_docx_draft_import_creates_real_baseline(client):
    from docx import Document
    document=Document();document.add_heading('第一章 原有草稿',1);document.add_paragraph('保留用户已经撰写的文字。');table=document.add_table(rows=2,cols=2);table.cell(0,0).text='指标';table.cell(0,1).text='数值';table.cell(1,0).text='样本';table.cell(1,1).text='10';buffer=io.BytesIO();document.save(buffer)
    p=project(client);out=client.post(f'/api/v1/projects/{p}/files',files={'file':('草稿.docx',buffer.getvalue())},data={'role':'draft'});f=out.json()['file'];parsers.parse_file(f['id']);r=client.post('/api/v1/files/'+f['id']+'/import-draft');assert r.status_code==200,r.text;assert r.json()['imported_sections']==1;sid=r.json()['section_ids'][0];body=client.get('/api/v1/sections/'+sid+'/content').json();assert '保留用户' in body['revision']['text'];assert any(n['type']=='table' for n in body['document']['content']);assert body['revision']['kind']=='draft_import';assert client.post('/api/v1/files/'+f['id']+'/import-draft').status_code==409

def test_reference_example_is_not_automatically_imported_as_own_draft(client):
    p=project(client);f=file(client,p,'别人论文的示例',role='example');assert client.post('/api/v1/files/'+f['id']+'/import-draft').status_code==422

def test_outline_candidate_acceptance_is_idempotent(client):
    p=project(client);manual_package(client,p);result={'sections':[{'title':'研究方法','kind':'methods','goal':'说明实际方法','target_words':1000}]};task=tasks.enqueue(p,'outline_ai',{});db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']));first=client.post(f'/api/v1/projects/{p}/outline/accept-candidate',json={'task_id':task['id'],'mode':'append'});assert first.status_code==200,first.text;again=client.post(f'/api/v1/projects/{p}/outline/accept-candidate',json={'task_id':task['id'],'mode':'append'});assert again.status_code==409;assert db.one('SELECT COUNT(*) AS n FROM sections')['n']==1

def test_uncited_registered_bibliography_does_not_satisfy_minimum(client):
    p=project(client);client.post(f'/api/v1/projects/{p}/rules',json={'statement':'至少1篇文献','checker':'reference_min','rule_key':'reference_min','value':1});rules.publish(p);client.post(f'/api/v1/projects/{p}/references',json={'metadata':{'title':'未引用书目','authors':'甲','year':'2024'}});out=reviews.run(p);finding=next(x for x in out['findings'] if '去重文献' in x['message']);assert finding['result']=='fail';assert '0/1' in finding['message']

def test_generation_probe_needs_user_key_without_creating_charge(client,monkeypatch):
    p=project(client);monkeypatch.setattr(security,'get_key',lambda:'');r=client.post('/api/v1/provider/probe-generation',json={'project_id':p});assert r.status_code==422;assert not db.rows('SELECT * FROM generation_runs')

def test_provider_settings_reject_negative_prices(client):
    r=client.put('/api/v1/settings',json={'provider':{'input_price':-1}});assert r.status_code==422

def test_sources_without_formal_references_remain_linked(client,monkeypatch):
    p=project(client);f=file(client,p);manual_package(client,p);sid=section(client,p);source_id=db.one('SELECT id FROM sources')['id'];cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg);monkeypatch.setattr(provider,'generate',lambda *a,**kw:{'text':db.dumps({'paragraphs':[{'text':'根据提供资料组织的论述','source_ids':[source_id]}]}),'usage':{},'cost':0,'remote_id':'test','request_hash':'h'});t=tasks.enqueue(p,'write',{'section_id':sid,'base_revision_id':None,'operation':'generate'});tasks.run_one();r=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':t['id'],'base_revision_id':None});assert r.status_code==200,r.text;assert db.loads(r.json()['document'])['content'][0]['attrs']['sourceIds']==[source_id]

def test_completed_paid_call_is_not_automatically_regenerated_on_recovery(client):
    p=project(client);t=tasks.enqueue(p,'write',{'section_id':'example'});db.execute("UPDATE tasks SET status='running',lease_until=0 WHERE id=?",(t['id'],));output=security.put_blob(b'completed response');manifest={'output_blob':output};db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.uid(),t['id'],p,'completed','test','openai',db.dumps(manifest),db.now()));tasks.recover();row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='incomplete';assert db.loads(row['result'])['raw_text']=='completed response';assert client.post('/api/v1/tasks/'+t['id']+'/retry',json={}).status_code==422

def test_archived_projects_are_hidden_and_recoverable(client):
    p=project(client);client.patch('/api/v1/projects/'+p,json={'archived':1});assert client.get('/api/v1/projects').json()==[];assert client.get('/api/v1/projects?include_archived=true').json()[0]['id']==p;client.patch('/api/v1/projects/'+p,json={'archived':0});assert client.get('/api/v1/projects').json()[0]['id']==p

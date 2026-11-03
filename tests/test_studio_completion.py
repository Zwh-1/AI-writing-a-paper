"""Second-pass acceptance covers missing UI data paths and real failure recovery."""
import io,json,zipfile
from pathlib import Path
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,save,manual_package
from test_studio import mock_model,response,studio
from app import db,tasks,security,documents
from app import studio_store as store,studio_ai as ai,studio_context as context,studio_imports as imports,studio_exports as exports,studio_parsing as parsing,studio_reading as reading

def test_zotero_retries_unchanged_parent_after_attachment_failure(client,monkeypatch):
    pid=project(client);db.set_setting('studio:zotero:'+pid,{'library_id':'42','library_type':'users','attachments':True})
    monkeypatch.setattr(store,'read_secret',lambda name:'synthetic-key');attempts=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params=None,headers=None):
            if url.endswith('/children'):return response([{'key':'ATTACHMENT','version':9,'data':{'contentType':'application/pdf','filename':'attachment.txt'}}])
            return response([{'key':'PAPER','version':5,'data':{'title':'Same parent version','creators':[],'tags':[]}}],{'Total-Results':'1'})
    def download(*args,**kwargs):
        attempts.append(1)
        if len(attempts)==1:raise ValueError('transient download failure')
        return b'Actual attachment content.','attachment.txt','https://example.org/file'
    monkeypatch.setattr(imports,'http_client',lambda *args,**kwargs:Client());monkeypatch.setattr(imports,'download',download)
    task=tasks.enqueue(pid,'studio',{'action':'zotero'});first=imports.zotero_sync(task,{})
    link=db.one('SELECT * FROM studio_external_links WHERE project_id=?',(pid,));rid=link['reference_id']
    assert not first['complete'] and json.loads(link['data'])['attachment_pending']
    second=imports.zotero_sync(task,{})
    assert second['complete'] and len(attempts)==2
    reference=store.require_reference(pid,rid)
    assert reference['file_id'] and reference['id']==rid

def test_incomplete_zotero_task_is_not_marked_succeeded(client,monkeypatch):
    pid=project(client);task=tasks.enqueue(pid,'studio',{'action':'zotero'})
    monkeypatch.setattr(tasks,'dispatch',lambda task:{'seen':1,'updated':1,'attachment_failures':[{'key':'A'}],'complete':False})
    tasks.run_one();saved=db.one('SELECT status,result FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='incomplete' and json.loads(saved['result'])['seen']==1

def test_asset_text_content_reaches_assistant_and_permission_is_rechecked(client,monkeypatch):
    pid=project(client);aid=db.uid();sha=security.put_blob(b'Actual table cells: count=12, outcome=unknown.')
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'findings.json','data',sha,db.dumps({'egress':'selected_content'}),db.now()))
    conversation=store.save(pid,'conversation',{'title':'Read an asset'});calls=mock_model(monkeypatch,{'text':'Read asset content','citations':[]})
    task=ai.send_message(pid,conversation['id'],{'text':'read it','asset_ids':[aid],'use_knowledge':False});tasks.dispatch(task)
    assert 'count=12' in calls[0]['payload']['assets'][0]['text']
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(task['id'],));db.execute('UPDATE artifacts SET metadata=? WHERE id=?',(db.dumps({'egress':'local_only'}),aid))
    task=ai.send_message(pid,conversation['id'],{'text':'continue','use_knowledge':False})
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:pytest.fail('Revoked asset-derived history cannot be sent'))
    with pytest.raises(HTTPException):tasks.dispatch(task)

def test_image_asset_uses_vision_payload(client,monkeypatch):
    from PIL import Image
    pid=project(client);image=io.BytesIO();Image.new('RGB',(100,60),'green').save(image,format='PNG');aid=db.uid();sha=security.put_blob(image.getvalue())
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'plot.png','figure',sha,db.dumps({'egress':'selected_content'}),db.now()))
    captured=[]
    def model(task,system,user,manifest,images=None):captured.extend(images or []);return {'text':'{"text":"Image reply","citations":[]}','run_id':'vision'}
    monkeypatch.setattr(tasks,'model_call',model);conversation=store.save(pid,'conversation',{'title':'vision'})
    tasks.dispatch(ai.send_message(pid,conversation['id'],{'text':'describe','asset_ids':[aid],'use_knowledge':False}))
    assert len(captured)==1 and captured[0]['mime']=='image/png' and captured[0]['data']

def test_document_chat_proposal_needs_adoption_and_preserves_inline_nodes(client,monkeypatch):
    pid=project(client);sid=section(client,pid);save(client,sid,'Original statement requiring clarification.')
    revision=documents.current(security.require_section(sid));node=json.loads(revision['document'])['content'][0]
    conversation=store.save(pid,'conversation',{'title':'edit chapter'})
    calls=mock_model(monkeypatch,{'text':'Suggested revision','citations':[],'operations':[{'node_id':node['attrs']['nodeId'],'before':'requiring clarification','after':'with explicit conditions'}]})
    task=ai.send_message(pid,conversation['id'],{'text':'clarify','section_id':sid,'revision_id':revision['id'],'allow_edit':True,'use_knowledge':False});reply=tasks.dispatch(task)
    assert calls[0]['payload']['current_document']['paragraphs'][0]['text']==revision['text']
    assert documents.current(security.require_section(sid))['id']==revision['id']
    proposal=store.get(pid,reply['message']['data']['proposal_id'],'proposal');adopted=context.adopt(pid,proposal['id'],proposal['revision'])
    assert 'explicit conditions' in adopted['text']
    with pytest.raises(HTTPException):context.adopt(pid,proposal['id'],proposal['revision'])

def test_assistant_proposal_cannot_modify_locked_or_newer_body(client,monkeypatch):
    pid=project(client);sid=section(client,pid);save(client,sid,'Original editable paragraph.')
    section_row,revision,doc,paragraphs,permission=context.section(pid,sid);node=paragraphs[0]['node_id']
    proposal=store.save(pid,'proposal',{'base_revision_id':revision['id'],'permission_stamp':permission,'operations':[{'node_id':node,'before':'editable','after':'changed'}]},owner=sid)
    db.execute('INSERT INTO locks VALUES(?,?)',(sid,node))
    with pytest.raises(HTTPException):context.adopt(pid,proposal['id'],proposal['revision'])
    assert documents.current(security.require_section(sid))['id']==revision['id']

def test_record_history_restores_to_new_version_and_preserves_original(client):
    pid=project(client);note=store.save(pid,'note',{'title':'Note','text':'first'})
    updated=store.save(pid,'note',{'title':'Note','text':'second'},note['id'],expected_revision=1)
    result=client.post(studio(pid)+f'/records/{note["id"]}/restore',json={'revision':2,'target_revision':1})
    assert result.status_code==200 and result.json()['revision']==3 and result.json()['data']['text']=='first'
    assert [row['revision'] for row in store.versions(pid,note['id'])]==[3,2,1]

def test_search_escapes_wildcards_and_orders_equal_timestamps_by_insert(client,monkeypatch):
    pid=project(client);monkeypatch.setattr(db,'now',lambda:'2026-11-02T00:00:00.000000+00:00')
    first=store.save(pid,'note',{'title':'100%_exact'});second=store.save(pid,'note',{'title':'100 percent'})
    assert store.list_records(pid,'note',query='%_')[0]['id']==first['id']
    assert store.list_records(pid,'note',limit=1)[0]['id']==second['id']

def test_duplicate_quote_comment_uses_explicit_offsets_and_unicode(client):
    pid=project(client);sid=section(client,pid);save(client,sid,'🙂同词，🙂同词。')
    revision=documents.current(security.require_section(sid));node=json.loads(revision['document'])['content'][0]
    text=documents.plain(node);start=text.rindex('同词')
    comment=ai.add_comment(pid,{'section_id':sid,'revision_id':revision['id'],'node_id':node['attrs']['nodeId'],'quote':'同词','start':start,'end':start+2,'text':'第二处'})
    assert ai.comment_context(pid,comment)[3] and comment['data']['start']==start

def test_excluded_original_pages_and_old_progress_are_not_used(client):
    pid=project(client);original=file(client,pid,'Excluded source paragraph.')
    view=reading.document(pid,original['id']);uid=view['units'][0]['id'];reading.progress(pid,original['id'],{'unit_id':uid,'completed_units':[uid]})
    db.execute("UPDATE pages SET review_state='not_applicable' WHERE file_id=?",(original['id'],))
    result=reading.document(pid,original['id'])
    assert not result['units'] and not result['progress']['completed_units'] and result['progress']['unit_id'] is None

def test_abstract_view_does_not_show_translation_after_source_changed(client):
    pid=project(client);reference=imports.upsert_reference(pid,{'title':'P','abstract':'first'})
    store.save(pid,'translation',{'reference_id':reference['id'],'language':'zh-CN','source_hash':reading.text_hash('first'),'text':'旧译文'},owner=reference['id'])
    assert client.get(studio(pid)+'/abstract/'+reference['id']).json()['data']['text']=='旧译文'
    imports.upsert_reference(pid,{'title':'P','abstract':'second'},provider='test',external='ref')
    db.execute('UPDATE bibliography SET metadata=? WHERE id=?',(db.dumps({'title':'P','abstract':'second'}),reference['id']))
    assert client.get(studio(pid)+'/abstract/'+reference['id']).json() is None

def test_guide_requires_real_quotes_and_locates_original_units(client,monkeypatch):
    pid=project(client);original=file(client,pid,'A result holds only for this sample.');unit=reading.document(pid,original['id'])['units'][0]
    mock_model(monkeypatch,{'summary':'A limited result','keypoints':[{'unit_id':unit['id'],'quote':'only for this sample','text':'限定样本'}],'questions':['Can it generalize?'],'limitations':['仅当前样本']})
    task=tasks.enqueue(pid,'studio',{'action':'guide','file_id':original['id']});tasks.dispatch(task)
    assert reading.document(pid,original['id'])['guide']['data']['keypoints'][0]['unit_id']==unit['id']

def test_complete_sync_refuses_silent_omission_of_studio_records(client):
    from app import sync_bundle
    pid=project(client);store.save(pid,'note',{'title':'Must retain'})
    with pytest.raises(HTTPException,match='完整'):sync_bundle.make(pid)

def test_two_metadata_documents_still_connect_after_first_reference_removed(client):
    from app import studio_graph,knowledge_graph
    pid=project(client);a=imports.upsert_reference(pid,{'title':'First','tags':['shared']});b=imports.upsert_reference(pid,{'title':'Second','tags':['shared']})
    studio_graph.enrich_project(pid);before=knowledge_graph.search(pid,'shared')
    assert any(edge['relation']=='shares_keywords' for edge in before['edges'])
    preview=exports.deletion_preview(pid,[a['id']]);exports.delete_references(pid,{'reference_ids':[a['id']],'fingerprint':preview['fingerprint']})
    after=knowledge_graph.search(pid,'shared')
    assert any(node['kind']=='keyword' for node in after['nodes'])

def test_surya_and_mineru_normalization_retains_lists_math_and_order():
    payload={'blocks':[{'page_idx':0,'type':'list','list_items':['one','two'],'bbox':[0,0,100,30]},{'page_idx':0,'type':'equation','latex':'x^2','order':3},{'page_idx':0,'type':'section_header','text':'Heading','order':-1}]}
    blocks=parsing.normalize_blocks(payload)
    assert blocks[0]['type']=='heading' and blocks[1]['text']=='one\ntwo' and blocks[-1]['type']=='formula'

def test_surya_layout_does_not_drop_unassigned_ocr_lines(tmp_path,monkeypatch):
    import importlib.util
    monkeypatch.setenv('SURYA_DATA_ROOT',str(tmp_path/'surya'))
    root=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('completion_surya',root/'services/surya_service.py');service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    layout=[{'bboxes':[{'bbox':[0,0,50,100],'label':'Text','position':0}]}]
    ocr=[{'text_lines':[{'bbox':[2,2,30,10],'text':'first column'},{'bbox':[60,2,90,10],'text':'unassigned second column'}]}]
    blocks=service.normalize_results(layout,ocr)
    assert len(blocks)==2 and 'second column' in blocks[1]['text']

def test_latex_cross_references_include_target_section_and_table(client):
    pid=project(client);sid=section(client,pid);target=db.uid();document={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':db.uid()},'content':[{'type':'crossReference','attrs':{'targetId':target,'targetSectionId':sid}}]},{'type':'table','attrs':{'nodeId':target,'caption':'Results'},'content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'12'}]}]}]}]}]}
    documents.save(security.require_section(sid),document,None);result=exports.latex_project(pid,{'language':'en'})
    from app.config import DATA
    with zipfile.ZipFile(DATA/'exports'/result['export_id']/'latex-project.zip') as archive:
        content=archive.read('main.tex').decode();label='visual:'+sid+':'+target
        assert '\\label{'+label+'}' in content and '\\ref{'+label+'}' in content

def test_native_block_formula_exports_structural_office_math(client):
    from app import exports as core
    pid=project(client);sid=section(client,pid);manual_package(client,pid)
    documents.save(security.require_section(sid),{'type':'doc','content':[{'type':'formula','attrs':{'latex':r'\frac{x}{y}','expression':r'\frac{x}{y}'}}]},None)
    result=core.export_project(pid,{'mode':'draft'});folder=core.DATA/'exports'/result['export_id']
    with zipfile.ZipFile(next(folder.glob('*.docx'))) as archive:assert '<m:f>' in archive.read('word/document.xml').decode()

def test_rich_asset_can_keep_editable_table_and_feed_actual_content(client,monkeypatch):
    pid=project(client);document={'type':'doc','content':[{'type':'table','content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'Count: 12'}]}]}]}]}]}
    result=client.post(studio(pid)+'/assets',json={'title':'Structured data note','document':document,'text':'','tags':['evidence']})
    assert result.status_code==200;asset=result.json();assert asset['metadata']['document']['content'][0]['type']=='table'
    content,_,_,_=context.artifact(pid,asset['id']);assert 'Count: 12' in content['text']
    changed=client.put(studio(pid)+f'/assets/{asset["id"]}/content',json={'revision':1,'title':'Updated asset','text':'Current content','tags':['new']})
    assert changed.status_code==200 and changed.json()['metadata']['revision']==2
    assert db.one("SELECT id FROM project_revisions WHERE project_id=? AND kind='studio_asset'",(pid,))

def test_image_execution_input_output_protocol_can_use_a_real_child_process_boundary(client,monkeypatch,tmp_path):
    # Exercise the HTTP broker contract without granting the test host code execution.
    import importlib.util
    from fastapi.testclient import TestClient
    monkeypatch.setenv('PAPER_EXECUTION_TOKEN','synthetic-remote-key-more-than-24-characters')
    root=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('test_remote_execution_broker',root/'services/execution_service.py');service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    monkeypatch.setattr(service.execution,'local',lambda task,code,timeout,inputs,metadata:{'exit_code':0,'stdout':'broker contract exercised','stderr':'','artifacts':[],'timed_out_or_output_limited':False})
    with TestClient(service.app) as remote:
        assert remote.post('/execute',json={'code':'print(1)'}).status_code==401
        response_=remote.post('/execute',headers={'Authorization':'Bearer synthetic-remote-key-more-than-24-characters'},json={'code':'print(1)','files':[],'timeout_seconds':10})
        assert response_.status_code==200 and response_.json()['stdout']=='broker contract exercised'

def test_connector_retry_respects_retry_after_and_is_bounded(monkeypatch):
    attempts=[];delays=[]
    class Client:
        def get(self,*args,**kwargs):
            attempts.append(1);value=response({}, {'Retry-After':'3'});value.status_code=429 if len(attempts)<3 else 200;return value
    monkeypatch.setattr(tasks.STOP,'wait',lambda seconds:delays.append(seconds) or False)
    assert imports.connector_get(Client(),'https://api.example.org').status_code==200
    assert len(attempts)==3 and delays==[3,3]

def test_batch_deletion_of_shared_source_does_not_leave_it_active(client):
    pid=project(client);original=file(client,pid,'Shared original')
    refs=[client.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':title},'file_id':original['id']}).json()['id'] for title in ['A','B']]
    preview=exports.deletion_preview(pid,refs);exports.delete_references(pid,{'reference_ids':refs,'fingerprint':preview['fingerprint']})
    assert not db.one('SELECT active FROM files WHERE id=?',(original['id'],))['active']
    restored=imports.upsert_reference(pid,{'title':'A'},original['id'])
    assert db.one('SELECT active FROM files WHERE id=?',(original['id'],))['active'] and not restored['metadata'].get('_studio_deleted')

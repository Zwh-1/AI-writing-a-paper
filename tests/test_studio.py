"""Research workflows use isolated fixture projects and simulated external transports."""
import base64
import io
import json
import zipfile
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,save,manual_package
from app import db,tasks,documents,security
from app import studio_store as store,studio_imports as imports,studio_reading as reading,studio_ai as ai,studio_exports as exports,studio_parsing as parsing

def studio(pid):return f'/api/v1/projects/{pid}/studio'

def response(value,headers=None):
    class Response:
        def raise_for_status(self):pass
        def json(self):return value
    result=Response();result.headers=headers or {};return result

def mock_model(monkeypatch,answer):
    calls=[]
    def call(task,system,user,manifest,images=None):
        calls.append({'system':system,'payload':json.loads(user),'manifest':manifest})
        value=answer(calls[-1]) if callable(answer) else answer
        return {'text':json.dumps(value,ensure_ascii=False),'run_id':'test-run','usage':{},'cost':0}
    monkeypatch.setattr(tasks,'model_call',call)
    return calls

def test_records_keep_history_and_reject_lost_updates_and_cross_project(client):
    pid=project(client);other=project(client,'other')
    row=client.post(studio(pid)+'/records',json={'kind':'note','data':{'title':'记录','text':'原文'}}).json()
    assert row['revision']==1
    updated=client.put(studio(pid)+'/records/'+row['id'],json={'revision':1,'data':{'title':'记录','text':'新文'}})
    assert updated.status_code==200 and updated.json()['revision']==2
    assert client.put(studio(pid)+'/records/'+row['id'],json={'revision':1,'data':{'text':'丢失更新'}}).status_code==409
    assert client.get(studio(other)+'/records/'+row['id']).status_code==404
    assert db.one('SELECT data FROM studio_versions WHERE record_id=?',(row['id'],))['data'].find('原文')>=0

def test_url_import_reuses_hash_and_preserves_reference_identity(client,monkeypatch):
    pid=project(client)
    monkeypatch.setattr(imports,'download',lambda *args,**kwargs:('真实的测试原文。'.encode(),'paper.txt','https://example.org/paper.txt'))
    task=tasks.enqueue(pid,'studio',{'action':'import_url','url':'https://example.org/paper.txt','metadata':{'doi':'10.1234/probe'}})
    first=tasks.dispatch(task);second=tasks.dispatch(task)
    assert first['file_id']==second['file_id'] and first['reference']['id']==second['reference']['id']
    assert db.one('SELECT status FROM files WHERE id=?',(first['file_id'],))['status']=='ready'
    assert first['reference']['metadata']['url']=='https://example.org/paper.txt'

def test_zotero_pages_all_items_and_keeps_ids_on_resync(client,monkeypatch):
    pid=project(client);requested=[]
    db.set_setting('studio:zotero:'+pid,{'library_id':'42','library_type':'users','attachments':False})
    monkeypatch.setattr(store,'read_secret',lambda name:'synthetic-key')
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params=None,headers=None):
            start=params['start'];requested.append(start)
            rows=[{'key':f'K{i}','version':2,'data':{'title':f'Paper {i}','creators':[],'tags':[{'tag':'主题'}]}} for i in range(start,min(start+100,125))]
            return response(rows,{'Total-Results':'125'})
    monkeypatch.setattr(imports,'http_client',lambda *args,**kwargs:Client())
    task=tasks.enqueue(pid,'studio',{'action':'zotero'})
    result=imports.zotero_sync(task,{})
    before={row['external_id']:row['reference_id'] for row in db.rows('SELECT * FROM studio_external_links WHERE project_id=?',(pid,))}
    imports.zotero_sync(task,{'force':True})
    after={row['external_id']:row['reference_id'] for row in db.rows('SELECT * FROM studio_external_links WHERE project_id=?',(pid,))}
    assert result['seen']==125 and requested==[0,100,0,100] and before==after

def test_tags_persist_and_filter_without_losing_bibliography(client):
    pid=project(client);ref=imports.upsert_reference(pid,{'title':'论文','doi':'10.1234/tags'})
    result=client.put(studio(pid)+'/tags',json={'reference_ids':[ref['id']],'tags':[' OCR ','OCR','检索'],'mode':'replace'})
    assert result.status_code==200
    rows=client.get(studio(pid)+'/library?tag=OCR').json()['items']
    assert rows[0]['id']==ref['id'] and rows[0]['metadata']['tags']==['OCR','检索']

def test_reading_highlight_validates_quote_and_tracks_progress(client):
    pid=project(client);original=file(client,pid,'第一段研究原文。\n\n第二段方法说明。')
    view=reading.document(pid,original['id']);unit=view['units'][0]
    payload={'unit_id':unit['id'],'source_hash':unit['source_hash'],'parse_id':view['parse_id'],'start':0,'end':3,'quote':unit['text'][:3],'color':'yellow','note':'重点'}
    saved=client.post(studio(pid)+f'/reader/{original["id"]}/annotations',json=payload)
    assert saved.status_code==200
    assert client.post(studio(pid)+f'/reader/{original["id"]}/annotations',json={**payload,'quote':'虚构'}).status_code==409
    view=reading.document(pid,original['id']);assert view['annotations'][0]['current']
    assert reading.progress(pid,original['id'],{'unit_id':unit['id'],'completed_units':[unit['id']]})['completed_units']==[unit['id']]

def test_reference_ranges_and_structure_are_navigable():
    assert reading.citation_numbers('[1-3, 5]')==[1,2,3,5]
    units=[{'id':'u1','ordinal':1,'text':'1 Introduction\nBody.','locator':{}},{'id':'u2','ordinal':2,'text':'References\n[1] A. doi:10.1234/probe\n[2] B.','locator':{}}]
    refs=reading.reference_entries(units)
    assert refs[0]['url']=='https://doi.org/10.1234/probe' and refs[1]['unit_id']=='u2'
    assert reading.structure(units)['children'][0]['type']=='section'

def test_translation_reuses_completed_parts_and_keeps_original(client,monkeypatch):
    pid=project(client);original=file(client,pid,'Original academic text.')
    calls=mock_model(monkeypatch,{'text':'忠实译文。'})
    task=tasks.enqueue(pid,'studio',{'action':'translate','file_id':original['id'],'language':'zh-CN'})
    first=tasks.dispatch(task);second=tasks.dispatch(task)
    assert first['saved_parts']>=1 and second['saved_parts']==0 and len(calls)==1
    view=reading.document(pid,original['id'])
    assert view['units'][0]['text']=='Original academic text.' and view['units'][0]['translation']=='忠实译文。'

def test_abstract_translation_is_attached_to_reference_not_thesis(client,monkeypatch):
    pid=project(client);reference=imports.upsert_reference(pid,{'title':'Paper','abstract':'Original abstract'})
    mock_model(monkeypatch,{'text':'文献摘要译文'})
    task=tasks.enqueue(pid,'studio',{'action':'abstract','reference_id':reference['id']})
    result=tasks.dispatch(task)
    assert result['owner_id']==reference['id'] and result['data']['text']=='文献摘要译文'
    assert store.require_reference(pid,reference['id'])['metadata']['abstract']=='Original abstract'

def test_chat_is_persistent_and_cannot_overlap_or_cross_project(client,monkeypatch):
    pid=project(client);other=project(client,'other')
    conversation=store.save(pid,'conversation',{'title':'对话','agent_id':'academic'})
    mock_model(monkeypatch,{'text':'基于当前问题的回复','citations':[]})
    task=ai.send_message(pid,conversation['id'],{'text':'你好','use_knowledge':False})
    with pytest.raises(HTTPException):ai.send_message(pid,conversation['id'],{'text':'重叠请求'})
    result=tasks.dispatch(task)
    assert result['message']['data']['reply_to']==json.loads(task['payload'])['message_id']
    assert [row['data']['role'] for row in ai.messages(pid,conversation['id'])]==['user','assistant']
    assert client.get(studio(other)+'/conversations/'+conversation['id']+'/messages').status_code==404

def test_note_ai_returns_candidate_without_overwriting_original(client,monkeypatch):
    pid=project(client);note=store.save(pid,'note',{'title':'思路','text':'我的原想法'})
    mock_model(monkeypatch,{'title':'整理','text':'模型整理的候选'})
    task=tasks.enqueue(pid,'studio',{'action':'note','note_id':note['id']})
    result=tasks.dispatch(task)
    assert result['candidate']['text']=='模型整理的候选' and store.get(pid,note['id'])['data']['text']=='我的原想法'

def test_comment_thread_and_ai_reply_preserve_selection(client,monkeypatch):
    pid=project(client);sid=section(client,pid);save(client,sid,'可定位的研究论断。')
    current=documents.current(security.require_section(sid));node=json.loads(current['document'])['content'][0]
    root=ai.add_comment(pid,{'section_id':sid,'revision_id':current['id'],'node_id':node['attrs']['nodeId'],'quote':'研究论断','text':'请补充依据'})
    reply=ai.add_comment(pid,{'section_id':sid,'parent_id':root['id'],'text':'准备补充原件'})
    mock_model(monkeypatch,{'text':'建议明确适用范围与来源。'})
    task=tasks.enqueue(pid,'studio',{'action':'comment_reply','comment_id':root['id']});result=tasks.dispatch(task)
    assert reply['data']['parent_id']==root['id'] and result['data']['author']=='ai'
    assert ai.comment_context(pid,root)[3]

def test_completion_rejects_stale_versions(client,monkeypatch):
    pid=project(client);sid=section(client,pid);save(client,sid,'上文。')
    revision=documents.current(security.require_section(sid));mock_model(monkeypatch,{'text':'短续文'})
    task=tasks.enqueue(pid,'studio',{'action':'complete','section_id':sid,'revision_id':revision['id'],'prefix':'上文。','suffix':''})
    assert tasks.dispatch(task)['text']=='短续文'
    save(client,sid,'修改后的正文。',revision['id'])
    with pytest.raises(ValueError,match='版本'):tasks.dispatch(task)

def test_latex_archive_contains_actual_body_bibliography_and_instructions(client):
    pid=project(client);sid=section(client,pid);save(client,sid,'正文包含 50% & 转义测试。')
    result=exports.latex_project(pid,{'language':'zh'})
    from app.config import DATA
    with zipfile.ZipFile(DATA/'exports'/result['export_id']/'latex-project.zip') as archive:
        assert {'main.tex','references.bib','HOW_TO_COMPILE.md','manifest.json'}<=set(archive.namelist())
        content=archive.read('main.tex').decode()
        assert '\\documentclass[12pt,a4paper]{ctexart}' in content and '50\\% \\&' in content
        assert json.loads(archive.read('manifest.json'))['sections'][0]['section_id']==sid

def test_deletion_removes_current_entry_but_keeps_original_history(client):
    pid=project(client);original=file(client,pid,'保留历史原件。');reference=imports.upsert_reference(pid,{'title':'书目'},original['id'])
    preview=exports.deletion_preview(pid,[reference['id']])
    result=exports.delete_references(pid,{'reference_ids':[reference['id']],'fingerprint':preview['fingerprint']})
    assert result['deleted']==1 and not client.get(f'/api/v1/projects/{pid}/references').json()
    assert security.safe_object(original['sha256']).exists() and db.one('SELECT id FROM bibliography_revisions WHERE reference_id=?',(reference['id'],))

def test_structured_parse_adoption_creates_new_version_without_fake_review(client):
    pid=project(client);original=file(client,pid,'原生文字。')
    payload={'blocks':[{'text':'标题','type':'heading','page':1,'bbox':[0,0,10,10]},{'text':'OCR候选正文。','type':'paragraph','page':1,'bbox':[0,20,100,40]}]}
    task=tasks.enqueue(pid,'studio',{'action':'parse','file_id':original['id']})
    record=parsing.save_result(task,original,'surya-local',payload,None)
    result=parsing.adopt(pid,record['id'],record['revision'])
    view=reading.document(pid,original['id'])
    assert result['units']==2 and view['units'][0]['text']=='标题'
    assert db.one('SELECT review_state FROM knowledge_units WHERE id=?',(view['units'][0]['id'],))['review_state']=='pending'

def test_canvas_preview_is_archived_and_scene_stays_editable(client):
    from PIL import Image
    pid=project(client);record=store.save(pid,'canvas',{'title':'草图','scene':{'version':1,'elements':[]}})
    buffer=io.BytesIO();Image.new('RGB',(100,100),'white').save(buffer,format='PNG')
    result=client.post(studio(pid)+'/canvas/'+record['id']+'/image',json={'revision':record['revision'],'png':base64.b64encode(buffer.getvalue()).decode()})
    assert result.status_code==200
    artifact=db.one('SELECT * FROM artifacts WHERE id=?',(result.json()['artifact_id'],))
    assert json.loads(artifact['metadata'])['studio_canvas_id']==record['id'] and store.get(pid,record['id'])['data']['scene']['version']==1

def test_orphan_preview_protects_objects_owned_by_studio_records(client):
    pid=project(client);kept=security.put_blob(b'owned-scene-resource');unused=security.put_blob(b'orphan-probe')
    store.save(pid,'note',{'title':'附件','sha256':kept})
    import os,time
    old=time.time()-90000;os.utime(security.safe_object(unused),(old,old))
    preview=exports.orphan_objects()
    hashes={item['sha256'] for item in preview['items']}
    assert unused in hashes and kept not in hashes

def test_inline_math_is_a_real_document_node_and_exports_to_markdown(client):
    from app.export_checks import markdown_document
    pid=project(client);sid=section(client,pid)
    document={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'公式 '},{'type':'inlineFormula','attrs':{'latex':'x^2'}}]}]}
    result=documents.save(security.require_section(sid),document,None)
    assert '$x^2$' in markdown_document(result['document'] if isinstance(result.get('document'),dict) else documents.normalize(document))

def test_metadata_graph_adds_authors_keywords_with_bibliography_provenance(client):
    from app.studio_graph import enrich
    pid=project(client);reference=imports.upsert_reference(pid,{'title':'Paper','authors':['王明'],'tags':['OCR']})
    result=enrich(pid,reference['id'])
    assert len(result['relations'])==2
    assert {row['kind'] for row in db.rows('SELECT kind FROM kg_entities WHERE project_id=?',(pid,))}>={'document','author','keyword'}
    assert db.one("SELECT id FROM knowledge_dependencies WHERE project_id=? AND upstream_type='reference'",(pid,))

def test_oauth_state_is_single_use_and_expired_callbacks_fail(client,monkeypatch):
    import time
    pid=project(client);secrets={}
    monkeypatch.setenv('PAPER_ZOTERO_CLIENT_KEY','synthetic-app')
    monkeypatch.setenv('PAPER_ZOTERO_CLIENT_SECRET','synthetic-secret')
    monkeypatch.setattr(store,'write_secret',lambda key,value:secrets.update({key:value}))
    monkeypatch.setattr(store,'read_secret',lambda key:secrets.get(key,''))
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers):
            value=response({});value.text='oauth_token=temporary&oauth_token_secret=temporary-secret' if url.endswith('/request') else 'oauth_token=access-key&userID=42'
            return value
    monkeypatch.setattr(imports,'http_client',lambda *args,**kwargs:Client())
    started=imports.begin_oauth(pid,'http://localhost/callback')
    assert 'temporary' in started['authorize_url']
    assert imports.finish_oauth('temporary','verifier')['connected']
    assert secrets['zotero:'+pid]=='access-key' and not secrets['zotero-oauth:temporary']
    with pytest.raises(ValueError,match='过期|已经'):imports.finish_oauth('temporary','verifier')
    db.execute('INSERT INTO studio_oauth VALUES(?,?,?,?,0)',('expired',pid,'http://localhost',time.time()-1))
    with pytest.raises(ValueError,match='过期'):imports.finish_oauth('expired','verifier')

def test_public_download_rejects_local_addresses(monkeypatch):
    monkeypatch.delenv('PAPER_ALLOW_LOCAL_IMPORT',raising=False)
    monkeypatch.setattr(imports.socket,'getaddrinfo',lambda *args,**kwargs:[(2,1,6,'',('127.0.0.1',80))])
    with pytest.raises(ValueError,match='内网'):imports.public_url('http://example.org/private')

def test_openalex_parameters_and_candidate_metadata(client,monkeypatch):
    pid=project(client);captured={}
    monkeypatch.setattr(store,'read_secret',lambda key:'synthetic-openalex-key')
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url,params,headers):
            captured.update(params)
            return response({'meta':{'count':1},'results':[{'id':'https://openalex.org/W1','display_name':'Study','publication_year':2026,'authorships':[{'author':{'display_name':'A'}}],'abstract_inverted_index':{'A':[0],'study':[1]},'best_oa_location':{'pdf_url':'https://example.org/paper.pdf'}}]})
    monkeypatch.setattr(imports,'http_client',lambda *args,**kwargs:Client())
    value=imports.openalex(pid,{'query':'study','from_year':2020,'open_access':True})
    assert captured['api_key']=='synthetic-openalex-key' and 'is_oa:true' in captured['filter']
    assert value['items'][0]['abstract']=='A study' and value['items'][0]['pdf_url'].endswith('.pdf')

def test_remote_execution_validates_image_and_archives_result(client,monkeypatch):
    from PIL import Image
    from app import studio_execution as execution
    pid=project(client);buffer=io.BytesIO();Image.new('RGB',(40,30),'blue').save(buffer,format='PNG');raw=buffer.getvalue()
    db.set_setting('studio:execution:'+pid,{'provider':'remote-http','url':'https://execution.example'})
    monkeypatch.setattr(store,'read_secret',lambda name:'synthetic-execution-key')
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,headers,json):return response({'exit_code':0,'stdout':'done','stderr':'','images':[{'name':'plot.png','content_base64':base64.b64encode(raw).decode(),'sha256':__import__('hashlib').sha256(raw).hexdigest()}]})
    monkeypatch.setattr(execution,'http_client',lambda *args,**kwargs:Client())
    task=tasks.enqueue(pid,'studio',{'action':'execute','code':'print(1)'})
    value=execution.execute(task,{'code':'print(1)','timeout_seconds':10})
    assert value['stdout']=='done' and value['artifacts'][0]['artifact_id']
    assert db.one('SELECT id FROM artifacts WHERE project_id=?',(pid,))

def test_metadata_graph_inherits_original_egress_policy(client):
    from app import studio_graph,knowledge_lineage as lineage
    pid=project(client);original=file(client,pid,'真实资料。');reference=imports.upsert_reference(pid,{'title':'Study','authors':['A'],'tags':['B']},original['id'])
    value=studio_graph.enrich(pid,reference['id'])
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    with pytest.raises(HTTPException):lineage.version_of(pid,'assertion',value['relations'][0],remote=True)

def test_revoked_document_is_not_resent_through_conversation_history(client,monkeypatch):
    pid=project(client);original=file(client,pid,'Confidential original.');conversation=store.save(pid,'conversation',{'title':'讨论'})
    mock_model(monkeypatch,{'text':'Source-derived reply','citations':[]})
    task=ai.send_message(pid,conversation['id'],{'text':'read','file_ids':[original['id']],'use_knowledge':False});tasks.dispatch(task)
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(task['id'],));db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    task2=ai.send_message(pid,conversation['id'],{'text':'continue','file_ids':[],'use_knowledge':False})
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:pytest.fail('Revoked history must not reach model'))
    with pytest.raises(HTTPException):tasks.dispatch(task2)

def test_new_records_and_blobs_survive_complete_backup_and_offline_cli(client):
    from app import exports as workbench_exports
    from pathlib import Path
    import subprocess
    pid=project(client);original=file(client,pid,'Offline keyword corpus.');reference=imports.upsert_reference(pid,{'title':'Offline study'},original['id'])
    note=store.save(pid,'note',{'title':'Offline note','text':'Retain this note'})
    backup=workbench_exports.backup();archive=workbench_exports.DATA/'backups'/backup['filename']
    with zipfile.ZipFile(archive) as zipped:
        assert 'workbench.sqlite3' in zipped.namelist()
        assert not any('credential' in name for name in zipped.namelist())
    restored=workbench_exports.restore_backup(archive.read_bytes())
    import sqlite3
    with sqlite3.connect(Path(restored['restored_data_directory'])/'workbench.sqlite3') as connection:
        assert connection.execute('SELECT data FROM studio_records WHERE id=?',(note['id'],)).fetchone()[0].find('Retain this note')>=0
    root=Path(__file__).resolve().parents[1];node=__import__('shutil').which('node')
    for args in [['summary'],['list','knowledge'],['get','knowledge',reference['id']],['search','keyword']]:
        run=subprocess.run([node,str(root/'scripts/yantai-data.mjs'),*args,'--snapshot',str(archive),'--project',pid],capture_output=True,text=True,encoding='utf-8',timeout=20)
        assert run.returncode==0,run.stderr
        value=json.loads(run.stdout)
        if args[0]=='get':assert value['id']==reference['id']
        if args[0]=='search':assert value['items'][0]['file_id']==original['id']

def test_visual_inline_formula_exports_as_native_office_math(client):
    from app import exports as workbench_exports
    pid=project(client);sid=section(client,pid);manual_package(client,pid)
    document={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':db.uid()},'content':[{'type':'text','text':'Inline: '},{'type':'inlineFormula','attrs':{'latex':r'\frac{x}{2}','nodeId':db.uid()}}]}]}
    documents.save(security.require_section(sid),document,None)
    result=workbench_exports.export_project(pid,{'mode':'draft'})
    directory=workbench_exports.DATA/'exports'/result['export_id']
    docx=next(directory.glob('*.docx'))
    with zipfile.ZipFile(docx) as archive:
        content=archive.read('word/document.xml').decode()
        assert '<m:f>' in content and '<m:oMath' in content

def test_invalid_current_selection_never_gets_annotated_after_reparse(client):
    from app import knowledge_units
    pid=project(client);original=file(client,pid,'A source paragraph.')
    view=reading.document(pid,original['id']);unit=view['units'][0]
    knowledge_units.build(pid,original['id'],strategy='page',allow_material=True)
    with pytest.raises(HTTPException):reading.annotate(pid,original['id'],{'parse_id':view['parse_id'],'unit_id':unit['id'],'source_hash':unit['source_hash'],'start':0,'end':3,'quote':unit['text'][:3]})

def test_record_lists_keep_latest_messages_and_translation_cache_is_not_capped(client,monkeypatch):
    pid=project(client);original=file(client,pid,'Original sentence.');view=reading.document(pid,original['id']);unit=view['units'][0]
    store.save(pid,'translation',{'unit_id':unit['id'],'parse_id':view['parse_id'],'source_hash':unit['source_hash'],'language':'zh-CN','part':0,'parts':1,'text':'已完成译文'},owner=original['id'])
    conversation=store.save(pid,'conversation',{'title':'长会话'})
    with db.transaction():
        for index in range(510):
            store.save(pid,'message',{'role':'user','text':str(index)},owner=conversation['id'])
            store.save(pid,'translation',{'unit_id':'other-'+str(index),'parse_id':view['parse_id'],'language':'zh-CN','part':0,'parts':1,'source_hash':'other','text':'other'},owner=original['id'])
    assert store.list_records(pid,'message',conversation['id'],500)[-1]['data']['text']=='509'
    assert reading.document(pid,original['id'])['units'][0]['translation']=='已完成译文'
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:pytest.fail('Completed translation should not be requested again'))
    task=tasks.enqueue(pid,'studio',{'action':'translate','file_id':original['id'],'language':'zh-CN'})
    assert tasks.dispatch(task)['cached_parts']==1

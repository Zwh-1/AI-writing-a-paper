"""Cross-entry contracts: current content, provenance, durable commands and transfer."""
import copy
import io
import json
import sqlite3
import zipfile
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,save
from app import db,documents,security,tasks,model_install
from app import research_records as records,reference_service,reading_service,parsing_service,resource_refs

def adopt_layout(pid,original,text):
    task=tasks.enqueue(pid,'studio',{'action':'parse','file_id':original['id']})
    result=parsing_service.save_result(task,original,'mineru',{'blocks':[{'type':'text','text':text,'page':1}]},None)
    return parsing_service.adopt(pid,result['id'],result['revision'])

def test_w01_adopted_layout_is_shared_by_reader_search_assistant_and_summary(client,monkeypatch):
    from app import reference_knowledge,knowledge_service,assistant_service,material_summary
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False})
    pid=project(client);original=file(client,pid,'Legacy text with wrong recognition.')
    reference=reference_service.upsert_external(pid,{'title':'Shared original','doi':'10.1/shared'},original['id'])
    reading_service.document(pid,original['id'])
    adopted=adopt_layout(pid,original,'UniqueLayoutPhrase conservation conditions.')
    view=reading_service.document(pid,original['id'])
    assert view['parse_id']==adopted['parse_id']
    public=client.post(f'/api/v1/projects/{pid}/knowledge-base/search',json={'query':'UniqueLayoutPhrase'}).json()
    assert public['items'][0]['source_ref']['parse_id']==adopted['parse_id']
    for search in (reference_knowledge.search,knowledge_service.search):
        hits=search(pid,'UniqueLayoutPhrase')['items']
        assert hits and hits[0]['knowledge_unit_id']==view['units'][0]['id']
        assert not search(pid,'wrong recognition')['items']
    _,pages,_=material_summary.source(original['id'])
    assert 'UniqueLayoutPhrase' in pages[0]['text']
    conversation=records.conversations.save(pid,{'title':'Check layout'})
    def fake(task,system,user,manifest,images=None):
        payload=json.loads(user);assert 'UniqueLayoutPhrase' in payload['originals'][0]['text']
        return {'text':json.dumps({'text':'Bound to current layout.','citations':[payload['originals'][0]['source_id']]}),'run_id':'synthetic'}
    monkeypatch.setattr(tasks,'model_call',fake)
    result=tasks.dispatch(assistant_service.send_message(pid,conversation['id'],{'text':'UniqueLayoutPhrase','use_knowledge':True}))
    source=result['message']['data']['citations'][0]['source_ref']
    assert source['type']=='unit' and resource_refs.resolve(pid,source)['current']
    assert reference['id']==db.one('SELECT id FROM bibliography WHERE file_id=?',(original['id'],))['id']

def test_w02_manual_and_connector_changes_share_history_and_graph_invalidation(client):
    pid=project(client);original=file(client,pid)
    db.set_setting('graph_wiki_enabled:'+pid,True)
    row=reference_service.upsert_external(pid,{'title':'First','authors':['A'],'tags':['one']},original['id'])
    manual=client.patch('/api/v1/references/'+row['id'],json={'metadata':{'title':'Second','authors':['B'],'tags':['two']}})
    assert manual.status_code==200
    reference_service.set_tags(pid,[row['id']],['three'])
    assert reference_service.get(pid,row['id'])['revision']==3
    assert db.one('SELECT count(*) n FROM bibliography_revisions WHERE reference_id=?',(row['id'],))['n']==2
    assert db.one("SELECT count(*) n FROM knowledge_events WHERE project_id=? AND source_type='reference' AND source_id=?",(pid,row['id']))['n']>=3
    assert db.one("SELECT e.id FROM kg_entities e JOIN kg_entity_versions v ON v.id=e.current_version_id WHERE e.project_id=? AND v.name='B'",(pid,))

def annotated_note(client):
    from app.research_workflows import annotation_note
    pid=project(client);original=file(client,pid,'A precise original observation.')
    view=reading_service.document(pid,original['id']);unit=view['units'][0]
    annotation=reading_service.annotate(pid,original['id'],{'unit_id':unit['id'],'parse_id':view['parse_id'],'source_hash':unit['source_hash'],'start':0,'end':9,'quote':unit['text'][:9],'note':'Personal interpretation'})
    note=annotation_note(pid,annotation['id'])
    return pid,original,view,annotation,note

def test_w03_annotation_note_has_explicit_owner_and_transitive_source(client):
    pid,original,view,annotation,note=annotated_note(client)
    assert note['classification']=='personal_note'
    owner=db.one('SELECT * FROM research_record_owners WHERE record_id=?',(annotation['id'],))
    assert owner['file_id']==original['id']
    assert any(ref['type']=='annotation' for ref in note['source_refs'])
    assert resource_refs.version_of(pid,'note',note['id'])
    from app.research_workflows import annotation_note
    assert annotation_note(pid,annotation['id'])['id']==note['id']
    candidate=client.post(f'/api/v1/projects/{pid}/reading-files/{original["id"]}/evidence',json={key:annotation['data'][key] for key in ('unit_id','parse_id','start','end','quote')})
    assert candidate.status_code==200,candidate.text
    assert candidate.json()['kind']=='literature'


def test_webpage_is_available_as_a_versioned_shared_asset(client,monkeypatch):
    from app import assistant_service,asset_service
    pid=project(client);original=file(client,pid,'Synthetic original.')
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:{'text':json.dumps({'html':'<!doctype html><html><body>Derived page</body></html>'}),'run_id':'fake'})
    task=tasks.enqueue(pid,'studio',{'action':'webpage','file_id':original['id']})
    result=tasks.dispatch(task)
    asset=asset_service.get(pid,result['artifact_id'])
    assert asset['kind']=='webpage' and asset['metadata']['source_refs'][0]['type']=='webpage'
    assert asset['asset_version_id'] and asset['metadata']['review_state']=='unverified'

def test_w04_note_adoption_uses_writing_commit_and_remains_unverified(client):
    from app.research_workflows import note_section,note_proposal
    from app.assistant_context import adopt
    pid,_,_,_,note=annotated_note(client);sid=section(client,pid);base=save(client,sid,'Original paragraph.').json()
    note=note_section(pid,note['id'],sid,note['revision'])
    proposal=note_proposal(pid,note['id'],sid,base['id'])
    adopted=adopt(pid,proposal['id'],proposal['revision'])
    assert adopted['review_state']=='unverified' and 'Personal interpretation' in adopted['text']
    assert db.one('SELECT id FROM document_analyses WHERE revision_id=?',(adopted['id'],))
    assert db.one("SELECT id FROM dependencies WHERE revision_id=? AND upstream_type='knowledge:note'",(adopted['id'],))
    with pytest.raises(HTTPException):adopt(pid,proposal['id'],proposal['revision'])

def test_w05_asset_versions_and_targeted_intents_do_not_replace_history(client):
    from app import asset_service,workspace_intents
    from PIL import Image
    import base64
    pid=project(client);sid=section(client,pid);base=save(client,sid,'Start.').json()
    canvas=records.canvases.save(pid,{'title':'Diagram','scene':{'version':1,'elements':[]}})
    image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,format='PNG')
    asset=asset_service.publish_canvas(pid,canvas['id'],{'revision':1,'png':base64.b64encode(image.getvalue()).decode()})
    intent=workspace_intents.create(pid,{'section_id':sid,'artifact_id':asset['id']})
    doc=json.loads(base['document']);doc['content'].append({'type':'figure','attrs':{'artifactId':asset['id'],'assetVersionId':asset['asset_version_id'],'caption':'Pinned'}})
    saved=documents.save(security.require_section(sid),doc,base['id'])
    consumed=workspace_intents.finish(pid,intent['id'],{'revision_id':saved['id']})
    assert consumed['state']=='consumed' and not workspace_intents.pending(pid,sid)
    assert workspace_intents.finish(pid,intent['id'],{'revision_id':saved['id']})['state']=='consumed'
    db.execute('UPDATE artifacts SET name=? WHERE id=?',('Renamed',asset['id']))
    current=asset_service.get(pid,asset['id'])
    assert current['asset_version_id']!=asset['asset_version_id']
    from app.export_checks import freeze
    frozen=freeze(pid)
    assert frozen['artifacts'][asset['asset_version_id']]['name']=='Diagram'

def test_w06_export_history_lists_real_files_for_each_format(client):
    from app.export_service import generate,history
    pid=project(client);sid=section(client,pid);save(client,sid,'Shared export body.')
    for format_ in ('docx','latex','markdown'):
        result=generate(pid,{'format':format_,'language':'en','pdf':False})
        assert result['format']==format_ and result['snapshot_id']
        for artifact in result['artifacts']:
            response=client.get(artifact['download_url']);assert response.status_code==200,artifact
    rows=history(pid);tex=next(row for row in rows if row['format']=='latex')
    assert [item['name'] for item in tex['artifacts']]==['latex-project.zip']
    with zipfile.ZipFile(io.BytesIO(client.get(tex['artifacts'][0]['download_url']).content)) as archive:
        assert 'research-trace.json' in archive.namelist()

def test_w07_task_handle_and_domain_record_are_distinct(client):
    pid=project(client)
    response=client.post(f'/api/v2/projects/{pid}/research-records',json={'kind':'canvas','data':{'title':'Canvas','scene':{'version':1,'elements':[]}}})
    assert response.status_code==200,response.text
    assert response.json()['data']['resource_type']=='research_record'
    queued=client.post(f'/api/v2/projects/{pid}/exports',json={'format':'markdown'}).json()
    assert queued['meta']['kind']=='task' and queued['data']['task']['status']=='queued'
    task=queued['data']['task'];result=tasks.dispatch(task)
    assert result['format']=='markdown'


def test_message_execution_reuses_pending_task_and_restores_saved_result(client,monkeypatch):
    from app import execution_service
    pid=project(client);conversation=records.conversations.save(pid,{'title':'Execution'})
    message=records.save(pid,'message',{'role':'assistant','text':'```python\nprint(42)\n```'},owner=conversation['id'])
    body={'code':'print(42)','timeout_seconds':30,'message_id':message['id']}
    monkeypatch.setattr(execution_service,'execute',lambda task,payload:{'stdout':'42','artifacts':[],'exit_code':0})
    first=client.post(f'/api/v1/projects/{pid}/studio/execution',json=body).json()
    second=client.post(f'/api/v1/projects/{pid}/studio/execution',json=body).json()
    assert first['id']==second['id']
    assert tasks.run_one()
    result=client.get(f'/api/v1/projects/{pid}/studio/execution/results',params={'message_id':message['id']}).json()
    assert result['result']['stdout']=='42'

def test_w08_changing_parse_invalidates_transitive_annotations_notes_and_drafts(client,monkeypatch):
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False})
    pid,original,_,annotation,note=annotated_note(client)
    old_ref=resource_refs.make(pid,'note',note['id'])
    adopt_layout(pid,original,'Replacement layout content.')
    assert records.notes.get(pid,note['id'])['source_state']=='stale'
    with pytest.raises(HTTPException):resource_refs.validate(pid,[old_ref],remote=True)

def test_w09_complete_bundle_preserves_explicit_links_and_forkable_asset_versions(client):
    from app import knowledge_transfer,asset_service
    from app.resource_registry import validate_relations
    pid,_,_,_,note=annotated_note(client)
    asset=asset_service.create(pid,{'title':'Data','text':'First'})
    asset_service.update(pid,asset['id'],{'revision':1,'title':'Data','text':'Second'})
    result=knowledge_transfer.export(pid)
    raw=open(result['path'],'rb').read();manifest,tables=knowledge_transfer.validate(raw)
    assert len(tables['asset_versions'])==2 and tables['research_links']
    assert validate_relations(pid,tables)['relations_verified']
    corrupt=copy.deepcopy(tables);corrupt['research_links'][0]['target_id']='0'*32
    with pytest.raises(ValueError):validate_relations(pid,corrupt)

def test_w10_online_offline_projection_matches_and_finds_current_layout(client,monkeypatch,tmp_path):
    from app.workspace_query import query
    import importlib.util
    from app.config import ROOT
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False})
    pid=project(client);original=file(client,pid,'Old text.')
    adopt_layout(pid,original,'UnifiedProjectionPhrase current.')
    spec=importlib.util.spec_from_file_location('offline_projection_test',ROOT/'scripts/yantai_query_projection.py')
    projection=importlib.util.module_from_spec(spec);spec.loader.exec_module(projection)
    args={'command':'search','query':'UnifiedProjectionPhrase'}
    with db.connect() as connection:offline=projection.query(connection,pid,args)
    assert query(pid,args)==offline and offline['items'][0]['parse_id']

def test_incremental_sync_detects_conflict_and_preserves_both_histories(client):
    from app import research_sync
    pid=project(client);note=records.notes.save(pid,{'title':'Shared','text':'Base'})
    baseline=research_sync.snapshot(pid)
    remote=copy.deepcopy(baseline);remote['base']=baseline['heads'];remote['records'][0]['data']=db.dumps({'title':'Shared','text':'Remote edit'});remote['records'][0]['revision']=2
    records.notes.save(pid,{'title':'Shared','text':'Local edit'},note['id'],expected_revision=1)
    result=research_sync.apply(pid,remote)
    assert not result['merged'] and len(result['conflicts'])==1
    conflict=research_sync.conflicts(pid)[0]
    research_sync.resolve(pid,conflict['id'],{'choice':'copy_remote','local_hash':conflict['local_hash']})
    assert len(records.notes.list(pid))==2
    assert research_sync.apply(pid,remote)['idempotent_replay']
    assert db.one('SELECT id FROM research_sync_history WHERE project_id=?',(pid,))

def test_typed_records_reject_cross_project_owner_and_invalid_field_types(client):
    pid=project(client);other=project(client,'Other');original=file(client,other)
    response=client.post(f'/api/v1/projects/{pid}/studio/records',json={'kind':'note','owner_id':original['id'],'data':{'title':'Wrong owner'}})
    assert response.status_code==422


def test_legacy_note_source_permissions_are_not_lost_before_backfill(client):
    pid=project(client);original=file(client,pid,'Confidential original.')
    conversation=records.conversations.save(pid,{'title':'Legacy'})
    message=records.save(pid,'message',{'role':'assistant','text':'Private summary','file_ids':[original['id']]},owner=conversation['id'])
    note=records.notes.save(pid,{'title':'Legacy note','text':'Private summary','message_id':message['id']})
    db.execute('DELETE FROM research_links WHERE record_id=?',(note['id'],))
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    with pytest.raises(HTTPException):resource_refs.version_of(pid,'note',note['id'],remote=True)
    response=client.post(f'/api/v1/projects/{pid}/studio/records',json={'kind':'note','data':{'title':123,'tags':'wrong'}})
    assert response.status_code==422


def test_complete_fork_rebases_research_links_without_changing_original(client):
    from app import knowledge_transfer
    pid,original,_,_,note=annotated_note(client)
    bundle=knowledge_transfer.export(pid)
    raw=open(bundle['path'],'rb').read()
    result=knowledge_transfer.restore(raw,{'mode':'fork','expected_sha256':bundle['sha256'],'title':'Research fork'})
    target=result['project_id'];assert target!=pid
    notes=records.notes.list(target);assert len(notes)==1 and notes[0]['id']!=note['id']
    for ref in notes[0]['source_refs']:
        assert ref.get('project_id',target)==target
        resolved=resource_refs.resolve(target,ref)
        assert resolved['location']['file_id']!=original['id']
    assert records.notes.get(pid,note['id'])['data']['text']=='Personal interpretation'


def test_schema16_knowledge_package_still_imports_with_unknown_provenance(client):
    from app import knowledge_transfer
    pid=project(client);records.notes.save(pid,{'title':'Legacy note','text':'Retain old text'})
    bundle=knowledge_transfer.export(pid);raw=open(bundle['path'],'rb').read();output=io.BytesIO()
    new_tables={'research_record_owners','research_links','research_record_states','asset_versions','workspace_intents','research_sync_conflicts','research_sync_receipts','research_sync_history'}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest=json.loads(archive.read('manifest.json'));manifest['schema']=16
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
            for name in list(manifest['tables']):
                if name in new_tables:
                    manifest['tables'].pop(name);manifest['files'].pop('tables/'+name+'.json')
            for name in manifest['files']:
                content=archive.read(name)
                if name=='tables/studio_records.json':
                    rows=json.loads(content)
                    for row in rows:row.pop('schema_version',None)
                    content=json.dumps(rows,ensure_ascii=False).encode()
                    import hashlib
                    manifest['files'][name]=hashlib.sha256(content).hexdigest()
                    manifest['tables']['studio_records']['columns'].remove('schema_version')
                target.writestr(name,content)
            target.writestr('manifest.json',json.dumps(manifest))
    legacy=output.getvalue();preview=knowledge_transfer.preview(legacy)
    result=knowledge_transfer.restore(legacy,{'mode':'fork','expected_sha256':preview['sha256']})
    note=records.notes.list(result['project_id'])[0]
    assert note['data']['text']=='Retain old text' and note['source_state']=='unknown'


def test_incremental_fast_forward_is_idempotent_and_tombstones_remain_history(client):
    from app import research_sync
    pid=project(client);note=records.notes.save(pid,{'title':'Base','text':'base'})
    baseline=research_sync.snapshot(pid)
    packet=copy.deepcopy(baseline);packet['base']=baseline['heads']
    packet['records'][0].update(data=db.dumps({'title':'Remote','text':'updated'}),revision=2)
    result=research_sync.apply(pid,packet)
    assert result['merged']==[note['id']] and not result['conflicts']
    assert records.notes.get(pid,note['id'])['data']['text']=='updated'
    assert research_sync.apply(pid,packet)['idempotent_replay']
    tombstone=research_sync.snapshot(pid);tombstone['base']=tombstone['heads'];tombstone['records'][0]['deleted_at']=db.now()
    result=research_sync.apply(pid,tombstone)
    assert result['merged']==[note['id']] and records.notes.list(pid)==[]
    assert db.one('SELECT id FROM studio_versions WHERE record_id=?',(note['id'],))

"""Missing cross-feature behaviors found during the final completeness review."""
import copy
import io
import json
import pytest
from PIL import Image
from fastapi import HTTPException
from test_workbench import client,project,section,save,file
from app import db,security,documents,provider,research_records as records,research_sync


def test_account_balance_and_request_statistics_have_separate_contracts():
    base='https://api.x5m5x.com/v1/'
    assert provider.usage_endpoint(base).endswith('/v1/dashboard/billing/subscription')
    assert provider.usage_statistics_endpoint(base)=='https://api.x5m5x.com/v1/usage'
    assert provider.usage_statistics_endpoint('https://relay.example/v1///')=='https://relay.example/v1/usage'


def test_remote_revision_history_can_be_restored_without_overwriting_history(client):
    pid=project(client);note=records.notes.save(pid,{'title':'Base','text':'first'})
    packet=research_sync.snapshot(pid);packet['base']=packet['heads']
    remote=packet['records'][0];remote['versions']=[{'revision':1,'data':remote['data'],'created_at':remote['created_at']}]
    remote.update(data=db.dumps({'title':'Remote','text':'second'}),revision=2)
    result=research_sync.apply(pid,packet);assert result['merged']==[note['id']]
    latest=records.notes.get(pid,note['id']);history=records.sync_history(pid,note['id'])[0]
    restored=client.post(f'/api/v1/projects/{pid}/studio/records/{note["id"]}/restore-sync',json={'history_id':history['id'],'target_revision':1,'revision':latest['revision']})
    assert restored.status_code==200,restored.text
    assert restored.json()['data']['text']=='first' and restored.json()['revision']>latest['revision']
    assert restored.json()['source_state']=='unknown'
    assert any(row['data']['text']=='second' for row in records.versions(pid,note['id']))


@pytest.mark.parametrize('change',['malformed_json','kind','owner','bad_id'])
def test_sync_rejects_malformed_or_identity_changing_records_atomically(client,change):
    pid=project(client);note=records.notes.save(pid,{'title':'Original'})
    packet=research_sync.snapshot(pid);packet['base']=packet['heads'];row=packet['records'][0]
    if change=='malformed_json':row['data']='invalid json'
    elif change=='kind':row.update(kind='conversation')
    elif change=='owner':row['owner_id']='0'*32
    else:row['id']='bad/id'
    with pytest.raises(HTTPException) as error:research_sync.apply(pid,packet)
    assert error.value.status_code==422
    assert records.notes.get(pid,note['id'])['data']['title']=='Original'
    assert not db.rows('SELECT * FROM research_sync_receipts WHERE project_id=?',(pid,))


def test_sync_provenance_changes_are_part_of_record_fingerprint(client):
    from app.resource_refs import make
    pid=project(client);source=file(client,pid,'A source observation.')
    note=records.notes.save(pid,{'title':'Personal note'})
    baseline=research_sync.snapshot(pid)
    previous=baseline['heads'][note['id']]
    data={**note['data'],'source_refs':[make(pid,'file',source['id'])]}
    records.notes.save(pid,data,note['id'],expected_revision=1)
    assert research_sync.snapshot(pid)['heads'][note['id']]!=previous


def test_markdown_exports_only_used_images_and_converts_jpeg_to_png(client):
    from app.export_service import generate
    from app.config import DATA
    pid=project(client);sid=section(client,pid)
    image=io.BytesIO();Image.new('RGB',(12,12),'blue').save(image,format='JPEG')
    used,unused=db.uid(),db.uid();sha=security.put_blob(image.getvalue())
    for identity in (used,unused):db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(identity,pid,'JPEG image','figure',sha,db.dumps({'egress':'selected_content'}),db.now()))
    document={'type':'doc','content':[{'type':'figure','attrs':{'artifactId':used,'caption':'Used JPEG'}}]}
    saved=documents.save(security.require_section(sid),document,None)
    version=json.loads(saved['document'])['content'][0]['attrs']['assetVersionId']
    result=generate(pid,{'format':'markdown'});folder=DATA/'exports'/result['export_id']
    with Image.open(folder/('图-'+version+'.png')) as picture:assert picture.format=='PNG'
    assert not (folder/('图-'+unused+'.png')).exists()
    assert ('图-'+version+'.png') in (folder/'document.md').read_text(encoding='utf-8')


def test_rich_editor_optional_null_attributes_do_not_prevent_note_or_asset_save(client):
    pid=project(client)
    attrs={key:None for key in ('nodeId','artifactId','assetVersionId','caption','note','tableStyle','describesFigureId','explanationNodeId','diagramType','figurePurpose')}
    attrs.update({key:[] for key in ('sourceIds','sourceRefs','factIds','evidenceIds','knowledgeUnitIds','documentReadingIds','knowledgeClaimIds','wikiVersionIds','wikiBlockIds','relatedSectionIds')})
    document={'type':'doc','content':[{'type':'paragraph','attrs':attrs,'content':[{'type':'text','text':'Editable rich note'}]}]}
    response=client.post(f'/api/v1/projects/{pid}/studio/records',json={'kind':'note','data':{'title':'Rich defaults','document':document}})
    assert response.status_code==200,response.text
    assert response.json()['data']['text']=='Editable rich note'
    response=client.post(f'/api/v1/projects/{pid}/studio/assets',json={'title':'Asset defaults','document':document})
    assert response.status_code==200,response.text

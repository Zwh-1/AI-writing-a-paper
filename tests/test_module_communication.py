"""Consumer-level contracts for execution, writing snapshots and dataflow queries."""
import base64
import io
import json
import pytest
from fastapi import HTTPException
from PIL import Image
from test_workbench import client,project,file,section,save,manual_package
from test_product_interop import summary
from test_writing_agent import env,fake_model,finish,start
from app import db,security,tasks,provider,documents,research_records as records,resource_refs as refs
from app import product_service as products,product_relations,product_execution,execution_service,section_materials,asset_service,paper_visuals


def note(pid,text='A real reusable output.'):
    return records.notes.save(pid,{'title':'Input note','text':text})


def test_execution_consumes_actual_note_summary_file_and_native_table(client,monkeypatch):
    pid=project(client);original=file(client,pid,'Original twelve samples.');memo=note(pid)
    table=paper_visuals.create_visual(pid,{'spec':{'kind':'three_line_table','title':'Actual table','headers':['Group','N'],'rows':[['A','12']],'note':'Synthetic'}})
    sources=[refs.make(pid,'note',memo['id']),summary(pid,original),refs.make(pid,'asset',table['id']),refs.make(pid,'file',original['id'])]
    task=tasks.enqueue(pid,'studio',{'action':'execute'})
    monkeypatch.setattr(execution_service,'settings',lambda pid:{'provider':'local-container'})
    received=[]
    def local(task,code,timeout,inputs,metadata):
        received.extend(inputs)
        envelope=json.loads(next(item['raw'] for item in inputs if item['name']=='products.json'))
        assert envelope['schema_version']=='paper-products.execution.v1'
        assert envelope['products'][0]['text']=='A real reusable output.'
        assert 'Twelve samples.' in envelope['products'][1]['text']
        assert envelope['products'][2]['spec']['rows']==[['A','12']]
        assert next(item['raw'] for item in inputs if item['name']==envelope['products'][3]['filename'])==security.safe_object(original['sha256']).read_bytes()
        image=io.BytesIO();Image.new('RGB',(10,10),'blue').save(image,format='PNG')
        return {'stdout':'12','exit_code':0,'artifacts':execution_service.collect(pid,[('result.png',image.getvalue())],metadata),'host_execution':False}
    monkeypatch.setattr(execution_service,'local',local)
    result=execution_service.execute(task,{'code':'print(12)','product_refs':sources,'file_ids':[original['id']]})
    assert result['stdout']=='12' and {(ref['type'],ref['id'],ref['version']) for ref in result['source_refs']}=={(ref['type'],ref['id'],ref['version']) for ref in sources}
    assert original['id']+'.txt' in [item['name'] for item in received]
    assert all('raw' not in item for item in result['input_manifest'])
    asset=db.one('SELECT metadata FROM artifacts WHERE id=?',(result['artifacts'][0]['artifact_id'],))
    assert json.loads(asset['metadata'])['source_refs']==result['source_refs']


def test_remote_execution_sends_real_products_and_rejects_changes_before_collect(client,monkeypatch):
    pid=project(client);memo=note(pid);source=refs.make(pid,'note',memo['id'])
    task=tasks.enqueue(pid,'studio',{'action':'execute'})
    monkeypatch.setattr(execution_service,'settings',lambda pid:{'provider':'remote-http','url':'https://synthetic.test'})
    monkeypatch.setattr(execution_service.store,'read_secret',lambda name:'synthetic')
    transmitted=[]
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            transmitted.append(kwargs['json'])
            envelope=json.loads(base64.b64decode(kwargs['json']['files'][0]['content_base64']))
            assert envelope['products'][0]['text']=='A real reusable output.'
            records.notes.save(pid,{**memo['data'],'text':'Changed during execution.'},memo['id'],expected_revision=memo['revision'])
            class Reply:
                def raise_for_status(self):pass
                def json(self):return {'stdout':'Completed remotely','exit_code':0,'images':[]}
            return Reply()
    monkeypatch.setattr(execution_service,'http_client',lambda *args,**kwargs:Client())
    with pytest.raises(HTTPException) as failed:execution_service.execute(task,{'code':'print(12)','product_refs':[source]})
    assert failed.value.status_code==409 and transmitted
    assert not db.rows('SELECT id FROM artifacts WHERE project_id=?',(pid,))


def test_remote_execution_blocks_local_only_derived_input_before_request(client,monkeypatch):
    pid=project(client);original=file(client,pid);source=summary(pid,original)
    derived=products.reuse(pid,{'sources':[source],'target':'asset'})['asset']
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    calls=[]
    monkeypatch.setattr(execution_service,'settings',lambda pid:{'provider':'remote-http','url':'https://synthetic.test'})
    monkeypatch.setattr(execution_service,'http_client',lambda *args,**kwargs:calls.append(True))
    task=tasks.enqueue(pid,'studio',{'action':'execute'})
    with pytest.raises(HTTPException) as error:execution_service.execute(task,{'code':'print(1)','product_refs':[refs.make(pid,'asset',derived['id'])]})
    assert error.value.status_code==403 and calls==[]
    assert product_execution.prepare(pid,{'product_refs':[source]},remote=False)[0]


def test_execution_input_selection_is_validated_before_creating_task(client,monkeypatch):
    pid=project(client);other=project(client,'Other');memo=note(other)
    monkeypatch.setattr(execution_service,'settings',lambda pid:{'provider':'local-container'})
    response=client.post(f'/api/v2/projects/{pid}/execution',json={'code':'print(1)','product_refs':[refs.make(other,'note',memo['id'])]})
    assert response.status_code in (404,422),response.text
    assert not db.rows("SELECT id FROM tasks WHERE project_id=? AND json_extract(payload,'$.action')='execute'",(pid,))


def test_execution_results_remain_dependent_on_their_input_versions(client,monkeypatch):
    pid=project(client);memo=note(pid);source=refs.make(pid,'note',memo['id'])
    body={'action':'execute','code':'print(1)','product_refs':[source]}
    task=tasks.enqueue(pid,'studio',body)
    monkeypatch.setattr(execution_service,'settings',lambda pid:{'provider':'local-container'})
    monkeypatch.setattr(execution_service,'local',lambda *args:{'stdout':'1','exit_code':0,'artifacts':[]})
    result=execution_service.execute(task,body)
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']))
    assert any(edge['relation']=='execution_input' and edge['id']==task['id'] for edge in product_relations.lookup(pid,source)['items'])
    from app.export_checks import freeze
    frozen=freeze(pid)
    execution=frozen['research_trace']['execution_runs'][0]
    assert execution['input']['product_refs']==[source] and execution['result']['input_manifest']
    assert 'code' not in execution['input']
    execution_ref=refs.make(pid,'execution',task['id'])
    assert products.content(pid,execution_ref)['text']
    records.notes.save(pid,{**memo['data'],'text':'New result'},memo['id'],expected_revision=1)
    with pytest.raises(HTTPException):products.content(pid,execution_ref)


@pytest.mark.parametrize('change',['add','remove','move','edit','disable'])
def test_writing_material_snapshot_tracks_the_collection_not_only_existing_sources(client,change):
    pid=project(client);sid=section(client,pid);memo=note(pid)
    material=products.reuse(pid,{'sources':[refs.make(pid,'note',memo['id'])],'target':'material','section_id':sid})['record']
    inputs,snapshot=section_materials.snapshot(pid,sid)
    assert inputs[0]['source_ref']['id']==material['id']
    section_materials.validate(pid,snapshot)
    if change=='add':
        records.notes.save(pid,{'title':'Extra','text':'New restriction','section_id':sid,'use_for_writing':True})
    elif change=='remove':records.remove(pid,material['id'],material['revision'])
    else:
        patch={'section_id':section(client,pid)} if change=='move' else {'text':'Modified'} if change=='edit' else {'use_for_writing':False}
        records.notes.save(pid,{**material['data'],**patch},material['id'],expected_revision=material['revision'])
    with pytest.raises(HTTPException) as error:section_materials.validate(pid,snapshot)
    assert error.value.status_code==409
    section_materials.validate(pid,None)


def test_empty_snapshot_rejects_newly_selected_material_and_preserves_other_chapters(client):
    pid=project(client);sid=section(client,pid);other=section(client,pid)
    _,snapshot=section_materials.snapshot(pid,sid)
    records.notes.save(pid,{'title':'Unrelated','text':'Other chapter','section_id':other,'use_for_writing':True})
    section_materials.validate(pid,snapshot)
    records.notes.save(pid,{'title':'Added later','text':'This chapter','section_id':sid,'use_for_writing':True})
    with pytest.raises(HTTPException):section_materials.validate(pid,snapshot)


def test_agent_candidate_rejects_material_added_after_generation(env,monkeypatch):
    pid,sid=env['pid'],env['sid'];fake_model(monkeypatch)
    result=finish(env,start(env));assert result['status']=='needs_review'
    records.notes.save(pid,{'title':'Late restriction','text':'An additional condition.','section_id':sid,'use_for_writing':True})
    response=env['client'].post(f'/api/v1/projects/{pid}/writing-candidates/{result["candidates"][-1]["id"]}/accept',json={'base_revision_id':None,'accept_policy':'draft','placement':'replace_section'},headers={'Idempotency-Key':'changed-selection'})
    assert response.status_code==409,response.text
    assert security.require_section(sid)['current_revision_id'] is None


def test_ordinary_candidate_rejects_material_added_after_generation(client,monkeypatch):
    pid=project(client);sid=section(client,pid);manual_package(client,pid)
    def generate(system,user,images=None):
        return {'text':db.dumps({'paragraphs':[{'text':'当前结果仍需实际验证。','source_ids':[],'reference_ids':[]}],'missing_materials':['测量记录']}),'usage':{'input_tokens':100,'output_tokens':50},'cost':0,'remote_id':'synthetic','request_hash':'synthetic'}
    monkeypatch.setattr(provider,'generate',generate)
    queued=client.post(f'/api/v1/sections/{sid}/writing-tasks',json={'base_revision_id':None,'operation':'generate'}).json()
    assert tasks.run_one()
    row=db.one('SELECT * FROM tasks WHERE id=?',(queued['id'],));assert row['status']=='succeeded',row['error']
    assert db.loads(row['result'])['manifest']['section_materials']['record_heads']==[]
    records.notes.save(pid,{'title':'New input','text':'A new required limitation.','section_id':sid,'use_for_writing':True})
    response=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':queued['id'],'base_revision_id':None})
    assert response.status_code==409,response.text
    assert security.require_section(sid)['current_revision_id'] is None


def test_material_panel_does_not_lose_old_inputs_behind_500_unrelated_notes(client):
    pid=project(client);sid=section(client,pid)
    expected=records.notes.save(pid,{'title':'Old but linked','text':'Keep this input','section_id':sid,'use_for_writing':True})
    with db.transaction():
        for index in range(505):records.notes.save(pid,{'title':f'Unrelated {index}','text':'Not a writing input'})
    response=client.get(f'/api/v1/projects/{pid}/sections/{sid}/materials')
    assert response.status_code==200
    assert [item['id'] for item in response.json()['products']]==[expected['id']]


def test_relations_show_sources_assets_current_prose_and_pending_insertions(client):
    from app.assistant_context import adopt
    from app import workspace_intents
    pid=project(client);sid=section(client,pid);base=save(client,sid,'Body').json();memo=note(pid)
    source=refs.make(pid,'note',memo['id'])
    child=products.reuse(pid,{'sources':[source],'target':'note'})['record']
    asset=products.reuse(pid,{'sources':[source],'target':'asset'})['asset']
    proposal=products.reuse(pid,{'sources':[source],'target':'draft','section_id':sid,'revision_id':base['id']})['record']
    saved=adopt(pid,proposal['id'],proposal['revision'])
    result=product_relations.lookup(pid,source)
    targets={(edge['type'],edge['id']) for edge in result['items'] if edge['direction']=='downstream'}
    assert {('note',child['id']),('asset',asset['id']),('revision',saved['id'])}<=targets
    parents=product_relations.lookup(pid,refs.make(pid,'note',child['id']))['items']
    assert any(edge['direction']=='upstream' and edge['id']==memo['id'] for edge in parents)
    image=io.BytesIO();Image.new('RGB',(10,10),'red').save(image,format='PNG')
    aid=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'Image','figure',security.put_blob(image.getvalue()),'{}',db.now()))
    workspace_intents.create(pid,{'section_id':sid,'artifact_id':aid})
    assert any(edge['relation']=='insertion_pending' for edge in product_relations.lookup(pid,refs.make(pid,'asset',aid))['items'])


def test_relations_ignore_old_record_links_are_bounded_and_reject_other_projects(client):
    pid=project(client);other=project(client,'Other');a=note(pid);b=note(pid,'Different')
    first=refs.make(pid,'note',a['id']);second=refs.make(pid,'note',b['id'])
    child=records.notes.save(pid,{'title':'Child','text':'A','source_refs':[first]})
    records.notes.save(pid,{'title':'Child','text':'B','source_refs':[second]},child['id'],expected_revision=child['revision'])
    assert not any(edge['id']==child['id'] for edge in product_relations.lookup(pid,first)['items'])
    for index in range(5):records.notes.save(pid,{'title':f'Child {index}','text':'Derived','source_refs':[first]})
    limited=product_relations.lookup(pid,first,2)
    assert len(limited['items'])==2 and limited['truncated']
    response=client.post(f'/api/v2/projects/{other}/products/relations',json={'source':first})
    assert response.status_code==422


def test_reuse_transport_rejects_invalid_fields_without_partial_side_effects(client):
    pid=project(client);source=refs.make(pid,'note',note(pid)['id'])
    before=len(db.rows('SELECT * FROM studio_records WHERE project_id=?',(pid,)))
    for body in ({'sources':[source],'target':'note','unexpected':'field'},{'sources':[source],'target':'unknown'},{'sources':[{'id':'wrong'}],'target':'note'}):
        response=client.post(f'/api/v2/projects/{pid}/products/reuse',json=body)
        assert response.status_code==422,response.text
    assert len(db.rows('SELECT * FROM studio_records WHERE project_id=?',(pid,)))==before


def test_empty_assets_are_not_materialized_as_fake_inputs(client):
    pid=project(client);aid=db.uid()
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'Metadata only','data',None,'{}',db.now()))
    with pytest.raises(HTTPException) as error:product_execution.prepare(pid,{'product_refs':[refs.make(pid,'asset',aid)]})
    assert error.value.status_code==422

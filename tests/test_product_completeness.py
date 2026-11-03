"""Functional coverage, rather than source-path presence, for every product adapter."""
import copy
import io
import json
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file,section,save,manual_package
from test_product_interop import summary
from test_graph_wiki import library,no_remote_models,build
from test_writing_agent import env,fake_model,finish,start
from app import db,documents,security,tasks,provider,knowledge_units,document_reading
from app import product_service as products,resource_refs as refs,research_records as records
from app import reading_service,parsing_service,assistant_service,asset_service,reference_service,export_service
from app.writing_agent import evidence,coordinator


@pytest.fixture
def outputs(client,monkeypatch):
    from app import model_install
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False})
    pid=project(client);original=file(client,pid,'Reusable original methods and explicit limitations.')
    knowledge_units.build(pid,original['id']);view=reading_service.document(pid,original['id']);unit=view['units'][0]
    sid=section(client,pid);base=save(client,sid,'Initial body.').json()
    source=db.one('SELECT id FROM sources WHERE file_id=?',(original['id'],))['id']
    note=records.notes.save(pid,{'title':'Reusable note','text':'A saved interpretation.'})
    book=reference_service.upsert_external(pid,{'title':'A registered reference','authors':['Test'],'year':'2026'},original['id'])
    annotation=reading_service.annotate(pid,original['id'],{'unit_id':unit['id'],'parse_id':view['parse_id'],'source_hash':unit['source_hash'],'start':0,'end':8,'quote':unit['text'][:8],'note':'Reading interpretation'})
    conversation=records.conversations.save(pid,{'title':'Saved conversation'})
    message=records.save(pid,'message',{'role':'assistant','text':'An unverified assistant response.'},owner=conversation['id'])
    translation=records.save(pid,'translation',{'text':'A translated method.','language':'en'},owner=original['id'])
    guide=records.save(pid,'guide',{'summary':'A grounded guide.','keypoints':[{'text':'Retain conditions.'}]},owner=original['id'])
    webpage=records.save(pid,'webpage',{'html':'<html><body>Interactive explanation.</body></html>','parse_id':view['parse_id']},owner=original['id'])
    canvas=records.canvases.save(pid,{'title':'Canvas output','scene':{'version':1,'elements':[]}})
    search=records.save(pid,'search',{'title':'Literature discovery','items':[{'title':'Discovered paper','doi':'10.test/reuse'}]})
    parse_task=tasks.enqueue(pid,'studio',{'action':'parse','file_id':original['id']})
    parsed=parsing_service.save_result(parse_task,original,'surya',{'blocks':[{'text':'A structured result.','type':'text','page':1}]},None)
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(parse_task['id'],))
    comment=assistant_service.add_comment(pid,{'section_id':sid,'revision_id':base['id'],'node_id':json.loads(base['document'])['content'][0]['attrs']['nodeId'],'quote':'Initial body.','text':'Retain the source limitations.','start':0,'end':13})
    fact=client.post(f'/api/v1/projects/{pid}/facts',json={'label':'Planned result','statement':'Not yet measured.'}).json()
    observation=evidence.sync_source(pid,source)
    asset=asset_service.create(pid,{'title':'A shared asset','text':'Shared method description.'})
    proposal=products.reuse(pid,{'sources':[refs.make(pid,'note',note['id'])],'target':'draft','section_id':sid,'revision_id':base['id']})['record']
    rule=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'Keep conditions.','modality':'mandatory'}).json()
    package=manual_package(client,pid)
    reading=document_reading.create(pid,{'file_id':original['id'],'section_id':sid,'question':'What limits the method?'})
    analysis=db.uid();db.execute('INSERT INTO advanced_runs VALUES(?,?,?,?,?,?)',(analysis,pid,'advanced_statistics',db.dumps({'file_id':original['id'],'method':'welch_t'}),db.dumps({'source_sha256':original['sha256'],'n':12}),db.now()))
    delivered=export_service.generate(pid,{'format':'markdown'})
    finding=db.uid();db.execute('INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(finding,pid,sid,base['id'],None,'warning','unknown','human','Verify the method boundary.','','open',db.now()))
    mentor=db.uid();db.execute('INSERT INTO mentor_tasks VALUES(?,?,?,?,?,?,?)',(mentor,pid,sid,'Review the limitations.','open','',db.now()))
    execution=tasks.enqueue(pid,'studio',{'action':'execute','file_ids':[original['id']]})
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps({'stdout':'12','exit_code':0,'artifacts':[]}),execution['id']))
    values={'file':original['id'],'summary':summary(pid,original)['id'],'reference':book['id'],'note':note['id'],'annotation':annotation['id'],'translation':translation['id'],'guide':guide['id'],'message':message['id'],'webpage':webpage['id'],'asset':asset['id'],'fact':fact['id'],'analysis':analysis,'revision':base['id'],'rule':rule['id'],'rule_package':package,'outline':sid,'finding':finding,'mentor_task':mentor,'reading':reading['id'],'evidence':observation['id'],'source':source,'unit':unit['id'],'canvas':canvas['id'],'parse_result':parsed['id'],'proposal':proposal['id'],'search':search['id'],'comment':comment['id'],'export':delivered['export_id'],'execution':execution['id']}
    return client,pid,sid,original,values


def test_every_non_graph_output_is_discoverable_previewable_and_reusable(outputs):
    client,pid,sid,original,values=outputs
    assert set(values)==set(products.LABELS)-{'wiki_page','assertion'}
    for kind,identity in values.items():
        item=products.item(pid,kind,identity)
        assert item['state']=='current',(kind,item)
        assert item['actions'] and item['open_location'],kind
        assert any(row['id']==identity for row in products.catalog(pid,kind=kind,limit=100)['items']),kind
        value=products.content(pid,item['source_ref'])
        assert value['text'].strip() and value['document'],kind
        reused=products.reuse(pid,{'sources':[item['source_ref']],'target':'note'})['record']
        assert reused['source_refs'] and reused['data']['document'],kind
        assert refs.validate(pid,reused['source_refs']),kind


def test_both_graph_outputs_reuse_the_published_content(library):
    client,pid,original,parsed=library;build(pid)
    for kind in ('wiki_page','assertion'):
        rows=products.catalog(pid,kind=kind)['items']
        assert rows and rows[0]['state']=='current'
        value=products.content(pid,rows[0]['source_ref'])
        assert value['text']
        note=products.reuse(pid,{'sources':[rows[0]['source_ref']],'target':'note'})['record']
        assert note['classification']=='personal_note' and refs.validate(pid,note['source_refs'])


def test_direct_lookup_is_not_limited_to_the_first_catalog_page(client):
    pid=project(client)
    old=records.notes.save(pid,{'title':'Old note','text':'Saved before many other notes.'})
    for i in range(45):records.notes.save(pid,{'title':f'New note {i}','text':'Current'})
    assert old['id'] not in {row['id'] for row in products.catalog(pid,kind='note',limit=30)['items']}
    result=client.get(f'/api/v2/projects/{pid}/products/note/{old["id"]}')
    assert result.status_code==200 and result.json()['data']['id']==old['id']


def test_empty_chapter_and_local_only_chapter_accept_reviewed_local_products(client):
    from app.assistant_context import adopt
    pid=project(client);sid=section(client,pid);original=file(client,pid)
    source=summary(pid,original)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    body={'sources':[source],'target':'draft','section_id':sid,'revision_id':None}
    proposal=products.reuse(pid,body)['record']
    assert security.require_section(sid)['current_revision_id'] is None
    first=adopt(pid,proposal['id'],proposal['revision'])
    assert first['review_state']=='unverified'
    second=products.reuse(pid,{**body,'revision_id':first['id']})['record']
    assert adopt(pid,second['id'],second['revision'])['id']!=first['id']
    with pytest.raises(HTTPException):products.content(pid,source,remote=True)


def test_rich_prose_reuse_preserves_citations_formula_tables_and_marks(client):
    from app.assistant_context import adopt
    pid=project(client);source_sid=section(client,pid);target=section(client,pid)
    book=reference_service.upsert_external(pid,{'title':'Real registered reference','authors':['Author']})
    document={'type':'doc','content':[{'type':'heading','attrs':{'level':2},'content':[{'type':'text','text':'Method'}]},
        {'type':'paragraph','content':[{'type':'text','text':'Conditions','marks':[{'type':'bold'}]},{'type':'citation','attrs':{'citationId':book['id']}}]},
        {'type':'formula','attrs':{'latex':'x^2+y^2'}},
        {'type':'table','attrs':{'tableStyle':'three-line','caption':'Counts','note':'Observed sample'},'content':[{'type':'tableRow','content':[{'type':'tableCell','content':[{'type':'paragraph','content':[{'type':'text','text':'12'}]}]}]}]}]}
    original=documents.save(security.require_section(source_sid),document,None)
    source=refs.make(pid,'revision',original['id'])
    note=products.reuse(pid,{'sources':[source],'target':'note'})['record']
    asset=products.reuse(pid,{'sources':[refs.make(pid,'note',note['id'])],'target':'asset'})['asset']
    candidate=products.reuse(pid,{'sources':[refs.make(pid,'asset',asset['id'])],'target':'draft','section_id':target,'revision_id':None})['record']
    adopted=adopt(pid,candidate['id'],candidate['revision'])
    copied=json.loads(adopted['document']);all_nodes=list(documents.nodes(copied))
    assert {'heading','citation','formula','table'}<={node['type'] for node in all_nodes}
    assert next(node for node in all_nodes if node['type']=='citation')['attrs']['citationId']==book['id']
    assert next(node for node in all_nodes if node['type']=='formula')['attrs']['latex']=='x^2+y^2'
    assert next(node for node in all_nodes if node['type']=='table')['attrs']['note']=='Observed sample'
    assert next(node for node in all_nodes if node.get('text')=='Conditions')['marks']==[{'type':'bold'}]
    assert db.one('SELECT id FROM citations WHERE revision_id=? AND reference_id=?',(adopted['id'],book['id']))
    assert documents.current(security.require_section(source_sid))['document']==original['document']


def test_plan_material_and_review_targets_update_their_actual_features(client):
    pid=project(client);sid=section(client,pid)
    note=records.notes.save(pid,{'title':'Limitations','text':'Only a synthetic sample was assessed.'})
    source=refs.make(pid,'note',note['id'])
    planned=products.reuse(pid,{'sources':[source],'target':'plan','section_id':sid})
    assert 'synthetic sample' in security.require_section(sid)['goal']
    assert products.reuse(pid,{'sources':[source],'target':'plan','section_id':sid})['record']['id']==planned['record']['id']
    inputs=products.section_inputs(pid,sid,remote=True)
    assert inputs and 'synthetic sample' in inputs[0]['text']
    materials=client.get(f'/api/v1/projects/{pid}/sections/{sid}/materials').json()
    assert materials['products'][0]['id']==planned['record']['id']
    reviewed=products.reuse(pid,{'sources':[source],'target':'review','section_id':sid})['record']
    task=db.one('SELECT * FROM mentor_tasks WHERE id=?',(reviewed['data']['mentor_task_id'],))
    assert task['status']=='open' and 'synthetic sample' in task['comment']
    assert products.item(pid,'mentor_task',task['id'])['state']=='current'


def test_regular_writer_receives_linked_products_and_tracks_adopted_sources(client,monkeypatch):
    pid=project(client);manual_package(client,pid);sid=section(client,pid)
    source=records.notes.save(pid,{'title':'Study restriction','text':'This method has no measured deployment result.'})
    material=products.reuse(pid,{'sources':[refs.make(pid,'note',source['id'])],'target':'material','section_id':sid})['record']
    calls=[]
    def generate(system,user,images=None):
        payload=json.loads(user);calls.append(payload)
        assert 'no measured deployment' in payload['research_products'][0]['text']
        return {'text':db.dumps({'paragraphs':[{'text':'本方法尚无实际部署测量结果。','source_ids':[],'reference_ids':[]}],'missing_materials':['真实测量']}),'usage':{'input_tokens':100,'output_tokens':40},'cost':0,'remote_id':'synthetic','request_hash':'synthetic'}
    monkeypatch.setattr(provider,'generate',generate)
    task=client.post(f'/api/v1/sections/{sid}/writing-tasks',json={'base_revision_id':None,'operation':'generate'}).json()
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],));assert saved['status']=='succeeded',saved['error']
    accepted=client.post(f'/api/v1/sections/{sid}/accept',json={'task_id':task['id'],'base_revision_id':None})
    assert accepted.status_code==200,accepted.text
    assert calls and db.one("SELECT id FROM dependencies WHERE revision_id=? AND upstream_type='knowledge:note' AND upstream_id=?",(accepted.json()['id'],material['id']))


def test_agent_pipeline_reads_selected_products_and_rejects_old_versions(env,monkeypatch):
    pid,sid=env['pid'],env['sid']
    source=records.notes.save(pid,{'title':'Boundary note','text':'Static code does not prove runtime behavior.'})
    material=products.reuse(pid,{'sources':[refs.make(pid,'note',source['id'])],'target':'material','section_id':sid})['record']
    calls=fake_model(monkeypatch);session=finish(env,start(env))
    assert session['status']=='needs_review',session
    assert any(value.get('research_products') or value.get('context',{}).get('research_products') for _,value in calls)
    candidate=session['candidates'][-1]['id']
    records.notes.save(pid,{**source['data'],'text':'The upstream interpretation changed.'},source['id'],expected_revision=source['revision'])
    result=env['client'].post(f'/api/v1/projects/{pid}/writing-candidates/{candidate}/accept',json={'base_revision_id':None,'accept_policy':'draft','placement':'replace_section'},headers={'Idempotency-Key':'stale-material'})
    assert result.status_code==409,result.text


def test_saved_conversation_restores_products_and_enforces_agent_capabilities(client,monkeypatch):
    pid=project(client);note=records.notes.save(pid,{'title':'Saved input','text':'Discussion material.'})
    source=refs.make(pid,'note',note['id']);conversation=records.conversations.save(pid,{'title':'Continue'})
    task=assistant_service.send_message(pid,conversation['id'],{'text':'Read','product_refs':[source],'use_knowledge':False})
    assert records.get(pid,conversation['id'])['data']['product_refs']==[source]
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(task['id'],))
    next_task=assistant_service.send_message(pid,conversation['id'],{'text':'Continue','use_knowledge':False})
    assert db.loads(next_task['payload'])['product_refs']==[source]
    with pytest.raises(HTTPException):assistant_service.require_products({'capabilities':['notes']},[{'type':'asset'}])


def test_all_new_source_types_survive_complete_bundle_validation(outputs):
    from app import knowledge_transfer
    from app.resource_registry import validate_relations
    client,pid,sid,original,values=outputs
    for kind in ('rule','rule_package','outline','finding','mentor_task','reading','export','execution'):
        products.reuse(pid,{'sources':[refs.make(pid,kind,values[kind])],'target':'note'})
    bundle=knowledge_transfer.export(pid)
    with open(bundle['path'],'rb') as stream:_,tables=knowledge_transfer.validate(stream.read())
    assert validate_relations(pid,tables)['relations_verified']


def test_rechecking_keeps_referenced_findings_and_invalidates_consumers(outputs):
    from app import reviews,knowledge_transfer
    client,pid,sid,original,values=outputs
    note=products.reuse(pid,{'sources':[refs.make(pid,'finding',values['finding'])],'target':'note'})['record']
    reviews.run(pid)
    assert db.one('SELECT status FROM findings WHERE id=?',(values['finding'],))['status']=='superseded'
    assert records.notes.get(pid,note['id'])['source_state']=='stale'
    assert values['finding'] not in {row['id'] for row in client.get(f'/api/v1/projects/{pid}/findings').json()}
    bundle=knowledge_transfer.export(pid)
    with open(bundle['path'],'rb') as stream:knowledge_transfer.validate(stream.read())


def test_local_only_content_cannot_escape_via_asset_evidence(client):
    pid=project(client);original=file(client,pid);source=summary(pid,original)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?",(original['id'],))
    asset=products.reuse(pid,{'sources':[source],'target':'asset'})['asset']
    observed=evidence.sync_artifact(pid,asset['id'])
    with pytest.raises(HTTPException):products.content(pid,refs.make(pid,'evidence',observed['id']),remote=True)


def test_webpage_reuse_does_not_require_optional_html_packages_or_include_scripts(client):
    pid=project(client);original=file(client,pid);parsed=knowledge_units.build(pid,original['id'])
    webpage=records.save(pid,'webpage',{'html':'<html><head><style>hidden css</style></head><body><p>Visible &amp; useful.</p><script>hidden instruction</script></body></html>','parse_id':parsed['id']},owner=original['id'])
    value=products.content(pid,refs.make(pid,'webpage',webpage['id']))
    assert value['text']=='Visible & useful.'


def test_unannounced_fact_origin_changes_still_invalidate_saved_refs(client):
    pid=project(client);original=file(client,pid)
    sid=db.one('SELECT id FROM sources WHERE file_id=?',(original['id'],))['id']
    fact=client.post(f'/api/v1/projects/{pid}/facts',json={'label':'Source-backed fact','statement':'Interpretation.','source_ids':[sid]}).json()
    source=refs.make(pid,'fact',fact['id'])
    note=products.reuse(pid,{'sources':[source],'target':'note'})['record']
    db.execute('UPDATE sources SET text=text||? WHERE id=?',(' Source was amended.',sid))
    assert records.notes.get(pid,note['id'])['source_state']=='stale'
    with pytest.raises(HTTPException):products.content(pid,source)


def test_existing_note_to_chapter_entry_uses_the_same_rich_local_draft_service(client):
    from app import research_workflows
    from app.assistant_context import adopt
    pid=project(client);sid=section(client,pid)
    note=records.notes.save(pid,{'title':'Rich local note','text':'Formula note','document':{'type':'doc','content':[{'type':'formula','attrs':{'latex':'a+b'}}]}})
    proposal=research_workflows.note_proposal(pid,note['id'],sid,None)
    saved=adopt(pid,proposal['id'],proposal['revision'])
    assert any(node['type']=='formula' for node in documents.nodes(json.loads(saved['document'])))


@pytest.mark.parametrize('kind',['screenshot','image'])
def test_actual_screenshot_and_image_outputs_can_be_inserted_and_served_as_images(client,kind):
    from PIL import Image
    from app import workspace_intents
    pid=project(client);sid=section(client,pid)
    base=save(client,sid,'Body').json()
    output=io.BytesIO();Image.new('RGB',(20,20),'green').save(output,format='PNG')
    aid=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'Actual visual',kind,security.put_blob(output.getvalue()),db.dumps({'egress':'local_only','capture_method':'actual browser'}),db.now()))
    ref=refs.make(pid,'asset',aid)
    result=products.reuse(pid,{'sources':[ref],'target':'figure','section_id':sid,'revision_id':base['id']})
    pending=workspace_intents.pending(pid,sid)[0]
    doc=json.loads(base['document']);doc['content'].append({'type':'figure','attrs':{'artifactId':aid,'assetVersionId':pending['asset_version_id'],'caption':'Actual visual'}})
    saved=documents.save(security.require_section(sid),doc,base['id'])
    assert workspace_intents.finish(pid,result['intent']['id'],{'revision_id':saved['id']})['state']=='consumed'
    response=client.get(f'/api/v1/projects/{pid}/studio/assets/{aid}')
    assert response.status_code==200 and response.headers['content-type']=='image/png'
    assert response.content==output.getvalue()

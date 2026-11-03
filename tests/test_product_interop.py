"""Cross-feature outputs must be consumed with their exact sources and versions."""
import base64
import io
import json
import pytest
from fastapi import HTTPException
from test_workbench import client, project, file, section, save
from app import db, documents, security, tasks, material_summary, research_records as records
from app import product_service as products, resource_refs as refs, asset_service, assistant_service


def summary(pid, original):
    fingerprint = material_summary.source(original['id'])[2]
    data = {'overview': 'This summary retains the measured conditions.', 'key_points': [{'text': 'Twelve samples.', 'pages': [1]}]}
    db.execute('INSERT INTO material_summaries VALUES(?,?,?,?,?,?,?)', (original['id'], fingerprint, 'synthetic', db.dumps(data), 'synthetic', None, db.now()))
    return refs.make(pid, 'summary', original['id'])


def test_catalog_search_filter_pagination_and_project_isolation(client):
    pid = project(client)
    other = project(client, 'Other')
    first = records.notes.save(pid, {'title': 'Reusable method', 'text': 'Needle in the full text'})
    records.notes.save(other, {'title': 'Private other project', 'text': 'Needle'})
    records.notes.save(pid, {'title': 'Another note', 'text': 'More'})
    result = client.get(f'/api/v2/projects/{pid}/products', params={'kind': 'note', 'query': 'Needle'}).json()['data']
    assert result['total'] == 1 and result['items'][0]['id'] == first['id']
    assert result['items'][0]['source_ref']['version']
    assert products.catalog(pid, kind='note', limit=1)['total'] == 2
    assert len(products.catalog(pid, kind='note', limit=1, offset=1)['items']) == 1
    assert client.get(f'/api/v2/projects/{pid}/products', params={'kind': 'unknown'}).status_code == 422


def test_summary_translation_analysis_and_fact_reuse_real_text(client):
    pid = project(client)
    original = file(client, pid)
    inputs = [summary(pid, original)]
    translation = records.save(pid, 'translation', {'language': 'en', 'text': 'Translated conditions.'}, owner=original['id'])
    inputs.append(refs.make(pid, 'translation', translation['id']))
    fid = db.uid()
    db.execute('INSERT INTO facts(id,project_id,label,statement,created_at,updated_at) VALUES(?,?,?,?,?,?)', (fid, pid, 'Method status', 'Implementation remains planned.', db.now(), db.now()))
    inputs.append(refs.make(pid, 'fact', fid))
    rid = db.uid()
    db.execute('INSERT INTO advanced_runs VALUES(?,?,?,?,?,?)', (rid, pid, 'advanced_statistics', db.dumps({'file_id': original['id'], 'method': 'welch_t'}), db.dumps({'source_sha256': original['sha256'], 'n': 12, 'p_value': .04}), db.now()))
    inputs.append(refs.make(pid, 'analysis', rid))
    response = client.post(f'/api/v2/projects/{pid}/products/reuse', json={'sources': inputs, 'target': 'note'})
    assert response.status_code == 200, response.text
    result = response.json()['data']['record']
    assert all(text in result['data']['text'] for text in ('Twelve samples.', 'Translated conditions.', 'planned', 'p_value'))
    assert len(result['source_refs']) == 4 and result['classification'] == 'personal_note'
    assert products.reuse(pid, {'sources': inputs, 'target': 'note'})['record']['id'] == result['id']
    assert products.catalog(pid, kind='analysis')['items'][0]['id'] == rid


def test_multiple_products_preview_then_adopt_into_section_with_provenance(client):
    from app.assistant_context import adopt
    pid = project(client)
    sid = section(client, pid)
    base = save(client, sid, 'Original saved paragraph.').json()
    notes = [records.notes.save(pid, {'title': name, 'text': text}) for name, text in [('A', 'One reusable result.'), ('B', 'Another reusable result.')]]
    body = {'sources': [refs.make(pid, 'note', note['id']) for note in notes], 'target': 'draft', 'section_id': sid, 'revision_id': base['id']}
    proposal = products.reuse(pid, body)['record']
    assert security.require_section(sid)['current_revision_id'] == base['id']
    assert products.reuse(pid, body)['record']['id'] == proposal['id']
    adopted = adopt(pid, proposal['id'], proposal['revision'])
    assert all(text in adopted['text'] for text in ('Original saved paragraph.', 'One reusable result.', 'Another reusable result.'))
    assert adopted['review_state'] == 'unverified'
    assert len(db.rows("SELECT id FROM dependencies WHERE revision_id=? AND upstream_type='knowledge:note'", (adopted['id'],))) >= 2
    records.notes.save(pid, {'title': 'A', 'text': 'Changed upstream result.'}, notes[0]['id'], expected_revision=1)
    assert security.require_section(sid)['stale']


def test_old_draft_or_changed_source_cannot_be_adopted(client):
    from app.assistant_context import adopt
    pid = project(client)
    sid = section(client, pid)
    base = save(client, sid, 'Base').json()
    note = records.notes.save(pid, {'title': 'Input', 'text': 'Old result'})
    body = {'sources': [refs.make(pid, 'note', note['id'])], 'target': 'draft', 'section_id': sid, 'revision_id': base['id']}
    proposal = products.reuse(pid, body)['record']
    records.notes.save(pid, {'title': 'Input', 'text': 'New result'}, note['id'], expected_revision=1)
    with pytest.raises(HTTPException):
        adopt(pid, proposal['id'], proposal['revision'])
    assert save(client, sid, 'User edit.', base['id']).status_code == 200
    with pytest.raises(HTTPException):
        products.reuse(pid, {**body, 'sources': [refs.make(pid, 'note', note['id'])]})


def test_chat_reads_selected_products_and_retains_sources(client, monkeypatch):
    pid = project(client)
    original = file(client, pid)
    source = summary(pid, original)
    conversation = records.conversations.save(pid, {'title': 'Discuss outputs'})
    seen = []
    def fake(task, system, user, manifest, images=None):
        payload = json.loads(user)
        seen.append(payload)
        assert 'Twelve samples.' in payload['research_products'][0]['text']
        assert payload['research_products'][0]['classification'] == 'unverified_reused_content'
        assert original['id'] in manifest['file_ids']
        assert source in manifest['resource_refs']
        return {'text': json.dumps({'text': 'Discussing the actual result.', 'citations': []}), 'run_id': 'synthetic'}
    monkeypatch.setattr(tasks, 'model_call', fake)
    result = tasks.dispatch(assistant_service.send_message(pid, conversation['id'], {'text': 'Explain the selected result.', 'product_refs': [source], 'use_knowledge': False}))
    assert seen and source in result['message']['source_refs']
    assert source in records.get(pid, result['message']['data']['reply_to'])['source_refs']


def test_local_only_source_cannot_escape_through_reused_note_asset_or_document(client):
    from app.writing_agent.provenance import revision_permissions
    from app.assistant_context import adopt, artifact
    pid = project(client)
    original = file(client, pid)
    source = summary(pid, original)
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
    note = products.reuse(pid, {'sources': [source], 'target': 'note'})['record']
    asset = products.reuse(pid, {'sources': [refs.make(pid, 'note', note['id'])], 'target': 'asset'})['asset']
    edited = asset_service.update(pid, asset['id'], {'title': 'Edited asset', 'text': 'Edited result', 'revision': asset['metadata']['revision']})
    assert edited['metadata']['source_refs'] == asset['metadata']['source_refs']
    with pytest.raises(HTTPException) as blocked:
        artifact(pid, edited['id'])
    assert blocked.value.status_code == 403
    sid = section(client, pid)
    base = save(client, sid, 'Local-only working text.').json()
    proposal = products.reuse(pid, {'sources': [refs.make(pid, 'note', note['id'])], 'target': 'draft', 'section_id': sid, 'revision_id': base['id']})['record']
    adopted = adopt(pid, proposal['id'], proposal['revision'])
    with pytest.raises(HTTPException):
        revision_permissions(pid, adopted['id'])
    conversation = records.conversations.save(pid, {'title': 'Blocked'})
    with pytest.raises(HTTPException):
        assistant_service.send_message(pid, conversation['id'], {'text': 'Read', 'product_refs': [source]})
    assert not db.rows("SELECT * FROM studio_records WHERE owner_id=? AND kind='message'", (conversation['id'],))


def test_summary_changes_mark_derived_content_stale_and_bundle_preserves_links(client):
    from app import knowledge_transfer
    pid = project(client)
    original = file(client, pid)
    source = summary(pid, original)
    note = products.reuse(pid, {'sources': [source], 'target': 'note'})['record']
    bundle = knowledge_transfer.export(pid)
    with open(bundle['path'], 'rb') as stream:
        _, tables = knowledge_transfer.validate(stream.read())
    from app.resource_registry import validate_relations
    assert validate_relations(pid, tables)['relations_verified']
    db.execute('UPDATE material_summaries SET data=? WHERE file_id=?', (db.dumps({'overview': 'Changed summary'}), original['id']))
    assert records.notes.get(pid, note['id'])['source_state'] == 'stale'
    with pytest.raises(HTTPException):
        products.reuse(pid, {'sources': [source], 'target': 'asset'})


def test_image_reuse_creates_durable_editor_intent(client):
    from PIL import Image
    pid = project(client)
    sid = section(client, pid)
    save(client, sid, 'Base')
    canvas = records.canvases.save(pid, {'title': 'Result diagram', 'scene': {'version': 1, 'elements': []}})
    image = io.BytesIO()
    Image.new('RGB', (20, 20), 'blue').save(image, format='PNG')
    asset = asset_service.publish_canvas(pid, canvas['id'], {'revision': 1, 'png': base64.b64encode(image.getvalue()).decode()})
    body = {'sources': [refs.make(pid, 'asset', asset['id'])], 'target': 'figure', 'section_id': sid}
    result = products.reuse(pid, body)
    assert result['intent']['state'] == 'pending'
    assert products.reuse(pid, body)['intent']['id'] == result['intent']['id']


def test_cross_project_stale_summary_and_input_limits_are_rejected(client):
    pid = project(client)
    other = project(client, 'Other')
    note = records.notes.save(other, {'title': 'Secret', 'text': 'Other content'})
    source = refs.make(other, 'note', note['id'])
    assert client.post(f'/api/v2/projects/{pid}/products/reuse', json={'sources': [source], 'target': 'note'}).status_code == 422
    with pytest.raises(HTTPException):
        products.reuse(pid, {'sources': [], 'target': 'note'})
    original = file(client, pid)
    summary(pid, original)
    db.execute('UPDATE pages SET text=? WHERE file_id=?', ('Changed original', original['id']))
    item = products.catalog(pid, kind='summary')['items'][0]
    assert item['state'] == 'stale' and item['source_ref'] is None


def test_original_file_reuse_does_not_change_its_selected_version(client):
    pid=project(client)
    original=file(client,pid,'Original content available before knowledge indexing.')
    source=refs.make(pid,'file',original['id'])
    result=products.reuse(pid,{'sources':[source],'target':'note'})['record']
    assert 'Original content available' in result['data']['text']
    assert result['source_state']=='current' and refs.validate(pid,[source])


def test_reused_fact_is_reported_in_existing_writing_link_and_invalidated_on_edit(client):
    from app.assistant_context import adopt
    pid=project(client)
    sid=section(client,pid)
    base=save(client,sid,'Body').json()
    fact=client.post(f'/api/v1/projects/{pid}/facts',json={'label':'Planned work','statement':'It remains planned.'}).json()
    proposal=products.reuse(pid,{'sources':[refs.make(pid,'fact',fact['id'])],'target':'draft','section_id':sid,'revision_id':base['id']})['record']
    adopt(pid,proposal['id'],proposal['revision'])
    link=client.get(f'/api/v2/projects/{pid}/writing/model-link')
    assert link.status_code==200,link.text
    linked=next(row for row in link.json()['data']['facts'] if row['id']==fact['id'])
    assert sid in {row['id'] for row in linked['used_by']}
    assert client.patch(f'/api/v1/facts/{fact["id"]}',json={'statement':'The method changed.'}).status_code==200
    assert security.require_section(sid)['stale']


def test_asset_source_chain_survives_edits_and_marks_downstream_note_stale(client):
    pid=project(client)
    original=file(client,pid)
    source=summary(pid,original)
    asset=products.reuse(pid,{'sources':[source],'target':'asset'})['asset']
    note=products.reuse(pid,{'sources':[refs.make(pid,'asset',asset['id'])],'target':'note'})['record']
    assert refs.source_files(pid,[refs.make(pid,'note',note['id'])])==[original['id']]
    assert refs.resolve(pid,refs.make(pid,'asset',asset['id']))['location']['file_id']==original['id']
    db.execute('UPDATE material_summaries SET data=? WHERE file_id=?',(db.dumps({'overview':'Updated result'}),original['id']))
    assert records.notes.get(pid,note['id'])['source_state']=='stale'
    assert products.catalog(pid,kind='asset')['items'][0]['state']=='stale'


def test_original_edits_invalidate_summary_asset_note_and_current_section(client):
    from app.assistant_context import adopt
    from app.knowledge_lineage import changed
    pid=project(client)
    original=file(client,pid)
    source=summary(pid,original)
    asset=products.reuse(pid,{'sources':[source],'target':'asset'})['asset']
    note=products.reuse(pid,{'sources':[refs.make(pid,'asset',asset['id'])],'target':'note'})['record']
    sid=section(client,pid)
    base=save(client,sid,'Base').json()
    proposal=products.reuse(pid,{'sources':[refs.make(pid,'note',note['id'])],'target':'draft','section_id':sid,'revision_id':base['id']})['record']
    adopt(pid,proposal['id'],proposal['revision'])
    db.execute('UPDATE pages SET text=? WHERE file_id=?',('Original has changed.',original['id']))
    changed(pid,'file',original['id'],'Original changed')
    assert records.notes.get(pid,note['id'])['source_state']=='stale'
    assert security.require_section(sid)['stale']


def test_native_table_reuse_keeps_editable_cells_and_pinned_asset_version(client):
    from app import paper_visuals,workspace_intents
    pid=project(client)
    sid=section(client,pid)
    base=save(client,sid,'Base').json()
    asset=paper_visuals.create_visual(pid,{'spec':{'kind':'three_line_table','title':'Reusable table','headers':['Group','Count'],'rows':[['A','12']], 'note':'Synthetic data'}})
    source=refs.make(pid,'asset',asset['id'])
    assert 'A | 12' in products.content(pid,source)['text']
    assert products.content(pid,source,remote=True)['text']
    intent=products.reuse(pid,{'sources':[source],'target':'figure','section_id':sid})['intent']
    db.execute('UPDATE artifacts SET name=? WHERE id=?',('Renamed table',asset['id']))
    asset_service.get(pid,asset['id'])
    pending=workspace_intents.pending(pid,sid)[0]
    assert pending['artifact']['name']=='Reusable table'
    node=pending['artifact']['node']
    assert node['type']=='table' and node['attrs']['tableStyle']=='three-line'
    assert '12' in documents.plain(node)
    document=json.loads(base['document']);document['content'].append(node)
    saved=documents.save(security.require_section(sid),document,base['id'])
    assert workspace_intents.finish(pid,intent['id'],{'revision_id':saved['id']})['state']=='consumed'

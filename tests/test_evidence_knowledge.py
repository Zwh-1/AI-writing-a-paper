import io
import pytest
from fastapi import HTTPException
from test_workbench import client, project, file, section, manual_package
from app import db, parsers, security, model_install, tasks, provider, documents
from app import knowledge_units as units, knowledge_service as service


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    original = model_install.status
    monkeypatch.setattr(model_install, 'status', lambda name: {**original(name), 'ready': False})


@pytest.fixture
def library(client):
    pid = project(client)
    original = file(client, pid, '系统验证方法仅适用于单用户环境。\n\n并发操作仍需额外验证，不得声称已经通过测试。\n\n样本量为12，不代表完整总体。')
    parse = units.build(pid, original['id'])
    return client, pid, original, parse


def approval(item, **extra):
    return {'expected_hash': item['content_hash'], 'state': 'reviewed', 'note': '已逐字对照原件及前后文', 'reviewer': '测试核对人', 'compared_original': True, 'acknowledged_issues': item['issues'], **extra}


def test_migration_is_versioned_and_original_source_ids_remain(library):
    c, pid, original, parsed = library
    before = db.rows('SELECT id,text FROM sources ORDER BY id')
    from app.writing_agent.migrations import CURRENT
    assert db.one('PRAGMA user_version')['user_version'] == CURRENT
    assert units.build(pid, original['id'])['id'] == parsed['id']
    db.init()
    assert db.rows('SELECT id,text FROM sources ORDER BY id') == before
    assert not db.rows('PRAGMA foreign_key_check')


def test_exact_text_offsets_and_alternative_boundaries(library):
    c, pid, original, parsed = library
    paragraphs = [units.get(pid, r['id']) for r in parsed['units']]
    assert len(paragraphs) == 3
    page = db.one('SELECT text FROM pages WHERE file_id=?', (original['id'],))['text']
    for item in paragraphs:
        assert page[item['locator']['char_start']:item['locator']['char_end']] == item['text']
        assert item['semantic_support'] == 'claim_specific_check_required'
    alternate = units.build(pid, original['id'], strategy='page', activate=False)
    assert len(alternate['units']) == 1
    assert units.get(pid, paragraphs[0]['id'])['current']
    units.activate_parse(pid, alternate['id'])
    assert not units.get(pid, paragraphs[0]['id'])['current']
    units.activate_parse(pid, parsed['id'])
    assert units.get(pid, paragraphs[0]['id'])['current']


def test_read_keeps_conditions_and_neighbours(library):
    c, pid, original, parsed = library
    result = units.read(pid, parsed['units'][0]['id'])
    assert '仅适用于单用户' in result['text'] and '并发操作仍需额外验证' in result['text']
    assert result['complete'] and not result['text_is_model_summary']


def test_budget_exhaustion_provides_no_truncated_evidence(client):
    pid = project(client); original = file(client, pid, '有条件的原文。' * 200)
    parsed = units.build(pid, original['id']); uid = parsed['units'][0]['id']
    result = units.read(pid, uid, max_chars=200)
    assert result['status'] == 'needs_narrower_range' and result['passages'] == [] and 'text' not in result
    partial = units.read(pid, uid, start=0, end=100, max_chars=200)
    assert not partial['complete'] and partial['scope'] == 'explicit_partial_range'
    assert partial['text'] == units.get(pid, uid)['text'][:100]


def test_review_requires_original_comparison_and_explicit_issue_resolution(library):
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']; item = units.get(pid, uid)
    with pytest.raises(HTTPException):
        units.review(pid, uid, approval(item, compared_original=False))
    with pytest.raises(HTTPException):
        units.review(pid, uid, approval(item, acknowledged_issues=[]))
    reviewed = units.review(pid, uid, approval(item))
    assert reviewed['review_state'] == 'reviewed'
    assert reviewed['semantic_support'] == 'claim_specific_check_required'


def test_disagreement_never_grants_approval_by_resolution_alone(library):
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']; item = units.get(pid, uid)
    units.review(pid, uid, approval(item))
    disputed = units.dispute(pid, uid, {'kind': 'boundary', 'note': '结论是否依赖下一段存在分歧', 'reviewer': '核对人'})
    assert disputed['review_state'] == 'pending'
    with pytest.raises(HTTPException):
        units.review(pid, uid, approval(item))
    resolved = units.resolve(pid, uid, disputed['disputes'][0]['id'], {'note': '已扩大到下一段保留并发限制', 'reviewer': '核对人'})
    assert resolved['review_state'] == 'pending'
    assert units.review(pid, uid, approval(item))['review_state'] == 'reviewed'


def test_claim_support_is_exact_quote_and_specific_claim(library):
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']; item = units.get(pid, uid)
    body = {'expected_hash': item['content_hash'], 'claim': '方法适用范围为单用户环境', 'quote': '仅适用于单用户环境', 'support': 'supported', 'conditions': ['单用户'], 'note': '原文限定范围一致', 'reviewer': '核对人'}
    with pytest.raises(HTTPException): units.check_claim(pid, uid, body)
    units.review(pid, uid, approval(item))
    result = units.check_claim(pid, uid, body)
    assert result['method'] == 'human' and result['applies_only_to_claim'] == body['claim']
    with pytest.raises(HTTPException): units.check_claim(pid, uid, {**body, 'quote': '已经通过并发测试'})


def test_correction_is_new_version_and_does_not_overwrite_original(library):
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']; item = units.get(pid, uid)
    before_sources = db.rows('SELECT * FROM sources ORDER BY id')
    changed = units.revise(pid, uid, {'expected_hash': item['content_hash'], 'text': '人工核对后的完整转录，仅适用于单用户环境。', 'note': '保留原页并纠正误识别', 'reviewer': '核对人'})
    assert changed['id'] != uid and changed['parent_id'] == uid and changed['review_state'] == 'pending'
    assert units.get(pid, uid)['text'] == item['text'] and not units.get(pid, uid)['current']
    assert db.rows('SELECT * FROM sources ORDER BY id') == before_sources


def test_project_and_permission_isolation(library):
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']
    other = project(c, '其他论文')
    with pytest.raises(HTTPException): units.get(other, uid)
    c.patch('/api/v1/files/' + original['id'], json={'egress': 'local_only'})
    assert service.search(pid, '单用户')['items']
    assert not service.search(pid, '单用户', remote=True)['items']
    with pytest.raises(HTTPException): units.read(pid, uid, remote=True)


def test_parent_revocation_invalidates_unit_evidence(library):
    from app.writing_agent import evidence
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']
    parent = file(c, pid, '父原件', role='example'); security.link_derivation(original['id'], parent['id'], '原件转录')
    item = evidence.sync_unit(pid, uid)
    assert evidence.allowed_remote(pid, item['id'])
    c.patch('/api/v1/files/' + parent['id'], json={'egress': 'local_only'})
    assert not evidence.allowed_remote(pid, item['id'])


def test_text_change_makes_parse_and_evidence_stale(library):
    from app.writing_agent import evidence
    c, pid, original, parsed = library; uid = parsed['units'][0]['id']
    observed = evidence.sync_unit(pid, uid)
    db.execute('UPDATE sources SET text=text||? WHERE file_id=?', ('修订', original['id']))
    assert not units.get(pid, uid)['current']
    assert not evidence.get(pid, observed['id'])['version_current']
    with pytest.raises(HTTPException): units.activate_parse(pid, parsed['id'])


def test_excluded_page_and_archived_original_leave_search(library):
    c, pid, original, parsed = library
    page = db.one('SELECT id FROM pages WHERE file_id=?', (original['id'],))
    c.patch('/api/v1/pages/' + page['id'], json={'review_state': 'not_applicable', 'review_note': '排除本页'})
    assert not service.search(pid, '单用户')['items']
    with pytest.raises(HTTPException): units.read(pid, parsed['units'][0]['id'], remote=True)


def test_reviewed_collection_does_not_include_pending_units(library):
    c, pid, original, parsed = library
    assert not service.search(pid, '单用户', collection='reviewed')['items']
    item = units.get(pid, parsed['units'][0]['id']); units.review(pid, item['id'], approval(item))
    assert service.search(pid, '单用户', collection='reviewed')['items']


def test_import_preview_is_editable_and_commit_idempotent(client):
    pid = project(client); prefix = f'/api/v1/projects/{pid}/evidence-knowledge/imports'
    response = client.post(prefix + '/preview', files=[('files', ('study.txt', '原始研究内容'.encode(), 'text/plain')), ('files', ('bad.exe', b'invalid', 'application/octet-stream'))])
    assert response.status_code == 200
    data = response.json(); assert data['items'][1]['error']
    assert not db.rows('SELECT * FROM files')
    body = {'items': [{'index': 0, 'include': True, 'metadata': {'title': '人工录入的真实题名', 'authors': ['作者']}}]}
    first = client.post(prefix + '/' + data['id'] + '/commit', json=body)
    assert first.status_code == 200, first.text
    assert client.post(prefix + '/' + data['id'] + '/commit', json=body).json() == first.json()
    assert len(db.rows('SELECT * FROM files')) == 1
    assert db.loads(db.one('SELECT metadata FROM bibliography')['metadata'])['title'] == '人工录入的真实题名'
    assert not db.rows('SELECT * FROM generation_runs')


def test_selection_and_citation_versions_are_saved(library):
    c, pid, original, parsed = library; sid = section(c, pid); uid = parsed['units'][0]['id']
    selected = c.put(f'/api/v1/projects/{pid}/evidence-knowledge/sections/{sid}/selection', json={'unit_ids': [uid]})
    assert selected.status_code == 200
    ref = c.post(f'/api/v1/projects/{pid}/references', json={'metadata': {'title': '真实文献'}, 'file_id': original['id']}).json()
    doc = {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': {'nodeId': 'p', 'knowledgeUnitIds': [uid]}, 'content': [{'type': 'text', 'text': '仅限单用户环境。'}, {'type': 'citation', 'attrs': {'nodeId': 'cite', 'citationId': ref['id']}}]}]}
    saved = documents.save(security.require_section(sid), doc, None)
    link = db.one('SELECT * FROM citation_knowledge_links')
    assert link['unit_id'] == uid and link['unit_hash'] == units.get(pid, uid)['content_hash']
    assert db.one('SELECT * FROM dependencies WHERE upstream_type=\'knowledge_unit\'')['upstream_id'] == uid
    assert c.get(f'/api/v1/projects/{pid}/evidence-knowledge/citations').json()[0]['units'][0]['id'] == uid


def test_docx_native_headings_and_whole_table(client):
    from docx import Document
    pid = project(client); doc = Document(); doc.add_heading('方法及限制', 1); doc.add_paragraph('仅适用于已确认的范围。')
    table = doc.add_table(rows=2, cols=2); table.cell(0, 0).text = '样本'; table.cell(0, 1).text = '数量'; table.cell(1, 0).text = '单用户'; table.cell(1, 1).text = '12'
    stream = io.BytesIO(); doc.save(stream)
    original, _ = parsers.register_file(pid, '方法.docx', stream.getvalue()); parsers.parse_file(original['id'])
    parsed = units.build(pid, original['id']); items = [units.get(pid, r['id']) for r in parsed['units']]
    table_unit = next(r for r in items if r['kind'] == 'table')
    assert all(v in table_unit['text'] for v in ('样本', '数量', '单用户', '12'))
    assert not table_unit['locator']['physical_page'] and table_unit['heading_path'] == ['方法及限制']


def test_pdf_exact_bounding_boxes_are_retained(client):
    from reportlab.pdfgen import canvas
    stream = io.BytesIO(); pdf = canvas.Canvas(stream); pdf.drawString(50, 700, 'Only single-user testing is documented.'); pdf.showPage(); pdf.drawString(50, 700, 'Concurrent use requires further validation.'); pdf.save()
    pid = project(client); original, _ = parsers.register_file(pid, 'study.pdf', stream.getvalue()); parsers.parse_file(original['id'], native_only=True)
    parsed = units.build(pid, original['id']); items = [units.get(pid, r['id']) for r in parsed['units']]
    assert {r['locator']['page'] for r in items} == {1, 2}
    assert all(r['locator']['physical_page'] for r in items)
    assert all(r['locator']['bbox'] and len(r['locator']['bbox']) == 4 for r in items)
    read = units.read(pid, items[0]['id'])
    assert 'further validation' in read['text']


def test_evaluation_reports_retrieval_separately_from_semantic_acceptance(library):
    c, pid, original, parsed = library; prefix = f'/api/v1/projects/{pid}/evidence-knowledge'
    uid = parsed['units'][0]['id']
    result = c.post(prefix + '/evaluations', json={'cases': [{'query': '单用户环境', 'expected_unit_ids': [uid]}]})
    assert result.status_code == 200, result.text
    value = result.json(); assert not value['human_acceptance'] and value['cases'][0]['human_judgment'] is None
    reviewed = c.post(prefix + '/evaluations/' + value['id'] + '/human-review', json={'note': '逐项对照', 'reviewer': '核对人', 'judgments': [{'support': 'partial', 'note': '尚需补充跨页限定'}]})
    assert reviewed.status_code == 200 and not reviewed.json()['human_acceptance']


def test_boundary_revision_preserves_complete_source_segments(library):
    c,pid,original,parsed=library
    item=units.get(pid,parsed['units'][0]['id'])
    revised=units.revise(pid,item['id'],{'boundary':True,'before':0,'after':1,'expected_hash':item['content_hash'],'reviewer':'核对人','note':'将条件与限制合并阅读并保留原始位置'})
    assert '仅适用于单用户' in revised['text'] and '不得声称已经通过测试' in revised['text']
    assert len(revised['locator']['segments'])==2
    assert units.get(pid,item['id'])['text']==item['text']
    assert len(db.rows('SELECT * FROM knowledge_unit_chunks WHERE unit_id=?',(revised['id'],)))>=1


def test_ordinary_writing_reads_units_and_keeps_versions(library,monkeypatch):
    c,pid,original,parsed=library;sid=section(c,pid);manual_package(c,pid)
    uid=parsed['units'][0]['id']
    c.put(f'/api/v1/projects/{pid}/evidence-knowledge/sections/{sid}/selection',json={'unit_ids':[uid]})
    ref=c.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'范围研究'},'file_id':original['id']}).json()
    cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
    seen={}
    def fake(system,user,images=None):
        seen.update(db.loads(user,{}))
        return {'text':db.dumps({'paragraphs':[{'text':'适用范围仅为单用户环境。','knowledge_unit_ids':[uid],'reference_ids':[ref['id']]}]}),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'}
    monkeypatch.setattr(provider,'generate',fake)
    task=tasks.enqueue(pid,'write',{'section_id':sid,'base_revision_id':None,'operation':'generate'})
    result=tasks.writing(task,db.loads(task['payload'],{}))
    assert '并发操作仍需额外验证' in '\n'.join(item['text'] for item in seen['knowledge_units'])
    assert result['manifest']['knowledge_unit_versions'][uid]==units.get(pid,uid)['content_hash']
    assert result['citation_proposals'][0]['knowledge_unit_ids']==[uid]
    documents.save(security.require_section(sid),result['document'],None,citation_proposals=result['citation_proposals'])
    assert db.one('SELECT * FROM citation_knowledge_links')['unit_id']==uid


def test_raw_preview_cannot_supply_unread_knowledge_unit(library,monkeypatch):
    c,pid,original,parsed=library;sid=section(c,pid);manual_package(c,pid)
    cfg=provider.config();cfg['model']='test';db.set_setting('provider',cfg)
    monkeypatch.setattr(provider,'generate',lambda *a,**k:{'text':db.dumps({'paragraphs':[{'text':'伪造依据','knowledge_unit_ids':['unread-unit']}]}),'usage':{},'cost':0,'remote_id':'fixture','request_hash':'fixture'})
    task=tasks.enqueue(pid,'write',{'section_id':sid,'base_revision_id':None,'operation':'generate'})
    with pytest.raises(ValueError,match='未读取'):tasks.writing(task,db.loads(task['payload'],{}))


def test_human_citation_review_requires_current_exact_claim(library):
    c,pid,original,parsed=library;sid=section(c,pid);item=units.get(pid,parsed['units'][0]['id']);uid=item['id']
    units.review(pid,uid,approval(item))
    ref=c.post(f'/api/v1/projects/{pid}/references',json={'metadata':{'title':'范围研究'},'file_id':original['id']}).json()
    doc={'type':'doc','content':[{'type':'paragraph','attrs':{'nodeId':'p','knowledgeUnitIds':[uid]},'content':[{'type':'text','text':'只适用于单用户。'},{'type':'citation','attrs':{'nodeId':'c','citationId':ref['id']}}]}]}
    saved=documents.save(security.require_section(sid),doc,None)
    prefix=f'/api/v1/projects/{pid}/evidence-knowledge'
    cite=c.get(prefix+'/citations').json()[0]
    body={'expected_revision_id':saved['id'],'claim':cite['claim'],'support':'supported','reviewer':'核对人','note':'论断与原文范围一致','checks':[{'unit_id':uid,'expected_hash':item['content_hash'],'quote':'仅适用于单用户环境','conditions':['单用户']}]}
    assert c.post(prefix+'/citations/'+cite['id']+'/human-review',json=body).status_code==200
    assert db.one('SELECT support FROM citations')['support']=='supported'
    assert c.post(prefix+'/citations/'+cite['id']+'/human-review',json={**body,'claim':'支持多用户'}).status_code==409


def test_child_index_windows_do_not_replace_parent_evidence(client):
    pid=project(client);text='前置限定仅适用于单用户。'+('这里是完整实验背景。'*100)+'结论仍然不能推广到多用户。'
    original=file(client,pid,text);parsed=units.build(pid,original['id']);uid=parsed['units'][0]['id']
    chunks=db.rows('SELECT * FROM knowledge_unit_chunks WHERE unit_id=? ORDER BY char_start',(uid,))
    assert len(chunks)>1 and all(len(row['text'])<=240 for row in chunks)
    assert units.read(pid,uid,before=0,after=0)['text']==text


def test_failed_structural_file_is_reported_without_dropping_successes(library):
    c,pid,original,parsed=library
    bad,_=parsers.register_file(pid,'not-ready.txt',b'not parsed')
    result=service.build_project(pid,{},None)
    assert any(r['file_id']==original['id'] for r in result['files'])
    assert any(r['file_id']==bad['id'] for r in result['failures'])

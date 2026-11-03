from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_workbench import client, project, section, file, manual_package
from app import db, document_reading as reader, knowledge_units as units, model_install
from app import writing_retrieval as service, research_records


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    old = model_install.status
    monkeypatch.setattr(model_install, 'status', lambda name: {**old(name), 'ready': False})


def paper(client):
    pid = project(client); sid = section(client, pid)
    return pid, sid


def test_empty_reading_does_not_hide_new_literature(client):
    from app.document_preparation import prepare
    pid, sid = paper(client)
    old = file(client, pid, '旧文献讨论不同问题。')
    reader.create(pid, {'file_id': old['id'], 'section_id': sid, 'question': '旧问题'})
    new = file(client, pid, '新的验证范围。\n\n方法是案例分析。\n\n限制仅为模拟数据。')
    units.build(pid, new['id'])
    result = prepare(pid, '新的验证范围', sid, findings={'items': [{'file_id': new['id'], 'name': new['name']}]})
    assert new['id'] in {u['file_id'] for u in result['knowledge_units']}
    assert '模拟数据' in ''.join(u['text'] for u in result['knowledge_units'])
    assert any('尚未实际读取' in gap['reason'] for gap in result['gaps'])


def test_stale_reading_blocks_only_its_original(client):
    from app.document_preparation import prepare
    pid, sid = paper(client)
    old = file(client, pid, '旧论据验证范围。'); opened = reader.create(pid, {'file_id': old['id'], 'section_id': sid, 'question': '验证范围'})
    reader.read_range(pid, opened['id'], {'ordinal_start': 1, 'ordinal_end': 1})
    db.execute('UPDATE sections SET goal=? WHERE id=?', ('新任务', sid))
    new = file(client, pid, '新论据验证范围。'); units.build(pid, new['id'])
    result = prepare(pid, '验证范围', sid, findings={'items': [{'file_id': x['id'], 'name': x['name']} for x in (old, new)]})
    assert {u['file_id'] for u in result['knowledge_units']} == {new['id']}
    assert any('未用检索片段替代' in gap['reason'] for gap in result['gaps'])


def test_guidance_empty_reading_does_not_hide_current_rules_material(client):
    from app.guidance_preparation import prepare
    pid, sid = paper(client)
    old = file(client, pid, '旧教学材料。', role='example')
    reader.create(pid, {'file_id': old['id'], 'section_id': sid, 'question': '旧问题', 'intent': 'guidance'})
    new = file(client, pid, '论证要求。\n\n错误示例：直接推出结论。\n\n正确示例：先列证据和条件。', role='rules')
    result = prepare(pid, sid, '论证要求')
    assert new['id'] in result['file_ids']
    text = ''.join(p['text'] for e in result['examples'] if e.get('file_id') == new['id'] for p in e['passages'])
    assert '错误示例' in text and '正确示例' in text


def test_prepare_reads_literature_and_constraints_without_repository(client):
    pid, sid = paper(client); manual_package(client, pid)
    original = file(client, pid, '验证研究。\n\n方法采用样例。\n\n条件为单用户。')
    units.build(pid, original['id'])
    book = client.post(f'/api/v1/projects/{pid}/references', json={'file_id': original['id'], 'metadata': {'title': '验证研究'}}).json()
    bundle = service.prepare(pid, sid, '验证研究', channels=['literature', 'rules'])
    assert bundle['schema'] == service.SCHEMA and bundle['constraints'] and bundle['primary_evidence']
    assert book['id'] in bundle['compatibility']['reference_ids']
    assert service.validate(pid, bundle)
    patch = service.for_writing(bundle)
    assert patch['knowledge_units'] and patch['references'][0]['metadata']['title'] == '验证研究'
    assert not db.rows('SELECT * FROM generation_runs')


def test_search_preview_is_not_read_evidence_and_ignores_forged_text(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '论证笔记', 'text': '作者仍需核对的论证素材'})
    result = service.search(pid, sid, '论证笔记', channels=['products'])
    assert result['items'] and result['items'][0]['read_scope'] == 'search_preview_not_evidence'
    candidate = {**result['items'][0], 'text': '客户端伪造内容'}
    bundle = service.read(pid, sid, [candidate], '论证笔记')
    assert bundle['derived_context'][0]['text'] == '作者仍需核对的论证素材'
    assert not bundle['primary_evidence']
    assert note['id'] in {r['source_ref']['id'] for r in service.for_writing(bundle)['research_products']}
    assert '客户端伪造' not in db.dumps(bundle)


def test_local_only_notes_are_not_discovered_or_read(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '私密笔记', 'text': 'local secret', 'egress': 'local_only'})
    assert not service.search(pid, sid, '私密笔记', channels=['products'])['items']
    from app.resource_refs import make
    with pytest.raises(HTTPException) as exc:service.read(pid, sid, [make(pid, 'note', note['id'])], '私密笔记')
    assert exc.value.status_code == 403


def test_bundle_detects_tampering_and_permission_revocation(client):
    pid, sid = paper(client)
    original = file(client, pid, '权限核验原文。'); parsed = units.build(pid, original['id'])
    from app.resource_refs import make
    bundle = service.read(pid, sid, [make(pid, 'unit', parsed['units'][0]['id'])], '权限核验')
    altered = deepcopy(bundle); altered['primary_evidence'][0]['text'] = '篡改'
    with pytest.raises(HTTPException, match=''):service.validate(pid, altered)
    from app.resource_refs import digest
    altered['hash'] = digest({k: v for k, v in altered.items() if k != 'hash'})
    with pytest.raises(HTTPException):service.validate(pid, altered)
    with pytest.raises(HTTPException):service.restrict(altered, {})
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
    with pytest.raises(HTTPException) as exc:service.validate(pid, bundle)
    assert exc.value.status_code == 403


def test_bundle_rejects_changed_note_and_cross_project(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '版本笔记', 'text': '第一版'})
    from app.resource_refs import make
    bundle = service.read(pid, sid, [make(pid, 'note', note['id'])], '版本笔记')
    research_records.save(pid, 'note', {'title': '版本笔记', 'text': '第二版'}, note['id'], expected_revision=note['revision'])
    with pytest.raises(HTTPException):service.validate(pid, bundle)
    other = project(client, '其他项目')
    with pytest.raises(HTTPException):service.validate(other, bundle)
    with pytest.raises(HTTPException):service.search(other, sid, '问题')


def test_oversized_read_defers_whole_resource(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '长笔记', 'text': '不能截断。' * 200})
    from app.resource_refs import make
    bundle = service.read(pid, sid, [make(pid, 'note', note['id'])], '长笔记', max_chars=200)
    assert not bundle['derived_context'] and bundle['gaps'] and not bundle['complete']


def test_restrict_and_usage_match_actual_context(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '可用笔记', 'text': '正文可以参考但仍需核对。'})
    from app.resource_refs import make
    ref = make(pid, 'note', note['id']); bundle = service.read(pid, sid, [ref], '可用笔记')
    compact = service.for_model(bundle)
    assert 'text' not in compact['derived_context'][0] and 'context_patch' not in compact
    retained = service.restrict(bundle, service.for_writing(bundle))
    assert retained['derived_context'] and service.validate(pid, retained)
    used = service.attach_usage(retained, resource_refs=[ref])
    assert used['usage']['resource_refs'] == [ref]
    assert any(t['actual_use'] for t in used['retrieval_trace'])
    omitted = service.restrict(bundle, {})
    assert not omitted['derived_context'] and omitted['gaps']
    with pytest.raises(HTTPException):service.attach_usage(omitted, resource_refs=[ref])


def test_direct_guidance_read_preserves_teaching_context(client):
    pid, sid = paper(client)
    original = file(client, pid, '摘要教学。\n\n错误示例：计划完成。\n\n正确示例：已完成验证。\n\n仅为示例而非本项目结果。', role='example')
    parsed = units.build(pid, original['id'], allow_material=True)
    from app.resource_refs import make
    bundle = service.read(pid, sid, [make(pid, 'unit', parsed['units'][2]['id'])], '正确示例')
    assert not bundle['primary_evidence']
    text = bundle['guidance'][0]['text']
    assert all(value in text for value in ('摘要教学', '错误示例', '正确示例', '非本项目结果'))
    assert service.for_writing(bundle)['writing_examples'][0]['passages'][0]['text'] == text


def test_explicit_source_selection_is_read_even_without_query_match(client):
    pid, sid = paper(client)
    original = file(client, pid, '作者选定的完整限定条件。')
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))
    from app.resource_refs import make
    bundle = service.prepare(pid, sid, '与关键词无关的问题', source_refs=[make(pid, 'source', source['id'])])
    assert source['id'] in bundle['compatibility']['source_ids']
    assert bundle['primary_evidence'][0]['text'] == '作者选定的完整限定条件。'


def test_combine_keeps_earlier_sources_traces_usage_and_deduplicates(client):
    pid, sid = paper(client)
    first = research_records.save(pid, 'note', {'title': '最初笔记', 'text': '先说明适用条件。'})
    second = research_records.save(pid, 'note', {'title': '补读笔记', 'text': '再说明不能推出的结论。'})
    from app.resource_refs import make
    one = service.read(pid, sid, [make(pid, 'note', first['id'])], '适用条件')
    one = service.attach_usage(one, resource_refs=[one['derived_context'][0]['source_ref']])
    two = service.read(pid, sid, [make(pid, 'note', second['id'])], '补充局限')
    result = service.combine(pid, sid, [one, two, one], '合并论证依据')
    assert {row['id'] for row in result['derived_context']} == {first['id'], second['id']}
    assert len(result['context_patch']['research_products']) == 2
    assert len(result['retrieval_trace']) == 2
    assert result['usage']['recorded'] and result['usage']['resource_refs'] == one['usage']['resource_refs']
    assert service.validate(pid, result)


def test_combine_rejects_stale_version_and_foreign_section(client):
    pid, sid = paper(client)
    note = research_records.save(pid, 'note', {'title': '变更笔记', 'text': '旧版'})
    from app.resource_refs import make
    old = service.read(pid, sid, [make(pid, 'note', note['id'])], '旧版说明')
    research_records.save(pid, 'note', {'title': '变更笔记', 'text': '新版'}, note['id'], expected_revision=note['revision'])
    new = service.read(pid, sid, [make(pid, 'note', note['id'])], '新版说明')
    with pytest.raises(HTTPException):service.combine(pid, sid, [old, new], '不允许混合版本')
    other = section(client, pid)
    with pytest.raises(HTTPException):service.combine(pid, other, [new], '不允许混合章节')

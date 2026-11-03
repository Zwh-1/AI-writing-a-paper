"""Regression for full parsed units surviving packing and ordinary/paper writes."""
from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, write  # noqa: F401
from app import db, knowledge_units, security, tasks, writing_flow


def assert_unit_versions(result, parsed):
    expected = {unit['id']: unit['content_hash'] for unit in parsed['units']}
    actual = result['manifest']['knowledge_unit_versions']
    assert actual and all(actual[uid] == version for uid, version in expected.items())
    parse_id = db.one('SELECT parse_id FROM knowledge_units WHERE id=?', (parsed['units'][0]['id'],))['parse_id']
    assert all(scope['parse_id'] == parse_id for scope in result['manifest']['knowledge_read_scopes'].values())
    assert result['evidence_bundle']['primary_evidence']
    assert result['document']['content'][0]['attrs']['claimEvidence']


def test_packed_structured_units_keep_authoritative_manifest_versions(writing_project):
    e = writing_project
    parsed = knowledge_units.build(e['pid'], e['file']['id'])
    _, result = write(e)
    assert_unit_versions(result, parsed)
    draft_inputs = [payload for system, payload in e['sent'] if 'claim_reviews' not in system]
    assert draft_inputs[0]['knowledge_units']
    assert 'unit_hash' not in draft_inputs[0]['knowledge_units'][0]
    assert 'parse_id' not in draft_inputs[0]['knowledge_units'][0]
    assert result['writing_quality']['semantic']['claims'][0]['support'] == 'supported'


def test_whole_paper_with_real_structured_units_saves_source_mapping(writing_project):
    e = writing_project
    parsed = knowledge_units.build(e['pid'], e['file']['id'])
    writing_flow.save_settings(e['pid'], {'max_revision_rounds': 0})
    preview = e['client'].get(f'/api/v1/projects/{e["pid"]}/paper-writing/preflight').json()
    reply = e['client'].post(f'/api/v1/projects/{e["pid"]}/paper-writing', json={
        'instruction': '说明模块职责与研究材料的适用条件。', 'budget_limit': 10,
        'outline_revision': preview['outline_revision'], 'section_versions': preview['section_versions']},
        headers={'Idempotency-Key': 'structured-whole-paper-regression'})
    assert reply.status_code == 200, reply.text
    assert tasks.run_one()
    stored = db.one('SELECT * FROM tasks WHERE id=?', (reply.json()['id'],))
    assert stored['status'] == 'succeeded', stored['error']
    result = db.loads(stored['result'])
    chapter = result['paper_sections'][0]
    assert_unit_versions(chapter, parsed)
    citation = next(child for node in chapter['document']['content'] for child in node.get('content', []) if child.get('type') == 'citation')
    assert citation['attrs']['citationId'] == e['ref']['id']
    assert chapter['evidence_bundle']['usage']['recorded']

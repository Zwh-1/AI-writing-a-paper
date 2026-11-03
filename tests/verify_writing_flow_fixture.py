"""Exercise the disposable writing fixture through the UI's real HTTP contract."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fixture_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.runtime/writing-flow-ui-acceptance/fixture.json'
fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
import httpx

origin = 'http://127.0.0.1:' + str(fixture['port'])
pid, sid = fixture['project_id'], fixture['section_id']
checks = []


def unwrap(value):
    if value.get('meta', {}).get('api_version') == 'v2':value = value['data']
    if isinstance(value, dict) and value.get('task'):value = value['task']
    return value


with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
    reply = client.post('/api/v1/session', json={'token': fixture['url'].split('token=', 1)[1]})
    reply.raise_for_status();client.headers['X-CSRF-Token'] = reply.json()['csrf']

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        assert response.status_code == 200, (path, response.status_code, response.text)
        return unwrap(response.json())

    base = f'/api/v2/projects/{pid}/writing'
    settings = request('GET', base + '/flow/settings')
    settings['unit_target_words'] = 900
    settings['context_mode'] = 'argument_scoped'
    updated = request('PUT', base + '/flow/settings', json=settings)
    assert updated['unit_target_words'] == 900 and updated['context_mode'] == 'argument_scoped'
    request('PUT', base + '/flow/settings', json={'context_mode': 'full_manuscript'})
    checks.append('writing_settings_load_save_and_context_mode')

    plan = request('GET', base + '/argument-plan', params={'section_id': sid})
    assert plan['paragraph_cards']
    for iteration in (1, 2):
        plan['paragraph_cards'][0]['question'] = f'第{iteration}次核对：合成案例有哪些条件？'
        plan = request('PUT', base + '/argument-plan', params={'section_id': sid},
                       json={'base_hash': plan['hash'], 'paragraph_cards': plan['paragraph_cards'], 'author_note': '只验证合成素材，不代表真实论文质量。'})
    assert request('GET', base + '/argument-plan', params={'section_id': sid})['paragraph_cards'][0]['question'].startswith('第2次')
    checks.append('argument_plan_two_consecutive_edits_and_reload')

    found = request('POST', base + '/retrieval/search', json={'section_id': sid, 'query': '合成案例', 'limit': 8})
    assert found['items'] and all(item['read_scope'] == 'search_preview_not_evidence' for item in found['items'])
    bundle = request('POST', base + '/retrieval/read', json={'section_id': sid, 'query': '合成案例适用范围', 'candidates': found['items'][:2]})
    assert request('POST', base + '/retrieval/validate', json={'bundle': bundle})['valid']
    checks.append('discovery_actual_read_and_frozen_bundle_validation')

    preview = request('GET', f'/api/v1/projects/{pid}/paper-writing/preflight')
    assert preview['can_start'], preview['blockers']
    task = request('POST', f'/api/v1/projects/{pid}/paper-writing', headers={'Idempotency-Key': 'synthetic-writing-ui-acceptance'},
                   json={'instruction': '据合成案例说明研究问题、原文依据与局限，保留引用与待补范围。', 'budget_limit': 20,
                         'outline_revision': preview['outline_revision'], 'section_versions': preview['section_versions']})
    deadline = time.monotonic() + 120
    while task['status'] in ('queued', 'running') and time.monotonic() < deadline:
        time.sleep(.2);task = request('GET', '/api/v1/tasks/' + task['id'])
    assert task['status'] == 'succeeded', {key: task.get(key) for key in ('id', 'status', 'phase', 'error')}
    result = task['result'];assert result['complete'] and len(result['paper_sections']) == 2
    for chapter in result['paper_sections']:
        assert chapter['evidence_bundle'] and chapter['writing_quality'] and chapter['argument_plan']
        assert chapter['document']['content']
        assert any(child.get('type') == 'citation' for node in chapter['document']['content'] for child in node.get('content', []))
    quality = request('GET', base + '/quality/' + task['id'], params={'section_id': sid})
    assert len(quality['sections']) == 1 and quality['sections'][0]['writing_quality']
    checks.append('whole_paper_generation_preserves_sources_plan_quality_and_context_scope')

    (fixture_path.parent / 'task-result.json').write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
    calls = json.loads((fixture_path.parent / 'calls.json').read_text(encoding='utf-8'))
    tool_names = [row.get('action') for row in calls if row['kind'] == 'tool_model']
    assert 'search_writing_materials' in tool_names and 'read_writing_materials' in tool_names
    assert any(row['kind'] == 'semantic_model' for row in calls)
    assert any(row['kind'] == 'repair_model' for row in calls)
    checks.append('bounded_runtime_search_read_semantic_check_and_revision')
    report = {'checks': checks, 'passed': len(checks), 'project_id': pid, 'task_id': task['id'],
              'model_requests': len(calls), 'external_model_requests': 0,
              'scope': 'Real local HTTP and synthetic model responses; no browser was available for click/visual acceptance.'}
    (fixture_path.parent / 'http-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))

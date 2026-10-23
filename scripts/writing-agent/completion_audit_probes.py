"""Reproduce completion gaps in isolated fixtures; passing means gap reproduced.

This is an audit, not a product acceptance test. No live provider or user DB is used.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / '.runtime/python-deps', ROOT / '.runtime/advanced-deps', ROOT / 'backend', ROOT / 'tests'):
    sys.path.insert(0, str(folder))

import pytest
from test_writing_agent import env
from app import db, documents, tasks
import app.main as main
from app.writing_agent import impact

OBSERVATIONS = {}


def test_reproduce_legacy_adoption_gap(env):
    pid, sid = env['pid'], env['sid']
    task = tasks.enqueue(pid, 'write', {'section_id': sid})
    output = {
        'section_id': sid,
        'document': documents.text_document('这是隔离审计中的旧版候选，并无新版逐句核验。'),
        'manifest': {'rule_package_id': db.one('SELECT rule_package_id FROM projects WHERE id=?', (pid,))['rule_package_id']},
    }
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (db.dumps(output), task['id']))
    db.execute("UPDATE sections SET stale=1,stale_reason='未处理的来源变化' WHERE id=?", (sid,))
    body = {'task_id': task['id'], 'base_revision_id': None, 'accept_policy': 'strict', 'idempotency_key': 'audit-legacy-replay'}
    first = main.accept(sid, body)
    second = main.accept(sid, {**body, 'base_revision_id': first['id']})
    section = db.one('SELECT stale FROM sections WHERE id=?', (sid,))
    observation = {
        'legacy_request_with_strict_policy_is_accepted_without_new_analysis': bool(first),
        'new_adoption_receipts': db.one('SELECT COUNT(*) n FROM writing_adoptions WHERE project_id=?', (pid,))['n'],
        'new_document_analyses': db.one('SELECT COUNT(*) n FROM document_analyses WHERE project_id=?', (pid,))['n'],
        'same_legacy_candidate_can_create_another_revision': first['id'] != second['id'],
        'prior_stale_reason_cleared': section['stale'] == 0,
        'interpretation': 'Legacy API has no strict-policy/idempotency contract and does not adapt to the new adoption service.',
    }
    OBSERVATIONS['W16_legacy_path'] = observation
    assert observation['new_adoption_receipts'] == 0
    assert observation['new_document_analyses'] == 0
    assert observation['same_legacy_candidate_can_create_another_revision']
    assert observation['prior_stale_reason_cleared']


def test_reproduce_conservative_invalidation_gap(env):
    pid = env['pid']
    sid = env['client'].post(f'/api/v1/projects/{pid}/sections', json={'title': '无显式关联的隔离章节', 'kind': 'system_design'}).json()['id']
    documents.save(db.one('SELECT * FROM sections WHERE id=?', (sid,)), documents.text_document('本章未声明使用被变更的代码证据。'), None)
    before = db.one('SELECT stale FROM sections WHERE id=?', (sid,))['stale']
    impact.changed(pid, 'evidence', env['eid'], '隔离审计证据变化')
    after = db.one('SELECT stale FROM sections WHERE id=?', (sid,))['stale']
    OBSERVATIONS['W18_invalidation'] = {
        'unrelated_section_stale_before': bool(before),
        'unrelated_section_stale_after': bool(after),
        'strategy': 'conservative_project_invalidation',
        'interpretation': 'Conservative safety fallback exists; precise version-edge propagation is not implemented.',
    }
    assert before == 0 and after == 1


class AuditReporter:
    def pytest_sessionfinish(self, session, exitstatus):
        observations = {}
        for item in session.items:
            observations.update(getattr(item.module, 'OBSERVATIONS', {}))
        report = {'scope': 'isolated_gap_reproduction_not_acceptance', 'pytest_exit_code': int(exitstatus), 'observations': observations}
        (ROOT / 'docs/writing-agent/completion-audit-probes.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), '-q', f'--junitxml={ROOT / "docs/writing-agent/completion-audit-probes.xml"}'], plugins=[AuditReporter()]))

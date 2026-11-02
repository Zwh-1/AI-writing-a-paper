from test_workbench import client, project
from app import db, tasks


def test_acknowledge_failed_task_keeps_status_and_records_trace(client):
    pid = project(client)
    t = tasks.enqueue(pid, 'write', {'section_id': None, 'marker': 'a'})
    db.execute("UPDATE tasks SET status='failed',error='模型未返回约定 JSON' WHERE id=?", (t['id'],))
    r = client.post(f"/api/v1/tasks/{t['id']}/acknowledge", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['status'] == 'failed'
    assert body['payload']['acknowledged_at']
    assert body['payload']['acknowledged_status'] == 'failed'
    rows = client.get('/api/v1/tasks?summary=true&active_only=true&project_id=' + pid).json()
    assert any(x['id'] == t['id'] and x['acknowledged_at'] for x in rows)


def test_acknowledge_rejects_unfinished_task(client):
    pid = project(client)
    t = tasks.enqueue(pid, 'write', {'section_id': None, 'marker': 'b'})
    db.execute("UPDATE tasks SET status='running' WHERE id=?", (t['id'],))
    r = client.post(f"/api/v1/tasks/{t['id']}/acknowledge", json={})
    assert r.status_code == 409


def test_acknowledge_outcome_unknown_still_closes_warning(client):
    pid = project(client)
    t = tasks.enqueue(pid, 'write', {'section_id': None, 'marker': 'c'})
    db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?", (t['id'],))
    r = client.post(f"/api/v1/tasks/{t['id']}/acknowledge", json={'note': '已核对账单'})
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'acknowledged'
    assert r.json()['payload']['acknowledged_note'] == '已核对账单'

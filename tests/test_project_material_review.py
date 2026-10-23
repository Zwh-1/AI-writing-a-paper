import pytest

from test_workbench import client, project, file
from app import db, material_summary, tasks


def document(client, pid, text):
    result = file(client, pid, text)
    page_id = db.uid()
    db.execute('INSERT INTO pages(id,file_id,page_number,text) VALUES(?,?,?,?)', (page_id, result['id'], 2, text + ' 第二页'))
    db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)', (db.uid(), result['id'], page_id, db.dumps({'page': 2}), text, text, 'evidence', 'native', 0))
    db.execute('UPDATE files SET pages=2 WHERE id=?', (result['id'],))
    return result


def preview(client, pid):
    response = client.get('/api/v1/projects/' + pid + '/materials/review')
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, pid, state, **changes):
    return client.post('/api/v1/projects/' + pid + '/materials/review', json={'confirm': True, 'snapshot': state['snapshot'], **changes})


def test_project_review_updates_all_active_files_and_preserves_exclusions(client):
    pid = project(client)
    first = document(client, pid, '第一份资料')
    second = document(client, pid, '第二份资料')
    excluded = db.one('SELECT id FROM pages WHERE file_id=? AND page_number=2', (first['id'],))['id']
    client.patch('/api/v1/pages/' + excluded, json={'review_state': 'not_applicable', 'review_note': '封面排除'})
    before = {f['id']: material_summary.source(f['id'])[2] for f in (first, second)}
    state = preview(client, pid)
    assert state['pending_files'] == 2 and state['pending_pages'] == 3
    assert state['file_count'] == 2 and state['excluded_pages'] == 1 and state['can_confirm']
    response = confirm(client, pid, state)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['complete'] and result['updated_files'] == 2 and result['updated_pages'] == 3
    assert result['reviewed_pages'] == 3 and result['excluded_pages'] == 1 and result['pending_pages'] == 0
    assert result['summary_reviewed'] is False and result['external_requests'] is False
    page = db.one('SELECT * FROM pages WHERE id=?', (excluded,))
    assert page['review_state'] == 'not_applicable' and page['review_note'] == '封面排除'
    assert db.one('SELECT verified FROM sources WHERE page_id=?', (excluded,))['verified'] == 0
    assert not db.one("SELECT id FROM pages WHERE file_id IN (?,?) AND review_state='unreviewed'", (first['id'], second['id']))
    assert all(material_summary.source(fid)[2] == digest for fid, digest in before.items())
    assert not db.rows('SELECT * FROM generation_runs')
    assert preview(client, pid)['complete']
    again = confirm(client, pid, preview(client, pid))
    assert again.status_code == 200 and again.json()['updated_pages'] == 0
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='project_originals_reviewed'")['n'] == 1


def test_project_review_does_not_touch_other_projects_or_inactive_files(client):
    pid = project(client)
    active = document(client, pid, '当前文件')
    inactive = document(client, pid, '已停用文件')
    db.execute('UPDATE files SET active=0 WHERE id=?', (inactive['id'],))
    other = document(client, project(client, '另一个项目'), '另一个项目的文件')
    state = preview(client, pid)
    assert state['file_count'] == 1 and state['pending_pages'] == 2
    assert confirm(client, pid, state).status_code == 200
    for f in (inactive, other):
        assert all(p['review_state'] == 'unreviewed' for p in db.rows('SELECT * FROM pages WHERE file_id=?', (f['id'],)))
        assert not db.one('SELECT id FROM sources WHERE file_id=? AND verified=1', (f['id'],))
    assert all(p['review_state'] == 'reviewed' for p in db.rows('SELECT * FROM pages WHERE file_id=?', (active['id'],)))


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_project_review_requires_explicit_confirmation(client, value):
    pid = project(client)
    document(client, pid, '核对确认')
    assert confirm(client, pid, preview(client, pid), confirm=value).status_code == 422
    assert not db.one("SELECT id FROM pages WHERE review_state='reviewed'")


@pytest.mark.parametrize('change', ['text', 'notes', 'review', 'new_file', 'inactive'])
def test_project_review_rejects_stale_snapshot_atomically(client, change):
    pid = project(client)
    f = document(client, pid, '快照文件')
    second = document(client, pid, '另一个快照文件')
    state = preview(client, pid)
    page = db.one('SELECT id FROM pages WHERE file_id=?', (f['id'],))['id']
    if change in ('text', 'notes'):
        db.execute('UPDATE pages SET ' + change + '=? WHERE id=?', ('已改变的内容', page))
    elif change == 'review':
        client.patch('/api/v1/pages/' + page, json={'review_state': 'reviewed'})
    elif change == 'new_file':
        document(client, pid, '新加入的文件')
    else:
        db.execute('UPDATE files SET active=0 WHERE id=?', (f['id'],))
    response = confirm(client, pid, state)
    assert response.status_code == 409 and '已变化' in response.json()['detail']
    assert not db.one("SELECT id FROM pages WHERE file_id=? AND review_state='reviewed'", (second['id'],))
    assert not db.one("SELECT id FROM audit WHERE action='project_originals_reviewed'")


@pytest.mark.parametrize('problem', ['queued', 'parsing', 'failed', 'count', 'reparse'])
def test_project_review_blocks_all_writes_until_every_file_is_ready(client, problem):
    pid = project(client)
    first = document(client, pid, '正常文件')
    second = document(client, pid, '未就绪文件')
    if problem == 'count':
        db.execute('UPDATE files SET pages=3 WHERE id=?', (second['id'],))
    elif problem == 'reparse':
        tasks.enqueue(pid, 'parse', {'file_id': second['id'], 'repair': True})
    else:
        db.execute('UPDATE files SET status=? WHERE id=?', (problem, second['id']))
    state = preview(client, pid)
    assert not state['can_confirm'] and not state['complete'] and len(state['blocked_files']) == 1
    assert confirm(client, pid, state).status_code == 409
    assert not db.one("SELECT id FROM pages WHERE file_id=? AND review_state='reviewed'", (first['id'],))
    assert not db.one('SELECT id FROM sources WHERE file_id=? AND verified=1', (first['id'],))


def test_project_review_rejects_empty_project_and_foreign_snapshot(client):
    pid = project(client)
    empty = preview(client, pid)
    assert empty['file_count'] == 0 and not empty['can_confirm'] and not empty['complete']
    assert confirm(client, pid, empty).status_code == 422
    document(client, pid, '本项目')
    other = project(client, '另一项目')
    document(client, other, '其他项目')
    assert confirm(client, pid, preview(client, other)).status_code == 409
    assert not db.one("SELECT id FROM pages WHERE review_state='reviewed'")


def test_project_review_rejects_missing_snapshot(client):
    pid = project(client)
    document(client, pid, '无快照')
    response = client.post('/api/v1/projects/' + pid + '/materials/review', json={'confirm': True})
    assert response.status_code == 422


def test_project_review_rolls_back_every_file_on_write_failure(client, monkeypatch):
    from app import material_review
    pid = project(client)
    document(client, pid, '回滚文件一')
    document(client, pid, '回滚文件二')
    state = preview(client, pid)
    apply = material_review._apply
    calls = []

    def fail_second(connection, record):
        calls.append(record['file']['id'])
        if len(calls) == 2:
            raise RuntimeError('simulated write failure')
        return apply(connection, record)

    monkeypatch.setattr(material_review, '_apply', fail_second)
    with pytest.raises(RuntimeError, match='simulated write failure'):
        confirm(client, pid, state)
    assert not db.one("SELECT id FROM pages WHERE review_state='reviewed'")
    assert not db.one('SELECT id FROM sources WHERE verified=1')
    assert not db.one("SELECT id FROM audit WHERE action='project_originals_reviewed'")

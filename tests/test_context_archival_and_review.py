from pathlib import Path
import os,time
import pytest
from fastapi import HTTPException
from test_workbench import client,project,manual_package,save
from test_writing_continuity import chapter
from app import db,tasks,security,config
from app import context_maintenance as maintenance
from app.ai_graphs.chapter import run
from app.manuscript_review import prepare


def completed_graph(client):
    pid=project(client);task=tasks.enqueue(pid,'support_check',{})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?",(task['id'],))
    task=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    run(task,{'id':'fixture'},{'fixture':True},[('draft',lambda state:{**state,'done':True})],lambda state:None)
    db.execute("UPDATE tasks SET status='succeeded',created_at='2000-01-01T00:00:00+00:00' WHERE id=?",(task['id'],))
    return pid,task


def test_archive_and_restore_preserve_receipts_ledgers_and_checkpoints(client):
    pid,task=completed_graph(client)
    before=db.rows('SELECT * FROM ai_runtime_events WHERE task_id=?',(task['id'],))
    assert before
    result=maintenance.archive(pid,{'keep_days':30})
    assert result['events']==len(before) and result['checkpoint_rows']>0
    assert not db.rows('SELECT * FROM ai_runtime_events WHERE task_id=?',(task['id'],))
    assert db.one('SELECT * FROM ai_node_receipts WHERE task_id=?',(task['id'],))
    assert maintenance.restore(pid,result['archive_id'])['restored']
    assert db.rows('SELECT * FROM ai_runtime_events WHERE task_id=? ORDER BY id',(task['id'],))==before
    assert maintenance.restore(pid,result['archive_id'])['restored']
    other=project(client,'其他项目')
    with pytest.raises(HTTPException):maintenance.restore(other,result['archive_id'])


def test_archival_excludes_unresolved_requests_and_is_idle_only(client):
    pid,task=completed_graph(client)
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
               ('unknown',task['id'],pid,'outcome_unknown','fixture','openai','{}',db.now()))
    assert not maintenance.candidates(pid)
    db.execute("UPDATE tasks SET status='running' WHERE id=?",(task['id'],))
    with pytest.raises(HTTPException):maintenance.archive(pid,{'keep_days':30})


def test_frozen_blob_references_remain_reachable_to_gc(client):
    from app.workspace_maintenance import orphan_objects
    pid=project(client)
    leaf=security.put_blob(b'Frozen skill instructions');root=security.put_blob(db.dumps({'instructions_blob':leaf}).encode())
    unused=security.put_blob(b'unused')
    db.set_setting('frozen-context:'+pid,{'blob':root})
    for sha in (leaf,root,unused):os.utime(security.safe_object(sha),(time.time()-172800,)*2)
    result=orphan_objects()
    ids={row['sha256'] for row in result['items']}
    assert unused in ids and leaf not in ids and root not in ids


def test_review_batches_whole_paragraphs_and_covers_every_saved_paragraph(client):
    pid=project(client);manual_package(client,pid)
    first=chapter(client,pid,'方法','\n'.join('这是第'+str(i)+'段的完整条件。'*25 for i in range(12)))
    second=chapter(client,pid,'结果','只使用已登记的结果。\n不能推断未执行实验。')
    chunks,manifest=prepare(pid,7000,1000)
    assert len(chunks)>2
    assert set(manifest['related_revisions'])=={first,second}
    provided=[(c['section']['id'],p['paragraph_index']) for c in chunks for p in c['paragraphs']]
    assert len(provided)==len(set(provided))==14
    assert all(not p['text'].endswith('…') for c in chunks for p in c['paragraphs'])


def test_oversize_single_review_paragraph_is_reported_not_truncated(client):
    pid=project(client);manual_package(client,pid);chapter(client,pid,'正文','大'*5000)
    with pytest.raises(HTTPException) as error:prepare(pid,5000,1000)
    assert error.value.detail['code']=='REVIEW_PARAGRAPH_TOO_LARGE'


def test_cross_section_windows_keep_actual_reading_scope(client):
    from app.manuscript_review import cross_section_windows,SYSTEM
    from app.source_payload import token_upper_bound
    pid=project(client);manual_package(client,pid)
    first=chapter(client,pid,'方法','静态分析。\n尚未进行运行测试。')
    second=chapter(client,pid,'结果','结果限于合成样本。\n样本数量为12。')
    chunks,manifest=prepare(pid,12000,1000)
    windows=cross_section_windows(chunks,12000,1000)
    assert windows and windows[0]['can_review']
    assert {x['section_id'] for x in windows[0]['coverage']}=={first,second}
    assert token_upper_bound(SYSTEM,db.dumps(windows[0]))+1000<=12000
    assert all(x['paragraphs'] for x in windows[0]['sections'])


def test_review_executes_paragraph_and_cross_section_windows_with_honest_coverage(client,monkeypatch):
    from app.manuscript_review import run as review
    from app import writing_flow
    pid=project(client);manual_package(client,pid)
    chapter(client,pid,'方法','仅进行了静态分析。');chapter(client,pid,'结果','没有实测性能结果。')
    task=tasks.enqueue(pid,'semantic_review',{});calls=[]
    def fake(task,system,context,manifest,**options):
        calls.append(options['stage'])
        return {'text':db.dumps({'coverage_complete':True,'findings':[]}),'run_id':'synthetic'}
    monkeypatch.setattr(writing_flow,'_cached_call',fake)
    result=review(task)
    assert result['paragraph_coverage_complete'] and result['cross_section_semantic_coverage']
    assert 'whole_paper_review_chunk' in calls and 'whole_paper_cross_section' in calls
    assert result['requires_human_review']


def test_manuscript_review_version_guard_rechecks_before_send(client):
    from app.tasks import _validate_model_source_inputs
    pid=project(client);manual_package(client,pid);sid=chapter(client,pid,'正文','冻结前的正文。')
    chunks,manifest=prepare(pid,100000,8192)
    rid=manifest['manuscript_review_revisions'][sid]
    assert save(client,sid,'正文已更改。',rid).status_code==200
    with pytest.raises(ValueError,match='正文版本'):_validate_model_source_inputs(pid,manifest)

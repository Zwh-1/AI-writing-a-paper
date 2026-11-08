"""Explicit, recoverable archiving of finished runtime observations/checkpoints."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import base64
import hashlib
import sqlite3
from contextlib import closing
from fastapi import APIRouter,HTTPException
from . import db,security
from .ai_request_cache import digest
from .ai_runtime.policy import frozen

router=APIRouter()


def candidates(pid,days=30):
    security.require_project(pid)
    if type(days) is not int or not 1<=days<=3650:raise HTTPException(422,'保留天数为1至3650')
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    rows=db.rows("SELECT t.* FROM tasks t WHERE t.project_id=? AND t.status IN ('succeeded','cancelled') AND t.created_at<? "
        "AND NOT EXISTS(SELECT 1 FROM generation_runs g WHERE g.task_id=t.id AND (g.status IN ('pending','sent','outcome_unknown') OR g.cost IS NULL)) "
        "AND (EXISTS(SELECT 1 FROM ai_runtime_events e WHERE e.task_id=t.id) OR EXISTS(SELECT 1 FROM ai_node_receipts r WHERE r.task_id=t.id)) ORDER BY t.created_at LIMIT 100",(pid,cutoff))
    archived={identity for item in db.setting('context_archives:'+pid,[]) if not item.get('restored') for identity in item.get('task_ids',[])}
    return [row for row in rows if row['id'] not in archived]


@router.get('/projects/{pid}/context-maintenance')
def preview(pid,keep_days:int=30):
    rows=candidates(pid,keep_days)
    return {'task_ids':[x['id'] for x in rows],'keep_days':keep_days,
            'archives':db.setting('context_archives:'+pid,[]),
            'preserved':['request_ledgers','node_receipts','source_objects','task_protocols','project_memory_versions']}


def _idle():
    if db.one("SELECT id FROM tasks WHERE status='running' LIMIT 1"):raise HTTPException(409,'请在执行器空闲时维护上下文记录')


def _encode(row):return {k:{'base64':base64.b64encode(v).decode()} if isinstance(v,bytes) else v for k,v in dict(row).items()}
def _decode(row):return {k:base64.b64decode(v['base64']) if isinstance(v,dict) and set(v)=={'base64'} else v for k,v in row.items()}


@router.post('/projects/{pid}/context-maintenance/archive')
def archive(pid,body:dict):
    keep=body.get('keep_days',30);rows=candidates(pid,keep);_idle()
    if not rows:return {'archived_tasks':0,'events':0,'checkpoint_rows':0}
    events=[];threads=set()
    for task in rows:
        observations=db.rows('SELECT * FROM ai_runtime_events WHERE task_id=?',(task['id'],));events+=observations
        receipts=db.rows('SELECT * FROM ai_node_receipts WHERE task_id=?',(task['id'],))
        epochs={task['attempt'],*[r['execution_epoch'] for r in observations],*[r['execution_epoch'] for r in receipts]}
        for run_id in {r['run_id'] for r in receipts}:
            for epoch in epochs:threads.add(digest([run_id,frozen(task).get('graph_version','chapter.graph.v1'),epoch]))
    checkpoint=Path(db.DB_PATH).parent/'ai-graph-checkpoints.sqlite3';checkpoint_rows={}
    if checkpoint.is_file():
        with closing(sqlite3.connect(checkpoint)) as c:
            c.row_factory=sqlite3.Row
            for table in ('checkpoints','writes'):
                if not c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():continue
                checkpoint_rows[table]=[_encode(row) for thread in sorted(threads) for row in c.execute('SELECT * FROM '+table+' WHERE thread_id=?',(thread,))]
    value={'project_id':pid,'task_ids':[r['id'] for r in rows],'events':events,'checkpoints':checkpoint_rows,'created_at':db.now()}
    raw=db.dumps(value).encode();sha=security.put_blob(raw);identity=db.uid()
    archives=db.setting('context_archives:'+pid,[])
    entry={'id':identity,'blob':sha,'hash':hashlib.sha256(raw).hexdigest(),'tasks':len(rows),'task_ids':value['task_ids'],'events':len(events),
           'checkpoint_rows':sum(len(x) for x in checkpoint_rows.values()),'created_at':value['created_at'],'restored':False}
    # Archive bytes are committed before deleting reconstructible observations.
    with db.transaction():
        archives=db.setting('context_archives:'+pid,[])
        db.set_setting('context_archives:'+pid,[*archives,entry])
    return _prune(pid,entry,value,checkpoint_rows)


@db.atomic
def _prune(pid,entry,value,checkpoint_rows):
    _idle()
    for identity in value['task_ids']:
        row=db.one('SELECT status FROM tasks WHERE id=? AND project_id=?',(identity,pid))
        if not row or row['status'] not in ('succeeded','cancelled'):raise HTTPException(409,'归档任务已恢复运行，保留已保存归档，未继续清理')
    events=value['events'];checkpoint=Path(db.DB_PATH).parent/'ai-graph-checkpoints.sqlite3'
    for row in events:db.execute('DELETE FROM ai_runtime_events WHERE id=?',(row['id'],))
    if checkpoint_rows:
        with closing(sqlite3.connect(checkpoint)) as c:
            for table,values in checkpoint_rows.items():
                for thread in {r['thread_id'] for r in values}:c.execute('DELETE FROM '+table+' WHERE thread_id=?',(thread,))
            c.commit()
    db.audit(pid,'context_runtime_archived',{k:v for k,v in entry.items() if k not in ('blob','hash')})
    return {'archive_id':entry['id'],'archived_tasks':len(value['task_ids']),'events':len(events),'checkpoint_rows':entry['checkpoint_rows']}


@router.post('/projects/{pid}/context-maintenance/restore/{identity}')
@db.atomic
def restore(pid,identity):
    security.require_project(pid);_idle()
    archives=db.setting('context_archives:'+pid,[]);entry=next((x for x in archives if x['id']==identity),None)
    if not entry:raise HTTPException(404,'上下文归档不存在')
    raw=security.safe_object(entry['blob']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=entry['hash']:raise HTTPException(409,'上下文归档完整性校验失败')
    value=db.loads(raw.decode())
    if value['project_id']!=pid:raise HTTPException(403,'归档不属于项目')
    for row in value['events']:
        old=db.one('SELECT * FROM ai_runtime_events WHERE id=?',(row['id'],))
        if old and old!=row:raise HTTPException(409,'运行记录标识冲突')
        if not old:
            columns=list(row);db.execute('INSERT INTO ai_runtime_events('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(row.values()))
    if value['checkpoints']:
        from langgraph.checkpoint.sqlite import SqliteSaver
        path=Path(db.DB_PATH).parent/'ai-graph-checkpoints.sqlite3'
        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.setup()
            # A duplicate key is idempotent only when its state also matches.
            # Roll back this database as well as the business transaction if
            # any row conflicts; never mark a mixed checkpoint as restored.
            with saver.conn:
                for table,rows in value['checkpoints'].items():
                    if table not in ('checkpoints','writes'):raise HTTPException(409,'归档表无效')
                    schema=saver.conn.execute('PRAGMA table_info('+table+')').fetchall()
                    known={column[1] for column in schema};primary=[column[1] for column in schema if column[5]]
                    for row in rows:
                        decoded=_decode(row);columns=list(decoded)
                        if set(columns)-known or not primary or set(primary)-set(columns):raise HTTPException(409,'归档检查点字段与当前存储不兼容')
                        old=saver.conn.execute('SELECT '+','.join(columns)+' FROM '+table+' WHERE '+
                            ' AND '.join(key+'=?' for key in primary),[decoded[key] for key in primary]).fetchone()
                        if old is not None:
                            if tuple(old)!=tuple(decoded.values()):raise HTTPException(409,'归档检查点与现有状态冲突，未恢复')
                            continue
                        saver.conn.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(decoded.values()))
    entry['restored']=True;db.set_setting('context_archives:'+pid,archives)
    return {'restored':True,'events':len(value['events'])}

"""Fenced task writes: a late worker may retain evidence, never replace its successor."""
from contextlib import contextmanager
from contextvars import ContextVar
from . import db

_OWNER=ContextVar('task_execution_owner',default=None)
FIELDS=frozenset({'status','result','error','lease_until','heartbeat','phase','progress','cancel_requested','payload'})


@contextmanager
def ownership(task):
    token=_OWNER.set((task['id'],task['attempt']))
    try:yield
    finally:_OWNER.reset(token)


def expected_attempt(task_id):
    owner=_OWNER.get()
    return owner[1] if owner and owner[0]==task_id else None


def active_owner():
    owner=_OWNER.get()
    if not owner:return None
    task=db.one('SELECT id,project_id,attempt,status,cancel_requested FROM tasks WHERE id=?',(owner[0],))
    return task if task and task['attempt']==owner[1] and task['status']=='running' and not task['cancel_requested'] else None


def assert_current(task):
    row=db.one('SELECT attempt,status,cancel_requested FROM tasks WHERE id=?',(task['id'],))
    if not row or row['attempt']!=task.get('attempt',row['attempt']):
        raise InterruptedError('任务代次已变化，旧执行者不能继续提交')
    if row['cancel_requested'] or row['status']=='cancelled':raise InterruptedError('用户已请求取消')
    return row


def transition(task_id,attempt,expected_status,**updates):
    if not updates or set(updates)-FIELDS:raise ValueError('Invalid task transition fields')
    statuses=(expected_status,) if isinstance(expected_status,str) else tuple(expected_status)
    if not statuses:raise ValueError('An expected task status is required')
    with db.transaction() as connection:
        clause=','.join(key+'=?' for key in updates)+',updated_at=?'
        args=(*updates.values(),db.now(),task_id,attempt,*statuses)
        cursor=connection.execute('UPDATE tasks SET '+clause+' WHERE id=? AND attempt=? AND status IN ('+
                                  ','.join('?' for _ in statuses)+')',args)
        if cursor.rowcount==1:return True
        row=connection.execute('SELECT project_id,attempt,status FROM tasks WHERE id=?',(task_id,)).fetchone()
        if row:
            result_blob=None
            if updates.get('result') is not None:
                from .security import put_blob
                result_blob=put_blob(str(updates['result']).encode('utf-8'))
            db.audit(row['project_id'],'late_task_transition_ignored',{
                'task_id':task_id,'expected_attempt':attempt,'actual_attempt':row['attempt'],
                'expected_statuses':statuses,'actual_status':row['status'],
                'target_status':updates.get('status'),'result_retained':result_blob is not None,
                'late_result_blob':result_blob})
        return False

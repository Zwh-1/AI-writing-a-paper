"""Synchronous callers claim an ordinary queue lease; no unfenced graph writes."""
import threading
import time
from .. import db, provider, security


def execute(task, operation):
    from .. import tasks
    from ..task_transitions import ownership, transition
    with db.transaction() as connection:
        live=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(task['id'],task['project_id']))
        if (not live or live['status']!='queued' or live['cancel_requested']
                or live['attempt']!=task.get('attempt',live['attempt'])):
            raise InterruptedError('同步写作不能取得当前任务执行权')
        claimed=connection.execute("UPDATE tasks SET status='running',attempt=attempt+1,heartbeat=?,lease_until=?,updated_at=? WHERE id=? AND status='queued' AND attempt=?",
                           (time.time(),time.time()+60,db.now(),task['id'],live['attempt']))
        if claimed.rowcount!=1:raise InterruptedError('同步写作执行权已变化')
        current=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    done=threading.Event()
    heartbeat=threading.Thread(target=tasks.heartbeat_task,args=(task['id'],current['attempt'],done),daemon=True)
    heartbeat.start()
    try:
        with ownership(current),provider.connection_pool():
            result=operation(current)
            from ..documents import plain
            valid=isinstance(result,dict) and bool(result.get('document')) and bool(plain(result['document']).strip())
            transition(current['id'],current['attempt'],'running',status='succeeded' if valid else 'failed',
                       result=db.dumps(result),error=None if valid else '写作未形成有效正文候选',lease_until=None)
            return result
    except Exception as error:
        from ..summary_billing import BudgetExceeded
        status=('cancelled' if isinstance(error,InterruptedError) else 'budget_blocked' if isinstance(error,BudgetExceeded)
                else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed')
        transition(current['id'],current['attempt'],'running',status=status,error=security.redact(error),lease_until=None)
        raise
    finally:
        done.set();heartbeat.join(timeout=1)

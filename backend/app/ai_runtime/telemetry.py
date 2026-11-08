"""Local metadata only. Observability cannot change request or budget outcomes."""
from contextlib import contextmanager
import sqlite3
import time
import math
from .. import db


def record(task, stage, kind, status, duration_ms, **metadata):
    from .policy import settings
    try:
        if not settings()['telemetry_enabled']:
            return
        # No prompts, source text, URLs, credentials or exception text are logged.
        allowed = {'run_id', 'action_id', 'reused', 'engine', 'operation', 'error_type',
                   'input_characters', 'output_characters', 'graph_version', 'node_count'}
        metadata = {k: v for k, v in metadata.items() if k in allowed}
        db.execute('INSERT INTO ai_runtime_events(project_id,task_id,execution_epoch,stage,event_kind,status,duration_ms,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (task['project_id'], task['id'], task.get('attempt', 0), str(stage)[:100],
                    kind, status, max(0., float(duration_ms)), db.dumps(metadata), db.now()))
    except (sqlite3.Error, OSError, ValueError):
        # A missing/stale observer never masks a completed billable request.
        return


@contextmanager
def span(task, stage, kind='stage', **metadata):
    start = time.perf_counter()
    try:
        yield
    except BaseException as error:
        record(task, stage, kind, 'failed', (time.perf_counter()-start)*1000,
               error_type=type(error).__name__, **metadata)
        raise
    else:
        record(task, stage, kind, 'completed', (time.perf_counter()-start)*1000, **metadata)


def distribution(values):
    values = sorted(values)
    count = len(values)
    return {'sample_count': count, 'total_ms': round(sum(values), 3),
            'p50_ms': values[(count-1)//2] if count else None,
            'p95_ms': values[math.ceil(.95*count)-1] if count else None}


def summary(pid, task_id=None, *, limit=10000):
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('Telemetry window must contain 1 to 10000 events')
    query = 'SELECT stage,event_kind,status,duration_ms,metadata FROM ai_runtime_events WHERE project_id=?'
    args = [pid]
    if task_id:
        query += ' AND task_id=?'; args.append(task_id)
    total = db.one(query.replace('stage,event_kind,status,duration_ms,metadata', 'COUNT(*) count'), args)['count']
    rows = db.rows(query+' ORDER BY id DESC LIMIT ?', [*args, limit])
    groups = {}
    for row in rows:
        key = (row['stage'], row['event_kind'])
        group = groups.setdefault(key, {'stage': key[0], 'kind': key[1], 'completed': 0,
                                       'failed': 0, 'reused': 0, 'distributions': {'execution': [], 'failure': [], 'reuse': []}})
        group['completed' if row['status']=='completed' else 'failed'] += 1
        reused = bool(db.loads(row['metadata'], {}).get('reused'))
        group['reused'] += int(reused)
        category = 'reuse' if reused else 'execution' if row['status']=='completed' else 'failure'
        group['distributions'][category].append(row['duration_ms'])
    output = []
    for group in groups.values():
        distributions = {key: distribution(values) for key, values in group.pop('distributions').items()}
        group.update(distributions['execution'], distributions=distributions,
                     executed=distributions['execution']['sample_count'])
        output.append(group)
    return {'stages': output, 'events': len(rows), 'total_events': total, 'truncated': total > len(rows),
            'window_limit': limit, 'scope': 'latest_local_observed_events',
            'note': '最多展示最近10000条事件；P50/P95仅统计成功且实际执行的样本，失败与复用另列分布。阶段耗时可重叠，不能相加作为任务总耗时。'}

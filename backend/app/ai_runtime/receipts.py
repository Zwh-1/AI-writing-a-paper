"""Fenced node snapshots; actual remote execution facts stay in the old ledger."""
import hashlib
from fastapi import HTTPException
from .. import db, security, provider
from ..ai_request_cache import digest


def owner(task):
    live = db.one('SELECT project_id,status,attempt,cancel_requested FROM tasks WHERE id=?', (task['id'],))
    if (not live or live['project_id'] != task['project_id'] or live['status'] != 'running'
            or live['attempt'] != task.get('attempt') or live['cancel_requested']):
        raise InterruptedError('AI工作流执行代次失效或已取消')


def unresolved(task):
    row = db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown') LIMIT 1", (task['id'],))
    if row:
        raise provider.ProviderError('工作流仍有未核实模型请求，未自动重发或重计划', unknown=True)


def load(task, run_id, action_id, input_hash, validate):
    owner(task)
    unresolved(task)
    row = db.one('SELECT * FROM ai_node_receipts WHERE run_id=? AND action_id=? AND project_id=? AND task_id=?',
                 (run_id, action_id, task['project_id'], task['id']))
    if not row:
        return None
    if row['input_hash'] != input_hash:
        raise HTTPException(409, {'code': 'AI_NODE_INPUT_CHANGED', 'message': '工作流节点输入已变化，保留原结果；请建立明确的新任务。'})
    raw = security.safe_object(row['result_blob']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['result_hash']:
        raise HTTPException(409, '工作流结果完整性校验失败')
    output = db.loads(raw.decode('utf-8'))
    validate(output)
    return output


def commit(task, run_id, action_id, input_hash, output, validate):
    validate(output)
    raw = db.dumps(output).encode('utf-8')
    sha = security.put_blob(raw)
    with db.transaction():
        owner(task)
        validate(output)
        unresolved(task)
        old = db.one('SELECT * FROM ai_node_receipts WHERE run_id=? AND action_id=?', (run_id, action_id))
        if old:
            if (old['project_id']!=task['project_id'] or old['task_id']!=task['id']
                    or old['input_hash'] != input_hash or old['result_hash'] != hashlib.sha256(raw).hexdigest()):
                raise HTTPException(409, '工作流节点已由另一执行者提交不同结果')
            return
        db.execute('INSERT INTO ai_node_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                   (run_id, action_id, task['project_id'], task['id'], task['attempt'], input_hash,
                    sha, hashlib.sha256(raw).hexdigest(), db.now()))

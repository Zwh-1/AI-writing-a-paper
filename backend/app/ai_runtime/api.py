"""Authenticated local controls and observations; no billable API actions."""
from fastapi import APIRouter, HTTPException
from .. import db, security
from . import policy, telemetry, inventory

router = APIRouter(prefix='/ai-runtime')


@router.post('/projects/{pid}/compare')
def compare(pid: str, body: dict):
    from .evaluation import compare as compare_tasks
    return compare_tasks(pid,body)


@router.post('/projects/{pid}/context-experiments')
def create_context_experiment(pid:str,body:dict):
    from .context_experiments import create
    return create(pid,body)


@router.get('/projects/{pid}/context-experiments/{identity}')
def get_context_experiment(pid:str,identity:str):
    from .context_experiments import get
    return get(pid,identity)


@router.post('/projects/{pid}/context-assessments')
def record_context_assessment(pid:str,body:dict):
    from .context_experiments import assess
    return assess(pid,body)


@router.get('/policy')
def get_policy():
    return policy.settings()


@router.put('/policy')
def put_policy(body: dict):
    return policy.save(body)


@router.get('/inventory')
def get_inventory():
    return inventory.scan()


@router.get('/projects/{pid}')
def overview(pid: str, task_id: str | None = None):
    security.require_project(pid)
    if task_id and not db.one('SELECT id FROM tasks WHERE project_id=? AND id=?', (pid, task_id)):
        raise HTTPException(404, '任务不属于当前项目')
    from ..budget_usage import charge
    from .health import status as health_status
    health = health_status()
    query = 'SELECT COUNT(*) count FROM ai_node_receipts WHERE project_id=?'
    args = [pid]
    if task_id:
        query += ' AND task_id=?'; args.append(task_id)
    return {'policy_for_new_tasks': policy.settings(), 'metrics': telemetry.summary(pid, task_id),
            'completed_nodes': db.one(query, args)['count'], 'budget_occupancy': charge(pid, task_id=task_id),
            'budget_scope': 'task' if task_id else 'project',
            'capabilities': health['capabilities'], 'health': health,
            'note': '费用与未知预留仍以现有账本为准；图节点完成不代表论文通过人工审查。'}

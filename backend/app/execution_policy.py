"""Optional stage ceilings and evidence-labelled cost forecasts, never quality bypasses."""
import math
from fastapi import APIRouter,HTTPException
from . import db,security,budget_usage

router=APIRouter()


def settings(pid):
    return db.setting('execution_budget_policy:'+pid,{'stage_limits':{},'currency':None})


def check_stage(task,manifest,reservation,exclude_run_ids=()):
    policy=settings(task['project_id'])
    payload=db.loads(task.get('payload'),{})
    limits=payload.get('stage_budget_limits',policy.get('stage_limits',{}))
    stage=manifest.get('writing_stage') or manifest.get('stage') or manifest.get('knowledge_operation') or task['kind']
    if stage not in limits:return
    cap=limits[stage]
    if type(cap) not in (int,float) or not math.isfinite(cap) or cap<0:raise ValueError('阶段预算必须为非负有限数值')
    field='writing_stage' if manifest.get('writing_stage') else 'stage' if manifest.get('stage') else 'knowledge_operation'
    used=budget_usage.charge(task['project_id'],task_id=task['id'],manifest_scope=(field,stage),exclude_run_ids=exclude_run_ids)
    if used+reservation>cap:
        from .summary_billing import BudgetExceeded
        raise BudgetExceeded('阶段“'+str(stage)+'”已达到费用上限；候选和核验缺口已保留，不跳过质量检查')


@router.get('/projects/{pid}/execution-budget-policy')
def get_policy(pid):
    security.require_project(pid)
    return settings(pid)


@router.put('/projects/{pid}/execution-budget-policy')
@db.atomic
def save_policy(pid,body:dict):
    security.require_project(pid)
    if set(body)!={'stage_limits','currency'} or not isinstance(body['stage_limits'],dict):raise HTTPException(422,'须提供stage_limits和currency')
    from .expense_evidence import budget_currency
    if body['currency']!=budget_currency(pid):raise HTTPException(422,'阶段预算币种须与项目一致')
    for stage,value in body['stage_limits'].items():
        if not isinstance(stage,str) or not stage or len(stage)>100 or type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise HTTPException(422,'阶段名及预算数值无效')
    db.set_setting('execution_budget_policy:'+pid,body)
    db.audit(pid,'execution_budget_policy_changed',body)
    return settings(pid)


@router.post('/projects/{pid}/execution-cost-preview')
def preview(pid,body:dict):
    security.require_project(pid)
    task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(body.get('task_id'),pid))
    remaining=body.get('remaining_by_stage')
    if not task or not isinstance(remaining,dict) or any(not isinstance(k,str) or type(v) is not int or v<0 for k,v in remaining.items()):
        raise HTTPException(422,'须指定本项目任务和各阶段剩余工作数')
    samples={}
    for row in db.rows("SELECT g.cost,g.manifest,m.group_id FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.project_id=? AND g.task_id=? AND g.status IN ('completed','completed_invalid')",(pid,task['id'])):
        # Do not allocate aggregate account bills to individual requests.
        if row['group_id'] or type(row['cost']) not in (int,float) or not math.isfinite(row['cost']) or row['cost']<0:continue
        meta=db.loads(row['manifest'],{})
        stage=meta.get('writing_stage') or meta.get('stage') or meta.get('knowledge_operation') or task['kind']
        samples.setdefault(stage,[]).append(row['cost'])
    stages=[];total=0.;complete=True
    for stage,count in remaining.items():
        values=sorted(samples.get(stage,[]))
        mean=sum(values)/len(values) if values else None
        upper=values[min(len(values)-1,math.ceil(.95*len(values))-1)] if values else None
        estimate=count*mean if mean is not None else 0 if count==0 else None
        if estimate is None:complete=False
        else:total+=estimate
        stages.append({'stage':stage,'remaining_units':count,'sample_count':len(values),
                       'mean_recorded_estimate':mean,'p95_recorded_estimate':upper,'remaining_estimate':estimate})
    return {'stages':stages,'remaining_estimate':total if complete else None,'known_stage_subtotal':total,
            'currency':settings(pid).get('currency'),'basis':'历史本地估算，非服务商实际报价或支出承诺',
            'new_model_requests':0,'budget_changed':False,'quality_checks_skipped':False}

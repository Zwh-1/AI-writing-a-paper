"""User-started, bounded paired experiments and version-bound human assessments."""
from copy import deepcopy
import math
from fastapi import HTTPException
from .. import db,security
from ..ai_request_cache import digest


def _key(pid,identity):return 'context_experiment:'+pid+':'+identity


def get(pid,identity):
    security.require_project(pid);value=db.setting(_key(pid,identity))
    if not value:raise HTTPException(404,'上下文实验不存在')
    return value


@db.atomic
def create(pid,body):
    """Queue standard writing tasks; never add budget or bypass provider policy."""
    from .. import tasks
    from .policy import new_protocol
    from ..budget_usage import charge
    project=security.require_project(pid);sid=body.get('section_id');sec=security.require_section(sid)
    if sec['project_id']!=pid:raise HTTPException(404,'章节不属于项目')
    limit=body.get('budget_limit')
    if type(limit) not in (int,float) or not math.isfinite(limit) or limit<=0:raise HTTPException(422,'须指定本次配对实验的总费用上限')
    if charge(pid)+limit>project['budget']:raise HTTPException(422,'实验上限超过项目现有剩余预算；未创建额外额度')
    if db.one("SELECT id FROM tasks WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(pid,)):raise HTTPException(409,'请等待本项目任务结束后冻结配对实验')
    instruction=body.get('instruction','')
    if not isinstance(instruction,str) or len(instruction)>12000:raise HTTPException(422,'实验指令无效')
    operation=body.get('operation','generate')
    if operation not in ('generate','revise'):raise HTTPException(422,'实验操作无效')
    base={'section_id':sid,'base_revision_id':sec['current_revision_id'],'operation':operation,
          'instruction':instruction,'target_node_ids':body.get('target_node_ids',[])}
    tasks.writing_preflight(pid,base)
    from ..writing_continuity import collect
    _,guard,_=collect(pid,sid,mode='full_manuscript')
    if sec['current_revision_id']:guard['related_revisions'][sid]=sec['current_revision_id']
    else:guard['empty_section_revisions'][sid]=None
    guard['rule_package_id']=project['rule_package_id']
    from ..writing_agent.common import state
    head=state(pid)
    guard.update(section_id=sid,core_version_id=head['core_version_id'],target_snapshot_id=head['target_snapshot_id'],
                 contract_version_id=head['contracts'].get(sid))
    from ..writing_live import versions
    guard['live_input_versions']=versions(pid)
    protocol=new_protocol(pid);identity=db.uid();ids=[]
    # The experiment identity is deliberately excluded from A/B input comparison;
    # budget_limit is identical on both arms and enforced over their union.
    for mode in ('full_manuscript','hierarchical_v1'):
        frozen=deepcopy(protocol);frozen['writing_flow_policy']['context_mode']=mode
        task=tasks.enqueue(pid,'write',{**base,'ai_runtime':frozen,
            'context_experiment_id':identity,'context_experiment_guard':guard,'budget_limit':limit})
        ids.append(task['id'])
    value={'id':identity,'project_id':pid,'task_ids':ids,'budget_limit':limit,
        'created_at':db.now(),'input_guard':guard,'candidate_adoption':'manual_only',
        'assessment_state':'pending','metrics':['citation_support_rate','omission_count','author_edit_characters']}
    db.set_setting(_key(pid,identity),value);db.audit(pid,'context_experiment_created',value)
    return value


def guard(task,reservation=0,exclude_run_ids=()):
    payload=db.loads(task.get('payload'),{})
    identity=payload.get('context_experiment_id')
    if not identity:return
    value=get(task['project_id'],identity)
    if task['id'] not in value['task_ids'] or payload.get('budget_limit')!=value['budget_limit']:raise HTTPException(409,'实验任务或预算绑定已变化')
    from ..writing_live import validate as validate_inputs
    validate_inputs(task['project_id'],value['input_guard'])
    from ..writing_agent.impact import check_manifest
    if security.require_project(task['project_id'])['rule_package_id']!=value['input_guard']['rule_package_id']:
        raise HTTPException(409,'配对实验的规则版本已变化')
    conflicts=check_manifest(task['project_id'],{**value['input_guard'],'remote':True})
    if conflicts:raise HTTPException(409,{'code':'CONTEXT_EXPERIMENT_INPUT_CHANGED','conflicts':conflicts})
    from ..budget_usage import charge_tasks
    from ..summary_billing import BudgetExceeded
    if charge_tasks(task['project_id'],value['task_ids'],exclude_run_ids=exclude_run_ids)+reservation>value['budget_limit']:
        raise BudgetExceeded('本次全文与分层配对实验已达到总费用上限，已保存完成结果')


@db.atomic
def assess(pid,body):
    """Human ratings stay bound to candidate content, including later edits."""
    tid=body.get('task_id');task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(tid,pid))
    security.require_project(pid)
    if not task:raise HTTPException(404,'任务不属于项目')
    if task['status']!='succeeded':raise HTTPException(409,'只能评价已完成的候选')
    expected=body.get('result_hash');actual=digest(db.loads(task['result'],{}) or {})
    if expected!=actual:raise HTTPException(409,'候选内容已变化，请重新读取对照记录')
    supported=body.get('supported_citations');total=body.get('checked_citations');omissions=body.get('omission_count');edits=body.get('author_edit_characters')
    if any(type(x) is not int or x<0 for x in (supported,total,omissions,edits)) or supported>total:raise HTTPException(422,'人工指标须为非负整数，支持数不得大于核对数')
    assessor=body.get('assessor');notes=body.get('notes','')
    if not isinstance(assessor,str) or not assessor.strip() or len(assessor)>200 or not isinstance(notes,str) or len(notes)>8000:raise HTTPException(422,'须记录评价者和有效说明')
    value={'task_id':tid,'result_hash':actual,'supported_citations':supported,'checked_citations':total,
        'citation_support_rate':supported/total if total else None,'omission_count':omissions,'author_edit_characters':edits,
        'assessor':assessor,'notes':notes,'recorded_at':db.now(),'basis':'human_recorded_not_automatically_verified'}
    key='context_assessments:'+pid+':'+tid;history=db.setting(key,[]);db.set_setting(key,[*history,value])
    db.audit(pid,'context_assessment_recorded',value)
    return value


def assessment(pid,tid,result):
    history=db.setting('context_assessments:'+pid+':'+tid,[]);identity=digest(result)
    return next((row for row in reversed(history) if row['result_hash']==identity),None)

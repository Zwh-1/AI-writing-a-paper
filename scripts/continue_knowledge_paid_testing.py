"""Continue the explicitly authorized knowledge run after its current task.

This helper never renews an allowance, clears a hold, replays an unknown
request, or records a human acceptance. State and model results stay local.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,paid_testing,security

runner=runpy.run_path(str(ROOT/'scripts/knowledge_paid_testing.py'))


def completion_allowed(task):
    if task['status']!='succeeded':return False
    result=db.loads(task.get('result'),{})
    return result.get('phase')=='semantic' and result.get('result',{}).get('complete') is True


def maintenance_outcome(report,task_result):
    """A partial batch can advance, but failure and zero progress stop it."""
    if report.get('no_available_items'):return 'drained'
    if report.get('status') not in ('succeeded','incomplete'):return 'attention_required'
    result=task_result.get('result') or {}
    if result.get('complete') is True:return 'complete'
    if not result.get('completed_item_ids'):return 'attention_required'
    return 'advance'


def phase_arguments(task,allocation,output,phase,**options):
    """Carry the same human resume evidence into each metered phase."""
    payload=db.loads(task.get('payload'),{})
    return SimpleNamespace(allocation=allocation,phase=phase,file=[],max_items=None,
        artifact=None,query=None,output=output,stream=True,connect_timeout=45,
        resume_authorization=payload.get('paid_testing_resume_id'),**options)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allocation',required=True);p.add_argument('--semantic-task',required=True)
    p.add_argument('--output',type=Path,default=ROOT/'.runtime/knowledge-paid-testing-20261028')
    p.add_argument('--maintenance-batch',type=int,default=64)
    a=p.parse_args()
    if not 1<=a.maintenance_batch<=200:raise ValueError('维护批次必须为1至200项')
    task=db.one('SELECT * FROM tasks WHERE id=?',(a.semantic_task,))
    if not task:raise ValueError('未找到待续接的语义任务')
    pid=task['project_id'];paid_testing.get(pid,a.allocation)
    if not any(b['task_id']==task['id'] for b in paid_testing.bindings(pid,a.allocation)):
        raise ValueError('只能续接已经绑定本轮额度的语义任务')
    state_path=a.output/'pipeline-progress.json';stop_path=a.output/'pipeline.stop'
    started=db.now();batches=[];final_tasks=[]
    def save(state,phase,message=None):
        grant=paid_testing.report(pid,a.allocation)
        files=db.rows("SELECT status,COUNT(*) n FROM knowledge_build_items WHERE task_id=? AND stage='file' GROUP BY status",(a.semantic_task,))
        items=db.rows('SELECT status,COUNT(*) n FROM knowledge_maintenance_items WHERE project_id=? GROUP BY status',(pid,))
        value={'updated_at':db.now(),'started_at':started,'pid':os.getpid(),'project_id':pid,
               'state':state,'phase':phase,'message':message,'allocation_id':a.allocation,
               'limit':grant['limit'],'currency':grant['currency'],'budget_occupation':grant['used'],
               'semantic_task_id':a.semantic_task,'semantic_file_states':files,'maintenance_states':items,
               'maintenance_task_ids':batches,'final_index_and_query_task_ids':final_tasks,
               'human_acceptance_claimed':False,'stop_file':str(stop_path)}
        temporary=state_path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(state_path)
        return value
    def full_index_and_query():
        """Full current source scope; a limit error is never silent truncation."""
        if stop_path.exists() or paid_testing.closed(pid,a.allocation):
            save('stopped','index');return 1
        save('running','index')
        result=runner['run_phase'](pid,phase_arguments(task,a.allocation,a.output,'index'))
        if result.get('task_id'):final_tasks.append(result['task_id'])
        if result.get('status')!='succeeded':
            save('attention_required','index',result.get('error') or '完整官方索引未成功，未发送后续查询');return 1
        saved=db.one('SELECT result FROM tasks WHERE id=?',(result['task_id'],))
        artifact=db.loads(saved['result'],{}).get('result',{}).get('artifact_id')
        if not artifact:
            save('attention_required','index','索引结果没有产物ID');return 1
        if stop_path.exists() or paid_testing.closed(pid,a.allocation):
            save('stopped','drift');return 1
        save('running','drift')
        drift_args=phase_arguments(task,a.allocation,a.output,'drift')
        drift_args.artifact=artifact
        drift_args.query='基于本次完整文献索引，比较学术凭证认证、去中心化身份与隐私保护方法，说明原文中的验证条件、未验证范围及不能直接互比的结论，并保留来源编号。'
        result=runner['run_phase'](pid,drift_args)
        if result.get('task_id'):final_tasks.append(result['task_id'])
        state='completed' if result.get('status')=='succeeded' else 'attention_required'
        save(state,'drift',result.get('error') or ('语义编译、维护、完整官方索引及DRIFT已实际执行；人工与外部环境验收另计' if state=='completed' else '保留全部结果与费用预留'))
        return int(state!='completed')
    try:
        while True:
            task=db.one('SELECT * FROM tasks WHERE id=?',(a.semantic_task,))
            if task['status'] not in ('queued','running'):break
            if stop_path.exists():
                save('stopped','semantic','当前语义任务保持原状态；本续接器不会安排后续任务');return 1
            if task['status']=='running' and time.time()-float(task.get('heartbeat') or 0)>180:
                save('attention_required','semantic','任务心跳已过期，未猜测远端状态或重发请求');return 1
            save('running','semantic')
            time.sleep(30)
        if not completion_allowed(task):
            save('attention_required','semantic',task.get('error') or '语义任务未完整成功，停止自动进入后续付费阶段');return 1
        verifier=runpy.run_path(str(ROOT/'scripts/verify_full_knowledge_compilation.py'))
        checked=verifier['verify'](pid,task['id'],a.allocation)
        if not checked['compilation_complete'] or checked['foreign_key_errors']:
            runner['write'](a.output/'full-semantic-verification.json',checked)
            save('attention_required','semantic','逐范围完整性验证未通过，未安排后续收费');return 1
        checked['completion_record']=verifier['record_verified'](checked)
        runner['write'](a.output/'full-semantic-verification.json',checked)
        if stop_path.exists():
            save('stopped','maintenance');return 1
        # Each batch creates a separate immutable binding to the same allowance.
        for _ in range(500):
            if stop_path.exists() or paid_testing.closed(pid,a.allocation):
                save('stopped','maintenance');return 1
            save('running','maintenance')
            maintenance_args=phase_arguments(task,a.allocation,a.output,'maintenance')
            maintenance_args.max_items=a.maintenance_batch
            report=runner['run_phase'](pid,maintenance_args)
            if report.get('task_id'):batches.append(report['task_id'])
            finished=db.one('SELECT result FROM tasks WHERE id=?',(report.get('task_id'),))
            outcome=maintenance_outcome(report,db.loads(finished['result'],{}) if finished else {})
            if outcome in ('complete','drained'):
                from app.knowledge_maintenance import status
                current=status(pid,'literature')
                state='completed' if current.get('complete') else 'attention_required'
                if state!='completed':
                    save(state,'maintenance','无可继续执行项，仍需处理待核、冲突或失效记录');return 1
                return full_index_and_query()
            if outcome!='advance':
                save('attention_required','maintenance',report.get('error') or '批次没有继续推进，保留所有结果与费用预留');return 1
        save('attention_required','maintenance','已达到500个维护批次的执行保护上限');return 1
    except BaseException as error:
        save('attention_required','pipeline',security.redact(error));raise


if __name__=='__main__':raise SystemExit(main())

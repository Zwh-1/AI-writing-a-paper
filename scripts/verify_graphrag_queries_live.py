"""Validate distinct query modes against an existing official index, once each."""
import argparse,json,runpy,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,tasks,provider,security,graphrag_adapter
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifact',required=True);parser.add_argument('--budget',type=float,default=.2);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--mode',choices=['global','local','drift'],action='append');parser.add_argument('--connect-attempts',type=int,default=1);args=parser.parse_args();db.init();artifact=db.one('SELECT * FROM knowledge_artifacts WHERE id=?',(args.artifact,));pid=artifact['project_id'];checks=[]
    questions={'global':'这些文献研究了什么问题，结果有什么适用范围限制？','local':'列出这些资料涉及的研究主题及原文明确的限制条件。','drift':'有哪些研究结论的适用范围仍需要进一步核对，请列出问题。'}
    for mode,question in questions.items():
        if args.mode and mode not in args.mode:continue
        with db.transaction():
            task=tasks.enqueue(pid,'knowledge_graphrag_query',{'artifact_id':args.artifact,'mode':mode,'query':question,'budget_limit':args.budget});db.execute("UPDATE tasks SET status='running',attempt=1,heartbeat=?,lease_until=? WHERE id=?",(time.time(),time.time()+120,task['id']))
        done=threading.Event();thread=threading.Thread(target=tasks.heartbeat_task,args=(task['id'],1,done),daemon=True);thread.start()
        try:
            cfg={**provider.config(),'max_attempts':max(1,min(3,args.connect_attempts))}
            with provider.request_scope(cfg,provider.get_key()):result=graphrag_adapter.query(task,db.loads(task['payload'],{}))
            check={'mode':mode,'status':'passed','task_id':task['id'],'result':result};state='succeeded'
        except Exception as error:
            from app.summary_billing import BudgetExceeded
            state='budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed';check={'mode':mode,'status':state,'task_id':task['id'],'error':security.redact(error)}
        finally:done.set();thread.join(timeout=1)
        check['runs']=db.rows('SELECT id,status,cost,reserved FROM generation_runs WHERE task_id=?',(task['id'],));db.execute('UPDATE tasks SET status=?,result=?,updated_at=? WHERE id=?',(state,db.dumps(check),db.now(),task['id']));checks.append(check)
    args.output.write_text(json.dumps({'checks':checks,'human_verification_added':False},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps([{k:v for k,v in c.items() if k not in ('result','runs')} for c in checks],ensure_ascii=False));return 0 if all(c['status']=='passed' for c in checks) else 1
if __name__=='__main__':raise SystemExit(main())

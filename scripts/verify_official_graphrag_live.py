"""Independent bounded official-SDK probe; no retry of unknown earlier calls."""
import argparse,json,runpy,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,tasks,provider,security,graphrag_adapter
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--budget',type=float,default=1);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();db.init();pid=db.one('SELECT id FROM projects WHERE archived=0 ORDER BY created_at')['id']
    with db.transaction():
        task=tasks.enqueue(pid,'knowledge_graphrag_build',{'budget_limit':args.budget});db.execute("UPDATE tasks SET status='running',attempt=1,heartbeat=?,lease_until=? WHERE id=?",(time.time(),time.time()+120,task['id']))
    report={'task_id':task['id'],'checks':[],'human_verification_added':False}
    try:
        result=graphrag_adapter.build(task,{'namespace':'literature','edge_limit':4,'reports':True,'embeddings':True});report['checks'].append({'name':'official_reports_embeddings','result':result})
        for mode in ('global','local','drift'):
            result_query=graphrag_adapter.query(task,{'artifact_id':result['artifact_id'],'mode':mode,'query':'这些文献研究了什么问题，结果有什么适用范围限制？'});report['checks'].append({'name':mode,'result':result_query})
        report['status']='passed'
    except Exception as error:
        from app.summary_billing import BudgetExceeded
        report.update(status='budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed',error=security.redact(error))
    report['runs']=db.rows('SELECT id,status,usage,cost,reserved FROM generation_runs WHERE task_id=?',(task['id'],));db.execute('UPDATE tasks SET status=?,result=?,error=?,updated_at=? WHERE id=?',('succeeded' if report['status']=='passed' else report['status'],db.dumps(report),report.get('error'),db.now(),task['id']))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':report['status'],'checks':[r['name'] for r in report['checks']],'error':report.get('error'),'runs':len(report['runs'])},ensure_ascii=False));return 0 if report['status']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())

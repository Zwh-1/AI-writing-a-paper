"""Incrementally initialize the real library; never fabricates human reviews."""
import argparse
import json
import runpy
import sqlite3
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,knowledge_builds,knowledge_comparisons

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project');parser.add_argument('--scope',default='literature',choices=['all','literature','guidance','project']);parser.add_argument('--mode',default='literal',choices=['literal','llm']);parser.add_argument('--budget',type=float,default=0);parser.add_argument('--file',action='append',default=[]);parser.add_argument('--enable-writing',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args();db.init();projects=db.rows('SELECT id FROM projects WHERE archived=0')
    pid=args.project or (projects[0]['id'] if len(projects)==1 else None)
    if not pid:raise ValueError('请选择一个项目')
    payload={'namespace':args.scope,'mode':args.mode,'budget_limit':args.budget,'file_ids':args.file}
    with db.transaction():
        task=knowledge_builds.start(pid,payload)
        if task['status'] not in ('queued','failed','incomplete','budget_blocked'):raise ValueError('该范围已有任务正在处理，请在任务中心续接')
        db.execute("UPDATE tasks SET status='running',attempt=attempt+1,heartbeat=?,lease_until=? WHERE id=?",(time.time(),time.time()+120,task['id']))
    started=time.perf_counter()
    try:
        result=knowledge_builds.process(task,db.loads(task['payload'],{}));state='succeeded' if result['complete'] else 'incomplete'
        db.execute('UPDATE tasks SET status=?,result=?,progress=100,lease_until=NULL,updated_at=? WHERE id=?',(state,db.dumps(result),db.now(),task['id']))
        knowledge_comparisons.seed(pid)
        if args.enable_writing:db.set_setting('graph_wiki_enabled:'+pid,True)
        report={'task_id':task['id'],'status':state,'elapsed_seconds':time.perf_counter()-started,'result':result,'human_records_added':False}
    except BaseException as error:
        from app import provider
        from app.summary_billing import BudgetExceeded
        state='cancelled' if isinstance(error,InterruptedError) else 'budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed'
        db.execute('UPDATE tasks SET status=?,error=?,lease_until=NULL,updated_at=? WHERE id=?',(state,str(error),db.now(),task['id']));raise
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'task_id':task['id'],'status':state,'files':len(result['files']),'failures':result['failures'],'elapsed_seconds':report['elapsed_seconds']},ensure_ascii=False))
    return 0 if state=='succeeded' else 1

if __name__=='__main__':raise SystemExit(main())

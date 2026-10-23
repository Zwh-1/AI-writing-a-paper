"""Resume the original failed PPT scope with a bounded, primary-only automatic job."""
import argparse,json,pathlib,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,tasks,rule_ai,summary_billing
parser=argparse.ArgumentParser();parser.add_argument('--submit',action='store_true');args=parser.parse_args()
state=root/'.runtime/rules-auto-extraction-task.json'
if args.submit and not state.exists():
    old=db.one("SELECT * FROM tasks WHERE kind='rules_ai' AND error LIKE '上下文超过%' ORDER BY created_at DESC LIMIT 1")
    if not old:raise RuntimeError('Original failed source scope was not found')
    previous=db.loads(old['payload']);file_ids=previous['file_ids'];sources=rule_ai.source_scope(old['project_id'],file_ids)
    if len(sources)!=84:raise RuntimeError('The original 84-source scope changed; no request submitted')
    if summary_billing.budget(old['project_id'])['available']<3:raise RuntimeError('Insufficient existing project budget')
    payload={'automatic':True,'file_ids':file_ids,'source_ids':[s['id'] for s in sources],'source_limit':len(sources),'source_offset':0,'budget_limit':3,'gpt_budget':0,'allow_gpt':False,'auto_publish':True,'resume_task_id':old['id']}
    task=tasks.enqueue(old['project_id'],'rules_ai',payload)
    state.write_text(json.dumps({'task_id':task['id'],'project_id':old['project_id'],'budget_limit':3}),encoding='utf-8')
if not state.exists():raise SystemExit('Use --submit once to run the original failed material.')
saved=json.loads(state.read_text(encoding='utf-8'));task=db.one('SELECT * FROM tasks WHERE id=?',(saved['task_id'],));result=db.loads(task['result'],{}) or {}
out={**saved,'status':task['status'],'phase':task['phase'],'progress':task['progress'],'error':task['error'],'result':{k:v for k,v in result.items() if k not in ('raw_text','manifest')},'budget':summary_billing.budget(saved['project_id'])}
print(json.dumps(out,ensure_ascii=False))
if task['status'] not in ('queued','running'):(root/'docs/rules-auto-live-extraction-report.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')

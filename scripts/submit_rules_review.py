"""Submit one bounded automatic review of existing candidates to the local worker."""
import argparse,json,pathlib,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,tasks,rule_ai,summary_billing

parser=argparse.ArgumentParser();parser.add_argument('--submit',action='store_true');args=parser.parse_args()
state=root/'.runtime/rules-auto-live-task.json'
if args.submit and not state.exists():
    pid=db.one('SELECT id FROM projects WHERE archived=0 ORDER BY created_at LIMIT 1')['id']
    budget=summary_billing.budget(pid)
    if budget['available']<5:raise RuntimeError('Insufficient existing project budget for the bounded review')
    sources=rule_ai.source_scope(pid)
    payload={'automatic':True,'review_existing_only':True,'source_ids':[s['id'] for s in sources],'file_ids':[],'source_limit':len(sources),'source_offset':0,'budget_limit':5,'allow_gpt':True,'gpt_budget':3,'auto_publish':True}
    task=tasks.enqueue(pid,'rules_ai',payload)
    state.write_text(json.dumps({'task_id':task['id'],'project_id':pid,'budget_limit':5,'gpt_budget':3}),encoding='utf-8')
if not state.exists():raise SystemExit('No review submitted; use --submit once.')
saved=json.loads(state.read_text(encoding='utf-8'));task=db.one('SELECT * FROM tasks WHERE id=?',(saved['task_id'],))
result=db.loads(task['result'],{}) or {}
output={**saved,'status':task['status'],'phase':task['phase'],'progress':task['progress'],'review':result.get('review'),'rule_package':result.get('rule_package'),'error':task['error'],'budget':summary_billing.budget(saved['project_id'])}
print(json.dumps(output,ensure_ascii=False))
if task['status'] not in ('queued','running'):(root/'docs/rules-auto-live-review-report.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')

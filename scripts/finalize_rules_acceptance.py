"""Finalize saved acceptance bills; this command never generates model output."""
import json,pathlib,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,summary_billing
from app.rule_pipeline import finalize_billing
results=[]
for name in ('rules-auto-live-task.json','rules-auto-extraction-task.json'):
    path=root/'.runtime'/name
    if not path.exists():continue
    item=json.loads(path.read_text());task=db.one('SELECT * FROM tasks WHERE id=?',(item['task_id'],))
    if task['status'] in ('running','queued'):raise RuntimeError('Acceptance task is still active; no final report created')
    billing=finalize_billing(task);result=db.loads(task['result'],{}) or {}
    result['billing']=billing;result['budget']=summary_billing.budget(task['project_id'])
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps(result),task['id']))
    results.append({'task_id':task['id'],'billing':billing,'budget':result['budget']})
print(json.dumps(results,ensure_ascii=False,indent=2))

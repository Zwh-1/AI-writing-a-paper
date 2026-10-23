"""Hold only an idle acceptance task before a safe worker reload."""
import json,pathlib,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db
state=json.loads((root/'.runtime/rules-auto-extraction-task.json').read_text());tid=state['task_id'];mode=sys.argv[1]
with db.transaction():
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    if mode=='hold':
        if task['status']=='running' or db.one("SELECT g.id FROM generation_runs g JOIN tasks t ON t.id=g.task_id WHERE t.status='running' AND g.status IN ('pending','sent') LIMIT 1"):
            print('An active request is still running; no state changed.');raise SystemExit(2)
        if task['status'] in ('queued','budget_blocked','incomplete'):
            db.execute("UPDATE tasks SET status='cancelled',phase='已保存检查点，正在更新对账逻辑',updated_at=? WHERE id=?",(db.now(),tid))
            (root/'.runtime/rules-auto-held.json').write_text(json.dumps({'task_id':tid,'previous_status':task['status']}),encoding='utf-8')
        print(json.dumps({'task_id':tid,'safe_to_reload':True,'prior_status':task['status']}))
    elif mode=='resume':
        held=root/'.runtime/rules-auto-held.json'
        if held.exists() and task['status']=='cancelled':db.execute("UPDATE tasks SET status='queued',cancel_requested=0,error=NULL,updated_at=? WHERE id=?",(db.now(),tid))
        print(json.dumps({'task_id':tid,'status':db.one('SELECT status FROM tasks WHERE id=?',(tid,))['status']}))

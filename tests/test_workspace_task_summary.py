from test_workbench import client,project
from app import db,tasks,rule_review_receipts


def test_task_status_summary_skips_large_results_and_receipt_computation(client,monkeypatch):
    pid=project(client);task=tasks.enqueue(pid,'rules_ai',{'raw':'x'*10000})
    db.execute('UPDATE tasks SET result=?,status=?,phase=?,progress=42 WHERE id=?',(db.dumps({'raw':'y'*20000}),'running','正在整理',task['id']))
    def forbidden(_):raise AssertionError('Summary polling must not compute full receipts')
    monkeypatch.setattr(rule_review_receipts,'receipt',forbidden)
    response=client.get('/api/v1/tasks',params={'project_id':pid,'summary':'true'})
    assert response.status_code==200
    row=response.json()[0]
    assert row['id']==task['id'] and row['status']=='running' and row['progress']==42
    assert not {'payload','result','review_receipt'}&row.keys()
    assert len(response.content)<1000
    db.execute("UPDATE tasks SET status='succeeded',progress=100 WHERE id=?",(task['id'],))
    assert client.get('/api/v1/tasks',params={'project_id':pid,'summary':'true'}).json()[0]['status']=='succeeded'


def test_task_summary_keeps_project_scope_and_full_detail_contract(client,monkeypatch):
    pid=project(client);other=project(client,'其他论文')
    task=tasks.enqueue(pid,'rules_ai',{'version':'current'})
    tasks.enqueue(other,'rules_ai',{})
    monkeypatch.setattr(rule_review_receipts,'receipt',lambda t:{'calls':0,'task_id':t['id']})
    summary=client.get('/api/v1/tasks',params={'project_id':pid,'summary':'true'}).json()
    assert len(summary)==1 and summary[0]['id']==task['id']
    full=client.get('/api/v1/tasks',params={'project_id':pid}).json()[0]
    assert db.loads(full['payload'])=={'version':'current'} and full['review_receipt']['task_id']==task['id']
    detail=client.get('/api/v1/tasks/'+task['id']).json()
    assert detail['payload']=={'version':'current'} and detail['review_receipt']['task_id']==task['id']

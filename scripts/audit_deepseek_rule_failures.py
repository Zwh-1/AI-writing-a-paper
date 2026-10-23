"""Read saved rule-task failures without contacting any model or billing API."""
import collections,json,pathlib,sqlite3,sys,zipfile
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,provider

tag=db.now().replace(':','').replace('-','').split('.')[0]
folder=root/'data/backups'/('deepseek-review-'+tag);folder.mkdir(parents=True,exist_ok=True)
src=sqlite3.connect(str(db.DB_PATH));dst=sqlite3.connect(folder/'workbench.sqlite3')
try:src.backup(dst)
finally:src.close();dst.close()
with zipfile.ZipFile(folder/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for name in ('backend/app/rule_verification.py','backend/app/rule_pipeline.py','backend/app/tasks.py','frontend/src/Completion.tsx'):
        archive.write(root/name,name)
failures=[]
for task in db.rows("SELECT * FROM tasks WHERE kind='rules_ai' ORDER BY created_at"):
    runs=db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at',(task['id'],))
    if task['status']=='succeeded' or not any('deepseek' in r['model'].lower() for r in runs):continue
    details=[]
    for run in runs:
        meta=db.loads(run['manifest'],{});usage=db.loads(run['usage'],{}) or {};raw=usage.get('raw',{}) or {}
        details.append({'id':run['id'],'model':run['model'],'status':run['status'],'error':run['error'],'input_tokens':usage.get('input_tokens'),'output_tokens':usage.get('output_tokens'),'reasoning_tokens':(raw.get('completion_tokens_details') or {}).get('reasoning_tokens'),'thinking_enabled':meta.get('provider',{}).get('thinking_enabled'),'source_count':len(meta.get('source_ids',[]))})
    failures.append({'task_id':task['id'],'created_at':task['created_at'],'status':task['status'],'error':provider.readable_error(task['error']),'requests':details})
report={'generated_at':db.now(),'backup':str(folder),'baseline_generation_ids':[r['id'] for r in db.rows('SELECT id FROM generation_runs ORDER BY id')],'new_generation_calls':0,'failures':failures}
(root/'docs/deepseek-review-failure-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'backup':str(folder),'failed_rule_tasks':len(failures),'reasoning_exhausted_requests':sum(r['output_tokens']==r['reasoning_tokens'] and bool(r['output_tokens']) for t in failures for r in t['requests'])},ensure_ascii=False))

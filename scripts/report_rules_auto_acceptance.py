"""Assemble local acceptance evidence without network access or generation."""
import collections,json,pathlib,sqlite3,sys,xml.etree.ElementTree as ET
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,summary_billing,rule_workspace,rule_ai

def local_rows(connection,query,args=()):return [dict(r) for r in connection.execute(query,args)]
initial=root/'data/backups/rules-auto-20261022-222445/workbench.sqlite3'
original=sqlite3.connect(initial.as_uri()+'?mode=ro',uri=True);original.row_factory=sqlite3.Row
pid=db.one('SELECT id FROM projects WHERE archived=0 ORDER BY created_at LIMIT 1')['id']
before=local_rows(original,'SELECT * FROM rules WHERE project_id=?',(pid,));after={r['id']:r for r in db.rows('SELECT * FROM rules WHERE project_id=?',(pid,))}
human=[r for r in before if r['state']=='confirmed'];human_ok=all(all(after[r['id']].get(k)==v for k,v in r.items()) for r in human)
jobs=[];new_runs=[]
for name in ('rules-auto-live-task.json','rules-auto-extraction-task.json'):
    path=root/'.runtime'/name
    if not path.exists():continue
    item=json.loads(path.read_text());task=db.one('SELECT * FROM tasks WHERE id=?',(item['task_id'],));result=db.loads(task['result'],{}) or {}
    calls=db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at',(task['id'],));new_runs.extend(calls)
    jobs.append({'task_id':task['id'],'status':task['status'],'automatic_review_only':db.loads(task['payload'],{}).get('review_existing_only',False),'processed_source_units':result.get('processed_source_units'),'selected_source_units':result.get('selected_source_units'),'completed_batches':result.get('completed_batches'),'total_batches':result.get('total_batches'),'reused_batches':result.get('reused_batches'),'review':result.get('review'),'calls':len(calls),'new_calls_by_model':dict(collections.Counter(r['model'] for r in calls)),'call_statuses':dict(collections.Counter(r['status'] for r in calls)),'max_request_characters':max((db.loads(r['manifest'],{}).get('request_characters',0) for r in calls),default=0)})
charges=[];run_ids={r['id'] for r in new_runs}
for group in db.rows('SELECT * FROM generation_billing_groups WHERE project_id=?',(pid,)):
    old=original.execute('SELECT amount,evidence FROM generation_billing_groups WHERE project_id=? AND base_url=? AND credential_hash=? AND model=?',(pid,group['base_url'],group['credential_hash'],group['model'])).fetchone()
    old_ids=set(json.loads(old['evidence']).get('run_ids',[])) if old else set()
    current_ids=set(db.loads(group['evidence'],{}).get('run_ids',[]));delta_ids=current_ids-old_ids
    associated={r['id'] for r in new_runs if r['model']==group['model']}
    if associated:
        charges.append({'model':group['model'],'currency':group['currency'],'prior_reconciled_amount':old['amount'] if old else 0,'current_reconciled_amount':group['amount'],'increment':round(group['amount']-(old['amount'] if old else 0),9),'new_group_members_exactly_match_acceptance_calls':delta_ids==associated,'all_acceptance_calls_reconciled':associated.issubset(current_ids)})
pending=[]
decisions=rule_workspace.entries(pid)
for r in decisions:
    if r['state']=='candidate':pending.append({'id':r['id'],'statement':r['statement'],'source_file':r['source_file'],'page':r['page_number'],'decision':r.get('machine_review')})
tests={}
for name in ('rules-auto-release-final.xml','rules-auto-final-targeted.xml','rules-auto-cache-final.xml','rules-auto-semantic-last.xml','rules-auto-billing-completion.xml','rules-auto-budget-route-last.xml'):
    path=root/'docs'/name
    if path.exists():
        tree=ET.parse(path);suites=list(tree.iter('testsuite'))
        tests[name]={k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
browser_path=root/'docs/rules-auto-browser-report.json'
package=db.one('SELECT p.* FROM rule_packages p JOIN projects j ON j.rule_package_id=p.id WHERE j.id=?',(pid,))
report={'version':'0.3.6','project_id':pid,'source_units_available':len(rule_ai.source_scope(pid)),'rules_before':len(before),'rules_now':len(after),'original_rule_ids_retained':all(r['id'] in after for r in before),'original_human_confirmed_rows_preserved':human_ok,'original_candidate_text_changes_with_audit':sum(r['statement']!=after[r['id']]['statement'] for r in before),'current_state_counts':dict(collections.Counter(r['state'] for r in after.values())),'published_package':{'version':package['version'],'rule_count':len(db.loads(package['rules'],[]))} if package else None,'budget':summary_billing.budget(pid),'acceptance_jobs':jobs,'total_new_calls':len(new_runs),'gpt_calls':sum('gpt' in r['model'].lower() for r in new_runs),'new_reconciled_billing':charges,'remaining_isolated_rules':pending,'tests':tests,'browser':json.loads(browser_path.read_text()) if browser_path.exists() else None,'initial_backup':str(initial)}
original.close();path=root/'docs/rules-auto-acceptance-report.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))

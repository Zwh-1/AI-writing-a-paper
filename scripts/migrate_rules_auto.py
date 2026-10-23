"""Back up, reconcile known bills, and review existing rules without generation."""
import argparse,datetime,hashlib,json,pathlib,sqlite3,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,config,rule_governance,summary_billing,model_routing,provider,rules,rule_workspace


def digest(rows):return hashlib.sha256(db.dumps(rows).encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');options=parser.parse_args()
    if not options.apply:print('Use --apply to back up, reconcile and review existing project rules. No model generation is performed.');return
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    backup=config.DATA/'backups'/('rule-automation-migration-'+stamp+'.sqlite3')
    source=sqlite3.connect(str(db.DB_PATH));target=sqlite3.connect(backup)
    try:source.backup(target)
    finally:target.close();source.close()
    db.init();report={'backup':str(backup),'model_generation_calls':0,'projects':[]}
    for project in db.rows('SELECT id FROM projects WHERE archived=0'):
        pid=project['id'];before=db.rows('SELECT id,statement FROM rules WHERE project_id=? ORDER BY id',(pid,));human=db.rows("SELECT * FROM rules WHERE project_id=? AND state='confirmed' ORDER BY id",(pid,))
        budget_before=summary_billing.budget(pid);billing=[]
        for profile in ('material-summary','gpt-fallback'):
            if profile=='gpt-fallback' and not model_routing.ready():continue
            try:
                result=summary_billing.reconcile(pid,profile=profile)
                billing.append({'profile':profile,'reconciled':result.get('reconciled'), 'reason':result.get('reason'),'amount':result.get('amount')})
            except Exception as error:billing.append({'profile':profile,'reconciled':False,'error':provider.redact(error)})
        review=rule_governance.review_project(pid);status=rule_workspace.diagnostics(pid)
        package=rules.publish(pid) if status['can_publish'] else None
        after=db.rows('SELECT id,statement FROM rules WHERE project_id=? ORDER BY id',(pid,))
        preserved=all(db.one('SELECT * FROM rules WHERE id=?',(row['id'],))==row for row in human)
        entry={'project_id':pid,'budget_before':budget_before,'billing':billing,'budget_after':summary_billing.budget(pid),'review':review,'original_statements_preserved':digest(before)==digest(after),'human_confirmed_rows_preserved':preserved,'counts':status['counts'],'rule_package':package,'publication_blockers':status['blockers']}
        report['projects'].append(entry)
        if not preserved or digest(before)!=digest(after):raise RuntimeError('Migration preservation check failed; backup retained at '+str(backup))
    out=root/'docs/rules-auto-migration-report.json'
    if out.exists():
        previous=json.loads(out.read_text(encoding='utf-8'));history=previous.pop('previous_runs',[])
        report['previous_runs']=[*history,previous][-5:]
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

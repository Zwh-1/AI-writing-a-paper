"""Trace all saved rule statements to full local source context; no model calls."""
import argparse,collections,json,pathlib,sqlite3,sys
root=pathlib.Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend')]
from app import db,rule_text_integrity as integrity,rule_text_repair as repair

parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
baseline=json.loads((root/'.runtime/rule-text-integrity-baseline.json').read_text(encoding='utf-8'))
if args.apply and db.one("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1"):raise RuntimeError('存在执行中任务，暂不修改规则记录')
old=sqlite3.connect(pathlib.Path(baseline['backup'])/'workbench.sqlite3');old.row_factory=sqlite3.Row
findings=[];results=[]
for project in db.rows('SELECT id FROM projects'):
    selected=repair.scan(project['id'])
    for finding in selected:
        rule=finding['rule'];source=finding['source'];info=finding['info']
        item={k:rule[k] for k in ('id','project_id','state','statement','source_id')}
        item.update({'source_file':source['source_file'],'page':source['page_number'],'source_method':source['method'],'issue':info['issue'],'replacement':info['replacement'],'context':info['context'],'safe_to_repair':finding['safe_to_repair'],'safe_to_quarantine':finding['safe_to_quarantine'],'repaired':False})
        findings.append(item)
    if args.apply:
        value=repair.apply(project['id'],selected);results.append(value)
        for item in findings:
            if item['id'] in value['repaired_rule_ids']:item['repaired']=True
report={'generated_at':db.now(),'apply':args.apply,'backup':baseline['backup'],'checked_rules':db.one('SELECT COUNT(*) n FROM rules')['n'],'findings':findings,'finding_states':dict(collections.Counter(x['state'] for x in findings)),'safe_repair_count':sum(x['safe_to_repair'] for x in findings),'results':results,'new_generation_calls':len({x['id'] for x in db.rows('SELECT id FROM generation_runs')}-set(baseline['generation_ids']))}
(root/'docs/rule-text-integrity-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checked_rules':report['checked_rules'],'findings':len(findings),'states':report['finding_states'],'safe_repair_count':report['safe_repair_count'],'samples':[{'id':x['id'],'state':x['state'],'old':x['statement'],'new':x['replacement']} for x in findings[:16]]},ensure_ascii=False,indent=2))

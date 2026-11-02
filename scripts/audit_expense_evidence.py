"""Read-only expense evidence export; optional free account observations.

No model calls, budget changes or retrospective charge fabrication.
"""
import argparse
import csv
import json
import runpy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,expense_evidence as expense


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project')
    parser.add_argument('--observe-accounts',action='store_true')
    args=parser.parse_args()
    db.init()
    projects=db.rows('SELECT id,title,budget FROM projects WHERE id=?',(args.project,)) if args.project else db.rows('SELECT id,title,budget FROM projects')
    args.output.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for p in projects:
        if args.observe_accounts:expense.observe_accounts(p['id'])
        data=expense.ledger(p['id'])
        data['evidence_records']=[{**r,'content':db.loads(r['content'],{})} for r in db.rows('SELECT * FROM expense_evidence WHERE project_id=? ORDER BY rowid',(p['id'],))]
        (args.output/(p['id']+'-ledger.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        fields=['id','task_id','task_kind','model','created_at','status','kind','currency','amount','reserved','budget_occupation','input_tokens','output_tokens','remote_id','client_request_id','provider_request_id','base_url','group_id','gaps','basis']
        for filename,rows in [('all-expenses.csv',data['runs']),('missing-evidence.csv',[r for r in data['runs'] if r['gaps']])]:
            with (args.output/(p['id']+'-'+filename)).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
                for row in rows:
                    values={k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items() if k in fields}
                    # Spreadsheet formula escaping applies to all text cells.
                    writer.writerow({k:"'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for k,v in values.items()})
        summaries.append({'project_id':p['id'],'title':p['title'],'budget':p['budget'],'counts':data['counts'],'by_currency':data['by_currency'],'gap_count':data['gap_count'],'all_actual_costs_verified':data['all_actual_costs_verified']})
    (args.output/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Correct only evidenced ConnectTimeout records from named knowledge probes.

Does not infer model token charges or release read/write timeout reservations.
"""
import argparse,json,re,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db

def task_ids(value):
    result=set()
    if isinstance(value,dict):
        if isinstance(value.get('task_id'),str) and re.fullmatch('[a-f0-9]{32}',value['task_id']):result.add(value['task_id'])
        for v in value.values():result.update(task_ids(v))
    elif isinstance(value,list):
        for v in value:result.update(task_ids(v))
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('reports',type=Path,nargs='+');parser.add_argument('--apply',action='store_true');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();ids=set()
    for report in args.reports:ids.update(task_ids(json.loads(report.read_text(encoding='utf-8'))))
    changes=[]
    with db.transaction():
        for tid in ids:
            for row in db.rows("SELECT * FROM generation_runs WHERE task_id=? AND status='outcome_unknown' AND remote_id IS NULL AND cost IS NULL",(tid,)):
                manifest=db.loads(row['manifest'],{});error=row['error'] or ''
                if not manifest.get('knowledge_operation') or not re.fullmatch('请求已提交但未取得最终响应，请先核实账单或任务状态，再决定重试。ConnectTimeout',error):continue
                change={'id':row['id'],'task_id':tid,'before_status':row['status'],'before_error':error,'before_manifest':manifest,'reserved':row['reserved'],'reason':'HTTPX ConnectTimeout occurs while establishing a host connection, before the model HTTP request body is sent','authority':'https://www.python-httpx.org/exceptions/'};changes.append(change)
                if args.apply:
                    manifest={**manifest,'transport_failure':{'phase':'connect_before_http','exception':'ConnectTimeout','http_request_sent':False,'cost_not_inferred':True,'corrected_from':'outcome_unknown','original_error':error}}
                    db.execute("UPDATE generation_runs SET status='failed_before_send',manifest=? WHERE id=? AND status='outcome_unknown'",(db.dumps(manifest),row['id']))
                    db.audit(row['project_id'],'knowledge_transport_phase_corrected',{'run_id':row['id'],'task_id':tid,'original_status':'outcome_unknown','exception':'ConnectTimeout','scope':'named_knowledge_probe_only','cost_not_inferred':True})
            if args.apply and not db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown')",(tid,)):
                db.execute("UPDATE tasks SET status='incomplete',error='本次可证实的连接前超时已更正，已保存完成部分；未自动重复模型请求' WHERE id=? AND status='outcome_unknown'",(tid,))
    result={'applied':args.apply,'records':changes,'legacy_unrelated_records_changed':False,'actual_model_fees_inferred':False};args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'applied':args.apply,'corrected':len(changes),'reserves_excluded_for_presend_failure':sum(c['reserved'] for c in changes)}))
if __name__=='__main__':main()

"""Bounded real-provider smoke test; records machine results, never human gold."""
import argparse
import json
import runpy
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,tasks,provider,security,knowledge_lineage as lineage,knowledge_compiler as compiler,knowledge_graph as graph,knowledge_wiki as wiki

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--budget',type=float,default=0.3);parser.add_argument('--official',action='store_true');parser.add_argument('--resume-report',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();db.init()
    pid=db.one('SELECT id FROM projects WHERE archived=0 ORDER BY created_at')['id'];report={'project_id':pid,'human_verification_added':False,'checks':[]}
    original=db.one("SELECT u.id AS unit_id,u.text,p.file_id,f.name FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id JOIN knowledge_parses p ON p.id=u.parse_id JOIN files f ON f.id=p.file_id WHERE f.project_id=? AND f.role='research' AND f.active=1 AND u.review_state!='excluded' AND length(u.text) BETWEEN 500 AND 1800 AND u.text LIKE '%研究%' ORDER BY length(u.text),u.id LIMIT 1",(pid,))
    if not original:raise ValueError('没有找到适合小范围验证的研究原文')
    previous=json.loads(args.resume_report.read_text(encoding='utf-8')) if args.resume_report else None
    previous_runs=db.rows("SELECT * FROM generation_runs WHERE task_id=? AND status='completed' ORDER BY created_at",(previous['task_id'],)) if previous else []
    with db.transaction():
        task=tasks.enqueue(pid,'knowledge_live_probe',{'budget_limit':args.budget,'budget_scope_id':previous['task_id']} if previous else {'budget_limit':args.budget});db.execute("UPDATE tasks SET status='running',attempt=1,heartbeat=?,lease_until=? WHERE id=?",(time.time(),time.time()+120,task['id']))
    report['task_id']=task['id']
    try:
        obs=compiler.observe_unit(pid,original['unit_id'],remote=True);refs=[lineage.ref(pid,'observation',obs['id'],True)]
        entity=graph.entity(pid,{'namespace':'literature','kind':'document','key':'file:'+original['file_id'],'name':original['name'],'sources':refs,'method':'source_identity'})
        if previous_runs:
            raw=security.safe_object(db.loads(previous_runs[0]['manifest'],{})['output_blob']).read_text(encoding='utf-8');candidates=provider.parse_json(raw)['claims'];runs=[previous_runs[0]['id']]
        else:candidates,runs=compiler.extracted(task,obs,'llm')
        adopted=compiler.adopt_candidates(pid,obs,candidates,entity,'llm')
        report['checks'].append({'name':'real_model_structured_extraction','claims':len(adopted['assertion_ids']),'run_ids':runs})
        if not adopted['assertion_ids']:raise ValueError('模型未提取可校验论断')
        from app.knowledge_wiki_checks import assess_with_model
        if len(previous_runs)>1:
            from app.knowledge_wiki_checks import adopt_model_assessment
            raw=security.safe_object(db.loads(previous_runs[1]['manifest'],{})['output_blob']).read_text(encoding='utf-8');checked={'assessments':[adopt_model_assessment(pid,adopted['assertion_ids'][0],obs,provider.parse_json(raw),previous_runs[1]['id'])]}
        else:checked=assess_with_model(task,{'claim_ids':adopted['assertion_ids'][:1]})
        report['checks'].append({'name':'real_model_support_assessment','assessments':checked['assessments']})
        sources=[lineage.ref(pid,'assertion',cid,True) for cid in adopted['assertion_ids'][:3]]
        result=compiler.synthesize(task,{'namespace':'literature','kind':'topic','slug':'ai-pilot-'+original['file_id'],'title':'模型试编译：'+original['name'],'sources':sources})
        report['checks'].append({'name':'real_model_wiki_synthesis','page_id':result['page_id'],'version_id':result['version_id'],'run_id':result['run_id'],'published':False})
        if args.official:
            from app import graphrag_adapter
            official=graphrag_adapter.build(task,{'namespace':'literature','edge_limit':4,'reports':True,'embeddings':True})
            report['checks'].append({'name':'official_reports_and_embeddings','result':official})
            for mode in ('global','local','drift'):
                result=graphrag_adapter.query(task,{'artifact_id':official['artifact_id'],'mode':mode,'query':'这些材料报告了哪些研究内容和适用条件？'})
                report['checks'].append({'name':'official_'+mode,'result':result})
        report['status']='passed';db.execute("UPDATE tasks SET status='succeeded',result=?,progress=100,updated_at=? WHERE id=?",(db.dumps(report),db.now(),task['id']))
    except Exception as error:
        from app.summary_billing import BudgetExceeded
        report.update(status='budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed',error=security.redact(error))
        db.execute('UPDATE tasks SET status=?,result=?,error=?,updated_at=? WHERE id=?',(report['status'],db.dumps(report),report['error'],db.now(),task['id']))
    report['runs']=db.rows('SELECT id,status,usage,cost,reserved FROM generation_runs WHERE task_id=?',(task['id'],))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':report['status'],'checks':[c['name'] for c in report['checks']],'error':report.get('error'),'runs':len(report['runs'])},ensure_ascii=False))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())

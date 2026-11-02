"""Check complete source coverage and traceability without model generation."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,knowledge_lineage as lineage,knowledge_compiler as compiler,paid_testing


def verify(pid,task_id,allocation_id,baseline=None):
    task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(task_id,pid))
    if not task:raise ValueError('目标编译任务不存在')
    payload=db.loads(task['payload'],{});out=db.loads(task.get('result'),{}).get('result',{})
    plan=payload.get('planned_files',[]);files={r['file_id']:r for r in out.get('files',[])}
    from app.material_policy import research_sql
    current_files={r['id'] for r in db.rows('SELECT f.id FROM files f WHERE f.project_id=? AND f.active=1 AND '+research_sql(),(pid,))}
    full_scope={r['id'] for r in plan}==current_files and len(plan)==len(current_files)
    checks=[];problems=[];all_runs=set();no_claims=[]
    with lineage.validation_scope():
        for source in plan:
            original,head,stamp=lineage.file_stamp(pid,source['id'])
            rows=db.rows("SELECT id,text,content_hash FROM knowledge_units WHERE parse_id=? AND review_state!='excluded' ORDER BY ordinal",(head['id'],))
            by_unit={r['id']:r for r in rows};expected={(r['id'],a,b) for r in rows for a,b in compiler.ranges(r['text']) if r['text'][a:b].strip()}
            compiled=files.get(source['id']);found=set();errors=[]
            if source['source_version']!=stamp:errors.append('source_changed_after_plan')
            for oid in compiled.get('observation_ids',[]) if compiled else []:
                errors_before=len(errors)
                obs=lineage.owned(pid,'observation',oid);meta=db.loads(obs['metadata_json'],{});key=(obs['unit_id'],meta.get('char_start'),meta.get('char_end'))
                unit=by_unit.get(obs['unit_id'])
                if (key not in expected or obs['source_version']!=stamp or not unit or obs['text']!=unit['text'][key[1]:key[2]]):
                    errors.append('observation_source_or_range_mismatch:'+oid);continue
                checkpoint=db.one("SELECT * FROM knowledge_build_items WHERE project_id=? AND item_key=? AND stage='extract' AND status='completed' ORDER BY updated_at DESC LIMIT 1",(pid,'observation:'+oid))
                saved=db.loads(checkpoint['result_json'],{}) if checkpoint else {}
                run_ids=saved.get('run_ids',[])
                if not run_ids:errors.append('missing_completed_model_response:'+oid);continue
                for rid in run_ids:
                    run=db.one('SELECT * FROM generation_runs WHERE id=? AND project_id=?',(rid,pid))
                    manifest=db.loads(run['manifest'],{}) if run else {}
                    if not run or run['status']!='completed' or not manifest.get('output_blob'):
                        errors.append('model_response_not_complete:'+rid);continue
                    if not any(r['type']=='observation' and r['id']==oid for r in manifest.get('knowledge_refs',[])):
                        errors.append('model_response_not_bound_to_original:'+rid)
                    all_runs.add(rid)
                for candidate in saved.get('candidates',[]):
                    if not candidate.get('quote') or candidate['quote'] not in obs['text']:errors.append('stored_quote_not_verbatim:'+oid)
                if not saved.get('candidates'):
                    no_claims.append({'file_id':source['id'],'observation_id':oid,'model_reason':saved.get('no_claim_reason','legacy_empty_model_candidates')})
                lineage.version_of(pid,'observation',oid)
                for cid in saved.get('assertion_ids',[]):lineage.version_of(pid,'assertion',cid)
                if len(errors)==errors_before:found.add(key)
            if compiled:
                for page_id in compiled.get('page_ids',[]):lineage.version_of(pid,'wiki_page',page_id)
                if not compiled.get('page_ids'):errors.append('no_document_wiki_page')
            complete=bool(compiled) and found==expected and not errors
            record={'file_id':source['id'],'name':source['name'],'source_current':source['source_version']==stamp,
                'required_ranges':len(expected),'model_processed_ranges':len(found),'page_ids':compiled.get('page_ids',[]) if compiled else [],
                'assertions':len(compiled.get('assertion_ids',[])) if compiled else 0,'complete':complete,'errors':errors}
            checks.append(record)
            if not complete:problems.append(source['id'])
    old_unchanged=None
    if baseline:
        connection=sqlite3.connect(baseline);connection.row_factory=sqlite3.Row
        try:
            current={r['id']:r for r in db.rows('SELECT * FROM generation_runs')}
            old_unchanged=all(current.get(r['id'])==dict(r) for r in connection.execute('SELECT * FROM generation_runs'))
        finally:connection.close()
    grant=paid_testing.report(pid,allocation_id)
    return {'verified_at':db.now(),'task_id':task_id,'task_status':task['status'],'project_id':pid,
        'source_file_count':len(plan),'current_research_file_count':len(current_files),'planned_scope_covers_current_research_files':full_scope,
        'complete_files':sum(c['complete'] for c in checks),
        'required_ranges':sum(c['required_ranges'] for c in checks),'model_processed_ranges':sum(c['model_processed_ranges'] for c in checks),
        'completed_model_responses_referenced':len(all_runs),'file_checks':checks,'model_no_claim_ranges':no_claims,
        'compilation_complete':bool(plan) and full_scope and len(plan)==len(checks) and not problems and task['status']=='succeeded' and out.get('complete') is True,
        'historical_generation_records_unchanged':old_unchanged,'foreign_key_errors':db.rows('PRAGMA foreign_key_check'),
        'testing_limit':grant['limit'],'testing_budget_occupation':grant['used'],'paid_model_requests_from_verifier':0,
        'human_semantic_quality_accepted':False,'scope':'every_planned_current_source_range_read_and_model_processed; source-bound candidates only'}


@db.atomic
def record_verified(result):
    if not result['compilation_complete'] or result['foreign_key_errors']:raise ValueError('未通过逐范围验证，不能登记全量编译完成')
    pid=result['project_id'];source_versions={};wiki_refs=[]
    for source in result['file_checks']:
        source_versions[source['file_id']]=lineage.file_stamp(pid,source['file_id'])[2]
        for page in source['page_ids']:wiki_refs.append(lineage.ref(pid,'wiki_page',page))
    record={'task_id':result['task_id'],'verified_at':result['verified_at'],'file_count':result['source_file_count'],
        'expected_ranges':result['required_ranges'],'model_processed_ranges':result['model_processed_ranges'],
        'source_versions':source_versions,'wiki_refs':wiki_refs,'human_quality_accepted':False,
        'verification_sha256':hashlib.sha256(db.dumps(result).encode()).hexdigest()}
    db.audit(pid,'knowledge_full_semantic_compilation_verified',record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--task',required=True)
    p.add_argument('--allocation',required=True);p.add_argument('--baseline',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--record',action='store_true',help='全部范围验证通过后追加完成证据；不会登记人工质量通过');a=p.parse_args()
    result=verify(a.project,a.task,a.allocation,a.baseline)
    if a.record:result['completion_record']=record_verified(result)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('file_checks','model_no_claim_ranges')},ensure_ascii=False,indent=2))
    return int(not result['compilation_complete'] or bool(result['foreign_key_errors']))


if __name__=='__main__':raise SystemExit(main())

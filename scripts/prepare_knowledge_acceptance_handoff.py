"""Read-only handoff for compilation, invoices, human review and real papers.

No model generation, case labelling, manual review, settlement or project writes.
The output contains private project evidence and must not enter public releases.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import runpy
import zipfile

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,security,knowledge_acceptance as acceptance,expense_evidence,knowledge_builds,knowledge_lineage as lineage,knowledge_compiler

CRITICAL_TABLES=('generation_runs','generation_billing_groups','generation_billing_group_runs','tasks','files','pages','sources',
    'rules','bibliography','facts','sections','revisions','citations','knowledge_evaluation_cases','knowledge_review_events','knowledge_comparisons')


def fingerprints():
    return {table:{'rows':len(rows),'sha256':hashlib.sha256('\n'.join(sorted(db.dumps(r) for r in rows)).encode()).hexdigest()}
        for table in CRITICAL_TABLES for rows in [db.rows('SELECT * FROM '+table)]}


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def compilation(pid):
    preview=knowledge_builds.preview(pid,{'namespace':'literature','mode':'llm','budget_limit':0})
    task=db.one("SELECT * FROM tasks WHERE project_id=? AND kind='graph_wiki_build' AND json_extract(payload,'$.mode')='llm' AND status NOT IN ('queued','running') ORDER BY created_at DESC LIMIT 1",(pid,))
    result=db.loads(task['result'],{}).get('result',{}) if task else {}
    built={r['file_id']:r for r in result.get('files',[])};failed={r['file_id']:r['message'] for r in result.get('failures',[])}
    completed_observations=set()
    for row in db.rows("SELECT item_key FROM knowledge_build_items WHERE project_id=? AND stage='extract' AND status='completed' "
            "AND (json_extract(result_json,'$.model_processed')=1 OR json_array_length(json_extract(result_json,'$.run_ids'))>0)",(pid,)):
        completed_observations.add(row['item_key'].removeprefix('observation:'))
    files=[]
    with lineage.validation_scope():
        for source in preview['files']:
            units=db.rows("SELECT id,text FROM knowledge_units WHERE parse_id=? AND review_state!='excluded' ORDER BY ordinal",(source['parse_id'],))
            needed={(u['id'],start,end) for u in units for start,end in knowledge_compiler.ranges(u['text']) if u['text'][start:end].strip()}
            observed={}
            for row in db.rows('SELECT id,unit_id,source_version,text,metadata_json FROM knowledge_observations WHERE project_id=? AND file_id=? AND namespace=?',(pid,source['id'],'literature')):
                meta=db.loads(row['metadata_json'],{});key=(row['unit_id'],meta.get('char_start'),meta.get('char_end'))
                if row['source_version']==source['source_version'] and key in needed:observed[key]=row['id']
            completed={key for key,identity in observed.items() if identity in completed_observations}
            record=built.get(source['id']);current=False;error=None
            if record:
                try:
                    lineage.validate_refs(pid,[lineage.ref(pid,'assertion',cid) for cid in record.get('assertion_ids',[])])
                    for page in record.get('page_ids',[]):lineage.version_of(pid,'wiki_page',page)
                    current=True
                except Exception as exception:error=security.redact(exception)
            missing=sorted(needed-completed,key=lambda key:(next(i for i,u in enumerate(units) if u['id']==key[0]),key[1]))
            next_range=missing[0] if missing else None
            files.append({'file_id':source['id'],'name':source['name'],'source_version':source['source_version'],
                'current_units':len(units),'required_extraction_ranges':len(needed),'completed_cached_ranges':len(completed),
                'complete_compilation_record':bool(record),'compilation_record_current':current,'semantic_quality_verified':False,
                'next_range':{'unit_id':next_range[0],'char_start':next_range[1],'char_end':next_range[2],
                    'observation_id':observed.get(next_range)} if next_range else None,
                'blocker':error or failed.get(source['id']),'page_ids':record.get('page_ids',[]) if record else []})
    official=[]
    for row in db.rows("SELECT id,state,manifest_json FROM knowledge_artifacts WHERE project_id=? AND kind='graphrag_index'",(pid,)):
        m=db.loads(row['manifest_json'],{});packet=m.get('packet',{})
        official.append({'id':row['id'],'state':row['state'],'scope':m.get('scope'),'reports':bool(packet.get('reports')),'embeddings':bool(packet.get('embeddings'))})
    return {'files':files,'file_count':len(files),'complete_current_files':sum(f['compilation_record_current'] for f in files),
        'required_extraction_ranges':sum(f['required_extraction_ranges'] for f in files),
        'completed_cached_ranges':sum(f['completed_cached_ranges'] for f in files),
        'latest_cache_task_id':task['id'] if task else None,'official_indexes':official,
        'full_official_index_ready':any(r['state']=='ready' and r['scope']=='full_current_scope' and r['reports'] and r['embeddings'] for r in official),
        'support_assessments':db.rows('SELECT method,COUNT(*) n FROM kg_support_assessments WHERE project_id=? GROUP BY method',(pid,)),
        'maintenance_states':db.rows('SELECT status,COUNT(*) n FROM knowledge_maintenance_items WHERE project_id=? GROUP BY status',(pid,)),
        'all_complete':False,'paid_requests':0}


def provider_requests(pid):
    ledger=expense_evidence.ledger(pid)
    allocation=db.one("SELECT content FROM expense_evidence WHERE project_id=? AND kind='paid_testing_allocation' ORDER BY rowid DESC LIMIT 1",(pid,))
    historical=set(db.loads(allocation['content'],{}).get('historical_unresolved',{})) if allocation else set()
    result=[]
    for item in ledger['runs']:
        if item['kind']!='reservation':continue
        row=db.one('SELECT * FROM generation_runs WHERE id=?',(item['id'],));meta=db.loads(row['manifest'],{});cfg=meta.get('provider',{})
        result.append({k:item.get(k) for k in ('id','task_id','created_at','status','model','currency','reserved','base_url','remote_id','client_request_id','provider_request_id','gaps')}|{
            'cohort':'historical_21' if item['id'] in historical else 'new_unresolved',
            'snapshot_currency':cfg.get('currency'),'credential_fingerprint':meta.get('credential_hash'),
            'outgoing_context_sha256':meta.get('outgoing_context_hash'),'wire_request_sha256':meta.get('wire_request_hash'),
            'response_evidence':meta.get('response_evidence'),'reservation_basis':item.get('basis'),
            'has_saved_matching_request_id':bool(item.get('client_request_id') or item.get('remote_id') or item.get('provider_request_id')),
            'local_run_id_is_not_provider_id':True,'actual_charge':None,'execution_proven':False})
    return result


def write_csv(path,rows,fields):
    def safe(value):
        if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False)
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
        return value
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        writer.writerows({key:safe(row.get(key)) for key in fields} for row in rows)


def generate(pid,output,user_inputs=None):
    security.require_project(pid);output.mkdir(parents=True,exist_ok=True);before=fingerprints()
    plan=compilation(pid);save(output/'01-compilation-checkpoints.json',plan)
    write_csv(output/'01-compilation-checkpoints.csv',plan['files'],['file_id','name','current_units','required_extraction_ranges','completed_cached_ranges',
        'complete_compilation_record','compilation_record_current','semantic_quality_verified','next_range','blocker'])
    requests=provider_requests(pid);provider_dir=output/'provider';provider_dir.mkdir(exist_ok=True)
    save(provider_dir/'request-evidence.json',{'format':'provider-execution-evidence-request.v1','project_id':pid,'created_at':db.now(),
        'not_an_invoice':True,'not_sent_to_provider':True,'requests':requests})
    write_csv(provider_dir/'requests-for-provider.csv',requests,['id','cohort','created_at','base_url','model','status','currency','snapshot_currency',
        'reserved','remote_id','client_request_id','provider_request_id','has_saved_matching_request_id','credential_fingerprint','outgoing_context_sha256','gaps'])
    save(provider_dir/'receipt-fields-template.json',{'not_an_invoice':True,'receipt_id':None,'request_id':None,'client_request_id':None,
        'base_url':None,'model':None,'currency':None,'amount':None,'billed_at':None,'source_url':None,
        'instruction':'所有空值须由服务商原始账单填入；不能补造请求ID、填零费用或把预留当作实际扣款。'})
    (provider_dir/'取证说明.md').write_text('''# 服务商执行与费用取证

本包只整理本机留存证据，没有服务商发票或签发收据，也没有发送给任何第三方。

请由账号持有人在已登录的服务商控制台请求逐请求执行和账单记录，要求返回平台请求ID、已收到的客户端请求ID、模型、带时区的时间、执行状态、币种、实际金额及用量；如执行失败或不收费，也须提供明确原始证据。

`id`是本机记录ID。只有`client_request_id`非空时，该值才是当时留存的发送标识；禁止把本机ID事后充当服务商收到的ID。哈希只证明本机留存内容一致，不证明服务商已收到请求。

缺少远端ID和原始客户端ID的旧请求，需要服务商利用账号、时间、模型及原始请求摘要进行查找并提供可核对的原始范围；仅余额变化或汇总次数不足以唯一匹配。

`receipt-fields-template.json`只是字段清单，含空值，不能导入为收据。拿到凭证后使用项目“费用依据账本”的逐请求凭证导入，系统会校验身份、模型、接口、币种、重复收据及时间。本包不含API密钥或完整原文提示词。账号持有人需自行在服务商控制台识别账号。
''',encoding='utf-8')
    annotation=acceptance.annotation_packet(pid);annotator=output/'annotator';annotator.mkdir(exist_ok=True)
    save(annotator/'80-task-annotation-draft.json',annotation)
    save(annotator/'manual-submission-template.json',{'not_completed_annotations':True,'cases':[
        {'case_id':c['id'],'question':c['question'],'state':'unlabelled','reviewer':None,'real_task_confirmed':False,'compared_original':False,
         'gold':{'unit_ids':[],'evidence_ids':[],'required_qualifiers':[],'unacceptable_inferences':[],'note':None}} for c in annotation['cases']]})
    reviewer=output/'independent-reviewer';reviewer.mkdir(exist_ok=True)
    save(reviewer/'blind-review-readiness.json',{'not_completed_reviews':True,'human_reviewer':None,
        'case_count':len(annotation['cases']),'status':'awaiting_real_gold_and_generated_answers',
        'case_questions':[{'case_id':c['id'],'category':c['category'],'question':c['question']} for c in annotation['cases']],
        'group_answers':[],'gold_included':False,'variant_mapping_included':False})
    save(reviewer/'review-fields-template.json',{'case_id':None,'blind_group':None,'reviewer':None,'compared_original':False,'note':None,
        'supported_claims':None,'checkable_claims':None,'qualifiers_preserved':None,'qualifiers_required':None,'wrong_citations':None,
        'unsupported_expansions':None,'comparisons_valid':None,'comparisons_required':None,'contradictions_identified':None,'contradictions_required':None})
    sections=db.rows('SELECT id,title,current_revision_id FROM sections WHERE project_id=? ORDER BY position',(pid,))
    versions=db.rows('SELECT r.id,r.section_id,r.text FROM revisions r JOIN sections s ON s.current_revision_id=r.id WHERE s.project_id=?',(pid,))
    citations=db.one('SELECT COUNT(*) n FROM citations c JOIN sections s ON s.current_revision_id=c.revision_id WHERE s.project_id=?',(pid,))['n']
    paper={'sections':sections,'nonempty_current_revisions':sum(bool(r['text'].strip()) for r in versions),'current_citations':citations,
        'user_paper_availability':(user_inputs or {}).get('paper','unknown'),'real_paper_accepted':False,
        'requirements':['明确待验收的真实论文正文及其版本','保存正文和实际引用定位','实际作者或审阅者对照原件','记录具体问题与修订结果']}
    save(output/'04-real-paper-prerequisites.json',paper)
    after=fingerprints();integrity={'critical_tables_before':before,'critical_tables_after':after,'unchanged':before==after,
        'foreign_key_errors':db.rows('PRAGMA foreign_key_check'),'new_model_requests':after['generation_runs']['rows']-before['generation_runs']['rows']}
    save(output/'data-integrity.json',integrity)
    summary={'created_at':db.now(),'project_id':pid,'user_inputs':user_inputs or {},'files':plan['file_count'],
        'current_complete_compilations':plan['complete_current_files'],'extraction_ranges_required':plan['required_extraction_ranges'],
        'cached_completed_ranges':plan['completed_cached_ranges'],'pending_provider_requests':len(requests),
        'historical_provider_requests':sum(r['cohort']=='historical_21' for r in requests),'human_annotation_drafts':len(annotation['cases']),
        'drafts_with_matching_source_navigation':sum(bool(c['source_suggestion']) for c in annotation['cases']),
        'drafts_requiring_task_or_source_rework':sum(bool(c['preparation_gap']) for c in annotation['cases']),
        'real_human_labels':sum(c['state']=='labelled' for c in annotation['cases']),
        'real_human_review_records':db.one('SELECT COUNT(*) n FROM knowledge_review_events WHERE project_id=?',(pid,))['n'],
        'real_paper_prerequisites':paper,'integrity':{'unchanged':integrity['unchanged'],'new_model_requests':integrity['new_model_requests']},
        'all_four_accepted':False,'provider_contacted':False,'paid_requests':0}
    save(output/'SUMMARY.json',summary)
    (output/'交接说明.md').write_text(f'''# 四项验收交接包

当前{plan['file_count']}篇文献中，{plan['complete_current_files']}篇有当前有效的完整编译记录。精确分段总数{plan['required_extraction_ranges']}，可复用的已完成分段{plan['completed_cached_ranges']}。全量语义、支持复核及完整索引仍未完成；不得以原文索引或小范围DRIFT替代。

服务商取证清单包含{len(requests)}笔待核请求，其中历史21笔及本轮新增1笔。它不是账单，未发送给服务商。

人工标注包包含{len(annotation['cases'])}项草稿，其中{summary['drafts_requiring_task_or_source_rework']}项需要改写任务或重新选择原件。资料导航只按题目指定原件及已授权用途匹配，不循环填入无关文献，也不作为金标准。`source_suggestion`缺失时须由实际标注人补齐。所有人工姓名、判断和确认保持空白或false。

标注人与独立盲审者须不同。只把`independent-reviewer`目录交给独立评审者，不给金标准、组别映射或后台运行元数据。当前没有实际回答可供盲审，包中没有填造答案。评审应在揭示映射前完成；公开复核分数仍可留存，但不计为独立盲审。

完整95%目标针对D组，要求至少80项有效真实人工任务，覆盖原计划7类任务，全部有实际回答和独立原件核对。报告同时显示样本数与Wilson区间；检索召回率、模型辅助判断或少量试点评分不能代替这项验收。姓名和确认属于操作者声明，软件不能独立认证实际参加者的身份或行为。

真实论文当前非空正文版本数为{paper['nonempty_current_revisions']}，实际引用数为{paper['current_citations']}。示例论文、模型测试稿与他人论文不能自动登记为本项目真实论文验收。

包内没有修改任何费用、人工标签、评审记录或正文，没有调用收费模型。详见`data-integrity.json`。用户已提供的50 USD上限继续保留，新增未知请求未核实前不继续收费。
''',encoding='utf-8')
    manifest={str(path.relative_to(output)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(output.rglob('*'))
        if path.is_file() and path.suffix!='.zip' and path.name!='MANIFEST.sha256.json'}
    save(output/'MANIFEST.sha256.json',manifest)
    with zipfile.ZipFile(output/f'服务商取证包_{len(requests)}笔.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(provider_dir.iterdir()):archive.write(path,'provider/'+path.name)
    with zipfile.ZipFile(output/'独立盲审交接包_待评审.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(reviewer.iterdir()):archive.write(path,path.name)
    with zipfile.ZipFile(output/'四项验收交接包_含项目私有证据.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file() and path.suffix!='.zip':archive.write(path,str(path.relative_to(output)))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--paper-status',default='unknown');p.add_argument('--reviewer-status',default='unknown');p.add_argument('--bill-file-status',default='unknown')
    a=p.parse_args();projects=db.rows('SELECT id FROM projects WHERE archived=0');pid=a.project or (projects[0]['id'] if len(projects)==1 else None)
    if not pid:raise ValueError('请选择项目')
    result=generate(pid,a.output,{'paper':a.paper_status,'reviewers':a.reviewer_status,'bill_files':a.bill_file_status})
    print(json.dumps({k:v for k,v in result.items() if k!='real_paper_prerequisites'},ensure_ascii=False,indent=2))
    return int(not result['integrity']['unchanged'] or result['integrity']['new_model_requests']!=0)


if __name__=='__main__':raise SystemExit(main())

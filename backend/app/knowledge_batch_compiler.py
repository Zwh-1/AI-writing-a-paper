"""Read every current range, checkpoint each observation, batch model latency.

Model coverage, supported quotes and human semantic quality remain separate.
Previously completed single-range extractions are reused without recharging.
"""
from fastapi import HTTPException
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextvars import copy_context

from . import db,provider,knowledge_lineage as lineage,knowledge_graph as graph
from . import knowledge_compiler as compiler,knowledge_units as units
from .config import MAX_PARALLEL_REQUESTS

VERSION='knowledge-batch-extraction.v3-cited-windows'


def quote_windows(text):
    result=[];start=0
    while start<len(text):
        end=min(len(text),start+900)
        if end<len(text):
            boundary=max(text.rfind(mark,start+450,end) for mark in ('。','\n','. ',';','；'))
            if boundary>start:end=boundary+1
        result.append({'index':len(result),'start':start,'end':end,'begins_with':text[start:start+18],'ends_with':text[max(start,end-18):end]})
        if end==len(text):break
        start=max(start+1,end-160)
    return result


def payload_for(observations):
    return {'operation':'extract_source_bound_research_claims','schema_version':VERSION,
        'observations':[{'id':o['id'],'text':o['text'],'quote_windows':quote_windows(o['text']),'namespace':o['namespace'],
            'context':{k:v for k,v in db.loads(o['metadata_json'],{}).items() if k in ('content_roles','material_use','observation_scope','char_start','char_end')}} for o in observations],
        'output_schema':{'observations':[{'observation_id':'provided id','no_claim_reason':'仅当没有可提炼研究论断时说明，其他情况空字符串',
            'entities':[{'name':'原文实际技术术语','kind':'concept/method/system/dataset/metric/environment'}],
            'claims':[{'statement':'保留归属、范围和否定的一句中文主论断','source_segment':0,
                'predicate':'reports/uses/limits/compares/defines','conditions':['原文限定条件'],
                'polarity':'positive/negative/unknown'}]}]},
        'requirements':['必须逐一返回所有输入observation_id，不得漏掉或新增；每项独立判断',
            '每项优先保留1条主要研究论断，必要时2条；聚焦问题、方法、结果及限制，不重复抄录',
            '论断尽量不超过100个汉字，引句优先使用不超过240字符的完整连续范围；若无法保留必要条件就不压缩',
            '每项实体最多2个，在该项顶层entities中列一次，不能在每条claims内重复；实体必须直接属于本项论断，不能凭共现认定因果',
            '参考文献列表、页眉页脚和作者地址不能当作本论文的研究结论；无研究论断时返回空数组并说明原因',
            '每条论断必须以source_segment选择该原文quote_windows中的一个index，程序将逐字引用该窗口；不要另写quote，不得选择不支持论断的窗口',
            '不得借用上一页、下一页或其他输入来补全当前片段；无法独立支持时返回0条并记录缺口',
            '不以已有文献结论证明本项目已经实现或实测；不代填人工审查']}


def groups(observations,cfg):
    from .knowledge_model_bridge import SYSTEM
    from .source_payload import token_upper_bound
    limit=int(cfg.get('context_limit',32000))-int(cfg.get('max_output_tokens',8192))-256
    group=[]
    for observation in observations:
        candidate=group+[observation]
        if group and (len(candidate)>8 or token_upper_bound(SYSTEM,db.dumps(payload_for(candidate)))>limit):
            yield group;group=[]
        group.append(observation)
        if token_upper_bound(SYSTEM,db.dumps(payload_for(group)))>limit:
            lineage.fail('BATCH_SOURCE_TOO_LARGE','单个完整原文范围超出当前上下文，未静默截断或发送',422)
    if group:yield group


def correct_row(task,observation,run,claims,reason='原引用或结构未通过校验'):
    from .knowledge_model_bridge import json_call
    refs=[lineage.ref(task['project_id'],'observation',observation['id'],True)]
    data,correction=json_call(task,'knowledge_extract_quote_correction',{
        'observation':{'id':observation['id'],'text':observation['text'],'quote_windows':quote_windows(observation['text']),'namespace':observation['namespace']},
        'rejected_claims':claims if isinstance(claims,list) else [],'correction_reason':reason,
        'output_schema':{'observation_id':observation['id'],'no_claim_reason':'无完整研究论断时说明片段或质量限制',
            'claims':[{'statement':'本段实际支持的论断','source_segment':0,'conditions':['原文限定'],'predicate':'reports/uses/limits/compares/defines'}]},
        'requirements':['只可使用此段，不可借用上一页、下一页或其他输入',
            '以source_segment选择本段quote_windows中的窗口编号；不要输出引句文字，程序会逐字回填原文',
            'OCR乱序时不要拼接文本、修复字符或补全句子；窗口不足以支持时删除该论断',
            '可以返回0条并明确记录片段不完整或需回读的原因；不得漏掉observation_id',
            '最多2条完整主论断，保留条件与否定，不补造研究结果']},refs)
    if data.get('observation_id')!=observation['id']:lineage.fail('INVALID_PROVENANCE','复核返回错误的原文ID',422)
    fixed=validate_row(task,observation,data,correction,allow_correction=False)
    fixed['run_ids']=[run,*fixed['run_ids']];fixed['quote_correction_from_run_id']=run
    return fixed


def validate_row(task,observation,row,run,allow_correction=True):
    claims=row.get('claims')
    if not isinstance(claims,list) or len(claims)>8:
        if allow_correction:return correct_row(task,observation,run,claims,'候选数组结构无效，需要独立抽取此原文')
        lineage.fail('KNOWLEDGE_OUTPUT_INVALID','批次单元候选数组无效',422)
    reason=row.get('no_claim_reason','')
    if not claims and (not isinstance(reason,str) or not reason.strip()):
        if allow_correction:return correct_row(task,observation,run,claims,'零论断没有原因，需要独立确认此原文')
        lineage.fail('KNOWLEDGE_OUTPUT_INVALID','零论断单元必须记录模型判断原因，不能默认为已编译',422)
    if any(isinstance(c,dict) and c.get('observation_id',observation['id'])!=observation['id'] for c in claims):
        lineage.fail('INVALID_PROVENANCE','候选论断的来源ID与所属单元不一致',422)
    shared=row.get('entities',[]);prepared=[];windows=quote_windows(observation['text'])
    def entities(values):
        if not isinstance(values,list):return []
        return [{**e,'quote':e.get('quote') or compiler.align_quote(observation['text'],e.get('name'))} for e in values if isinstance(e,dict)]
    for claim in claims:
        if not isinstance(claim,dict):prepared.append(claim);continue
        candidate={**claim,'observation_id':observation['id'],'entities':entities(claim.get('entities',shared))}
        if 'source_segment' in claim:
            index=claim['source_segment']
            if type(index) is not int or not 0<=index<len(windows):candidate['quote']=''
            else:
                window=windows[index];candidate['quote']=observation['text'][window['start']:window['end']]
                candidate['quote_binding']={'method':'model_selected_exact_original_window','index':index,'char_start':window['start'],'char_end':window['end']}
        prepared.append(candidate)
    accepted,validation=compiler.validate_candidates(observation,prepared)
    validation.update(run_id=run,batch_schema=VERSION)
    db.audit(task['project_id'],'knowledge_candidate_quote_validation',validation)
    if claims and not accepted:
        if not allow_correction:lineage.fail('INVALID_QUOTE','独立复核后仍无连续原文引句；未采用不实引用',422)
        return correct_row(task,observation,run,claims)
    return {'candidates':accepted,'run_ids':[run],'no_claim_reason':reason if not claims else '',
        'model_processed':True,'human_approval_added':False}


def extract_batch(task,observations):
    from .knowledge_model_bridge import json_call
    if any(db.loads(o['metadata_json'],{}).get('requires_native_slide_reading') for o in observations):
        lineage.fail('GUIDANCE_CONTEXT_INCOMPLETE','批次中仍有需要完整原生幻灯片语境的观察，未发送模型推断',422)
    refs=[lineage.ref(task['project_id'],'observation',o['id'],True) for o in observations]
    try:data,run=json_call(task,'knowledge_extract_batch',payload_for(observations),refs)
    except Exception as error:
        if isinstance(error,ValueError):
            from .knowledge_model_bridge import json_call as repair_call
            compact={'operation':'repair_batch_protocol','observations':[{'id':o['id'],'quote_windows':quote_windows(o['text'])} for o in observations],
                'invalid_response':str(error)[:500],'requirements':['只返回有效JSON对象observations数组；每项必须包含 observation_id、claims 数组和 no_claim_reason，缺失时空数组并填写原因','不要重新推理，不要新增事实']}
            data,run=repair_call(task,'knowledge_extract_batch_protocol_repair',compact,refs)
        else:raise
    rows=data.get('observations')
    allowed={o['id']:o for o in observations}
    mapped={identity:[] for identity in allowed}
    for row in rows if isinstance(rows,list) else []:
        if isinstance(row,dict) and isinstance(row.get('observation_id'),str) and row['observation_id'] in mapped:
            mapped[row['observation_id']].append(row)
    result={}
    for oid,observation in allowed.items():
        selected=mapped[oid]
        if len(selected)!=1:
            db.audit(task['project_id'],'knowledge_batch_missing_or_duplicate_unit',{'observation_id':oid,'run_id':run,'returned_rows':len(selected)})
            result[oid]=correct_row(task,observation,run,[],'批次中缺失或重复此原文ID，需要独立处理，不能默认为已完成')
        else:result[oid]=validate_row(task,observation,selected[0],run)
    return result


@lineage.validation_scope()
def cached_rows(pid,fid,cfg):
    """Recover each validated unit from a paid response, independent of grouping."""
    from .knowledge_json import parse
    from .security import safe_object
    keys=('base_url','model','protocol','max_output_tokens','context_limit','json_mode','temperature','thinking_enabled','reasoning_effort','top_p','seed')
    found={}
    for run in db.rows("SELECT * FROM generation_runs WHERE project_id=? AND status='completed' AND json_extract(manifest,'$.knowledge_operation')='knowledge_extract_batch' "
            "AND EXISTS(SELECT 1 FROM json_each(manifest,'$.file_ids') f WHERE f.value=?) ORDER BY created_at DESC",(pid,fid)):
        meta=db.loads(run['manifest'],{})
        if any(meta.get('provider',{}).get(k)!=cfg.get(k) for k in keys) or not meta.get('output_blob'):continue
        try:
            lineage.validate_refs(pid,meta.get('knowledge_refs',[]),True)
            data,_,_=parse(safe_object(meta['output_blob']).read_text(encoding='utf-8'))
        except (HTTPException,ValueError,TypeError,SyntaxError,OSError):continue
        allowed={r['id']:r['version'] for r in meta.get('knowledge_refs',[]) if r['type']=='observation'}
        rows=data.get('observations') if isinstance(data,dict) else None
        if not isinstance(rows,list):continue
        for row in rows:
            if not isinstance(row,dict):continue
            oid=row.get('observation_id')
            if isinstance(oid,str) and oid in allowed and oid not in found:found[oid]=(row,run['id'],allowed[oid])
    return found


@lineage.validation_scope()
def prepare_file(task,fid,head,original,fingerprint,progress,namespace):
    pid=task['project_id']
    rows=db.rows("SELECT id,text FROM knowledge_units WHERE parse_id=? AND review_state!='excluded' ORDER BY ordinal",(head['id'],))
    observations=[];adopted={};pending=[];items={};doc=None;gaps=[]
    for index,row in enumerate(rows):
        for start,end in compiler.ranges(row['text']):
            if not row['text'][start:end].strip():continue
            observation=compiler.observe_unit(pid,row['id'],start,end,True,intent='guidance' if namespace=='guidance' else 'research')
            observations.append(observation)
            if doc is None:doc=graph.entity(pid,{'namespace':observation['namespace'],'kind':'document','key':'file:'+fid,'name':original['name'],
                'sources':[lineage.ref(pid,'observation',observation['id'])],'method':'source_identity'})
            key='observation:'+observation['id'];input_hash=lineage.digest([observation['content_hash'],fingerprint])
            saved=db.one('SELECT * FROM knowledge_build_items WHERE project_id=? AND item_key=? AND input_hash=?',(pid,key,input_hash))
            items[observation['id']]={'id':saved['id'] if saved else db.uid(),'key':key,'hash':input_hash}
            if saved and saved['status']=='completed':
                previous=db.loads(saved['result_json'],{})
                try:
                    lineage.validate_refs(pid,[lineage.ref(pid,'assertion',cid) for cid in previous.get('assertion_ids',[])])
                    adopted[observation['id']]=previous;continue
                except HTTPException:
                    if previous.get('candidates') is not None:
                        fresh=compiler.adopt_candidates(pid,observation,previous['candidates'],doc,'llm')
                        adopted[observation['id']]={**previous,**fresh};continue
            pending.append(observation)
        if progress:progress(index+1,len(rows))
    return observations,adopted,pending,items,doc


def compile_file(task,fid,mode='llm',progress=None,namespace=None):
    if mode!='llm':return compiler.compile_file(task,fid,mode,progress,namespace)
    pid=task['project_id'];original,head,stamp=lineage.file_stamp(pid,fid)
    if not head or not units.parse_current(head,original):
        units.build(pid,fid,allow_material=True);original,head,stamp=lineage.file_stamp(pid,fid)
    cfg=provider.config()
    fingerprint=lineage.digest([compiler.VERSION,compiler.MODEL_SCHEMA,mode,
        {k:cfg.get(k) for k in ('base_url','model','protocol','context_limit','max_output_tokens','json_mode','thinking_enabled','reasoning_effort','temperature','top_p','seed')},
        db.rows('SELECT id,redirect_id,current_version_id FROM kg_entities WHERE project_id=? AND redirect_id IS NOT NULL',(pid,)),compiler.QUOTE_VALIDATION_VERSION])
    observations,adopted,pending,items,doc=prepare_file(task,fid,head,original,fingerprint,progress,namespace)
    # Reuse any single-range response from the older compiler, and keep the
    # explicitly authorized unknown request out of multi-source batching.
    remaining=[];recoverable=cached_rows(pid,fid,cfg)
    for observation in pending:
        if observation['id'] in recoverable and recoverable[observation['id']][2]==observation['content_hash']:
            row,rid,_=recoverable[observation['id']]
            value=validate_row(task,observation,row,rid)
            adopted[observation['id']]={**compiler.adopt_candidates(pid,observation,value['candidates'],doc,'llm'),**value}
            continue
        existing=db.one("SELECT id FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.knowledge_operation')='knowledge_extract' "
            "AND EXISTS(SELECT 1 FROM json_each(manifest,'$.knowledge_refs') r WHERE json_extract(r.value,'$.type')='observation' AND json_extract(r.value,'$.id')=?) LIMIT 1",(pid,observation['id']))
        if existing:
            candidates,runs=compiler.extracted(task,observation,'llm')
            adopted[observation['id']]={**compiler.adopt_candidates(pid,observation,candidates,doc,'llm'),
                'candidates':candidates,'run_ids':runs,'model_processed':True}
        else:remaining.append(observation)
    def checkpoint(observation,value):
        item=items[observation['id']]
        db.execute('INSERT INTO knowledge_build_items VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,item_key,input_hash) DO UPDATE SET '
            'task_id=excluded.task_id,status=excluded.status,result_json=excluded.result_json,error=NULL,updated_at=excluded.updated_at',
            (item['id'],pid,task['id'],item['key'],item['hash'],'extract','completed',db.dumps(value),None,db.now()))
    for observation in observations:
        if observation['id'] in adopted:checkpoint(observation,adopted[observation['id']])
    parallel=db.loads(task.get('payload'),{}).get('knowledge_parallel_requests',1)
    if type(parallel) is not int or not 1<=parallel<=MAX_PARALLEL_REQUESTS:lineage.fail('INVALID_PARALLELISM',f'知识编译并发数须为1至{MAX_PARALLEL_REQUESTS}',422)
    planned=iter(groups(remaining,cfg));error=None
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        pending={}
        def submit_one():
            try:chunk=next(planned)
            except StopIteration:return False
            from .tasks import event
            event(task['id'],f"{original['name']}：语义分段 {len(adopted)}/{len(observations)}，本批{len(chunk)}段",50)
            future=executor.submit(copy_context().run,extract_batch,task,chunk);pending[future]=chunk
            return True
        for _ in range(parallel):
            if not submit_one():break
        while pending:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                chunk=pending.pop(future)
                try:
                    compiled=future.result()
                    with lineage.validation_scope(fresh=True):
                        for observation in chunk:
                            value=compiled[observation['id']]
                            result={**compiler.adopt_candidates(pid,observation,value['candidates'],doc,'llm'),**value}
                            adopted[observation['id']]=result;checkpoint(observation,result)
                except BaseException as exception:
                    if error is None:error=exception
            if error is None:
                while len(pending)<parallel and submit_one():pass
            # Already submitted siblings finish and retain their own result;
            # no new request is scheduled after an unknown or failed batch.
        if error is not None:raise error
    assertions=[cid for observation in observations for cid in adopted[observation['id']].get('assertion_ids',[])]
    entities=[eid for observation in observations for eid in adopted[observation['id']].get('entity_ids',[])]
    runs=list(dict.fromkeys(rid for observation in observations for rid in adopted[observation['id']].get('run_ids',[])))
    pages=compiler.document_page(pid,original,doc,assertions,'llm') if doc and assertions else []
    gaps=[{'observation_id':o['id'],'kind':'model_no_research_claim','note':adopted[o['id']]['no_claim_reason']}
        for o in observations if adopted[o['id']].get('no_claim_reason')]
    if len(adopted)!=len(observations):lineage.fail('INCOMPLETE_BATCH_COVERAGE','仍有原文范围未收到模型判断，未记为完整',422)
    return {'file_id':fid,'observation_ids':[o['id'] for o in observations],'assertion_ids':assertions,
        'entity_ids':list(dict.fromkeys(entities+([doc['id']] if doc else []))),'page_ids':pages,'run_ids':runs,'mode':'llm',
        'extraction_strategy':VERSION,'model_processed_ranges':len(adopted),'expected_ranges':len(observations),
        'all_current_ranges_model_processed':True,'gaps':gaps,'semantic_quality_verified':False}

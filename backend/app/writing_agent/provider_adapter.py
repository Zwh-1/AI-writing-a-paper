"""Agent transport uses the existing request ledger and configured credential only."""
import hashlib,json,re
import httpx
from .. import db,provider
from .common import digest,fail
ALIAS_NOTE=' 本次E/B/P/S/I/C/R编号仅为给定证据、书目、段落、句子、问题、章节、正文版本的局部标识，请原样使用给定编号；不得猜测原始ID，也不要将内部编号写入正文。'

def transport(system,user,cfg,images=None):
    frame=json.loads(user);history=frame.get('messages',[]);tools=frame.get('tools',[]);protocol=cfg['protocol'];limit=int(cfg['max_output_tokens'])
    if images:raise ValueError('代码调查接口不接收隐式图像载荷')
    if protocol=='responses':
        path='/v1/responses';body={'model':cfg['model'],'instructions':system,'input':frame.get('items') or history,'max_output_tokens':limit,'store':False}
        if tools:body['tools']=[{'type':'function',**x['function']} for x in tools]
        if cfg.get('reasoning_effort'):body['reasoning']={'effort':cfg['reasoning_effort']}
    else:
        path='/v1/chat/completions';output_key='max_completion_tokens' if re.search(r'(?:^|/)gpt-(?:[5-9]|[1-9][0-9])',cfg['model'],re.I) else 'max_tokens'
        body={'model':cfg['model'],'messages':[{'role':'system','content':system},*history],output_key:limit,'stream':False}
        if tools:body['tools']=tools
        elif cfg.get('json_mode'):body['response_format']={'type':'json_object'}
        if 'thinking_enabled' in cfg:body['thinking']={'type':'enabled' if cfg['thinking_enabled'] else 'disabled'}
        if cfg.get('reasoning_effort') and cfg.get('thinking_enabled',True):body['reasoning_effort']=cfg['reasoning_effort']
    with provider.client(cfg) as client:
        try:r=client.post(provider.endpoint(cfg['base_url'],path),headers=provider.headers(protocol),json=body)
        except (httpx.ConnectError,httpx.ConnectTimeout) as e:
            failure=provider.ProviderError('连接尚未建立，模型HTTP请求未发送：'+type(e).__name__,unknown=False)
            failure.failure_phase='connect_before_http';failure.transport_error_type=type(e).__name__
            raise failure from e
        except httpx.RequestError as e:raise provider.ProviderError('模型请求已发出，远端状态未知：'+type(e).__name__,unknown=True) from e
    provider.response_error(r)
    try:data=r.json()
    except ValueError as e:raise provider.ProviderError('远端没有返回JSON，执行结果未知',unknown=True) from e
    if protocol=='responses':
        items=data.get('output',[]);text=''.join(p.get('text','') for x in items for p in x.get('content',[]) if p.get('type')=='output_text');calls=[{'id':x.get('call_id'),'name':x.get('name'),'arguments':x.get('arguments','{}')} for x in items if x.get('type')=='function_call'];finish=data.get('status');usage=data.get('usage',{});message=None
    else:
        choices=data.get('choices',[])
        if not choices:raise provider.ProviderError('模型没有返回候选',unknown=True)
        message=choices[0].get('message',{});text=message.get('content') or '';calls=[{'id':x.get('id'),'name':x.get('function',{}).get('name'),'arguments':x.get('function',{}).get('arguments','{}')} for x in message.get('tool_calls',[])];finish=choices[0].get('finish_reason');raw=data.get('usage',{});usage={'input_tokens':raw.get('prompt_tokens'),'output_tokens':raw.get('completion_tokens'),'raw':raw};items=[]
    envelope={'agent_transport':True,'text':text,'tool_calls':calls,'message':message,'items':items,'finish':finish,'reported_model':data.get('model')}
    cost=provider.usage_cost(cfg,usage)
    result={'text':db.dumps(envelope),'usage':usage,'cost':cost,'remote_id':data.get('id'),'response_evidence':provider.response_evidence(r,data),'request_hash':digest({'path':path,'body':body})}
    if finish in ('length','incomplete','MAX_TOKENS'):raise provider.ProviderError('生成预算耗尽，保留不完整候选',response_result=result)
    if not text and not calls:raise provider.ProviderError('模型响应没有正文或工具调用',response_result=result)
    return result

def call(task,profile,system,payload,manifest,tools=None,messages=None,items=None):
    from .profiles import for_session,record_capability
    from ..tasks import _model_call
    p,cfg,key=for_session(task['project_id'],profile['id']);cfg['agent_transport']=True
    if manifest.get('writing_step_id') and manifest.get('lease_epoch') is not None:
        from .coordinator import assert_lease
        assert_lease(manifest['writing_step_id'],manifest['lease_epoch'])
    from .identifiers import encode
    model_payload,aliases=encode(payload,manifest) if messages is None and not tools else (payload,{})
    if aliases:
        manifest={**manifest,'identifier_aliases':aliases}
        if ALIAS_NOTE not in system:system+=ALIAS_NOTE
    frame={'messages':messages or [{'role':'user','content':db.dumps(model_payload)}],'tools':tools or []}
    if items is not None:frame['items']=items
    encoded=system+'\n'+db.dumps(frame)
    for secret_profile in ('','gpt-fallback','material-summary'):
        from .. import security
        configured=security.get_key(secret_profile)
        if configured and len(configured)>12 and configured in encoded:fail('SECRET_IN_CONTEXT','生成输入包含已配置密钥，已阻止外发',403)
    if 'target_snapshot_id' in manifest and manifest.get('evidence_versions'):
        from .impact import check_manifest
        conflicts=check_manifest(task['project_id'],manifest)
        if conflicts:fail('INPUT_VERSION_CHANGED','发送前依据或权限已变化',409,conflicts=conflicts)
    frame_hash=digest({'system':system,'frame':frame,'profile':p['route_fingerprint']})
    if manifest.get('writing_step_id'):
        from .context import save_wire_context
        save_wire_context(manifest['writing_step_id'],frame,manifest,system,cfg)
    for old in db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC',(task['id'],)):
        meta=db.loads(old['manifest'],{})
        if meta.get('agent_frame_hash')!=frame_hash:continue
        if old['status']=='completed' and meta.get('output_blob'):
            from ..security import safe_object
            saved=json.loads(safe_object(meta['output_blob']).read_text(encoding='utf-8'))
            return finish_structured(task,profile,system,payload,manifest,saved,aliases,tools,messages,items,frame_hash)
        if old['status'] in ('pending','sent','outcome_unknown'):raise provider.ProviderError('此检查点的远端结果未知，不自动重复请求',unknown=True)
    session_id=manifest.get('writing_session_id')
    if session_id:
        session=db.one('SELECT policy_json FROM writing_sessions WHERE id=?',(session_id,))
        limit=db.loads(session['policy_json'],{}).get('budget_limit') if session else None
        used=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE json_extract(manifest,'$.writing_session_id')=? AND status NOT IN ('rejected','failed_before_send')",(session_id,))['n']
        from ..summary_billing import request_reservation,BudgetExceeded
        reservation,_=request_reservation(cfg,system,db.dumps(frame))
        if limit is not None and used+reservation>limit:raise BudgetExceeded('写作会话预算不足，已完成阶段保留')
    diagnostic_id=manifest.get('diagnostic_run_id')
    if diagnostic_id:
        run=db.one('SELECT policy_json FROM diagnostic_runs WHERE id=? AND project_id=?',(diagnostic_id,task['project_id']))
        if not run:fail('NOT_FOUND','诊断运行不存在',404)
        limit=db.loads(run['policy_json'],{})['budget_limit'];used=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE json_extract(manifest,'$.diagnostic_run_id')=? AND status NOT IN ('rejected','failed_before_send')",(diagnostic_id,))['n']
        from ..summary_billing import request_reservation,BudgetExceeded
        reservation,_=request_reservation(cfg,system,db.dumps(frame))
        if used+reservation>limit:raise BudgetExceeded('诊断运行预算不足，已完成样本保留')
    with provider.request_scope(cfg,key):
        from .billing import sample
        before=sample(cfg) if cfg.get('reconcile_billing') else None
        try:result=_model_call(task,system,db.dumps(frame),{**manifest,'agent_frame_hash':frame_hash,'agent_transport':True,'provider_route':p['configuration'].get('route','primary'),'automatic_retry_index':0})
        except provider.ProviderError as error:
            if error.response_result:
                if before:save_billing_interval(task['id'],frame_hash,before,sample(cfg),error.response_result['usage'])
                record_reasoning_observation(task['project_id'],p['id'],cfg,error.response_result)
            raise
        if before:
            save_billing_interval(task['id'],frame_hash,before,sample(cfg),result['usage'])
    parsed=json.loads(result['text'])
    record_reasoning_observation(task['project_id'],p['id'],cfg,result)
    record_capability(task['project_id'],p['id'],'text_generation','supported',{'run_request_hash':result['request_hash'],'scope':'this_sample'})
    if parsed.get('tool_calls'):record_capability(task['project_id'],p['id'],'tools','supported',{'scope':'this_sample'})
    return finish_structured(task,profile,system,payload,manifest,parsed,aliases,tools,messages,items,frame_hash)

def save_billing_interval(task_id,frame_hash,before,after,usage):
    from .billing import reconcile
    proof=reconcile(before,after,usage)
    row=db.one("SELECT * FROM generation_runs WHERE task_id=? AND status IN ('completed','completed_invalid') AND json_extract(manifest,'$.agent_frame_hash')=? ORDER BY rowid DESC LIMIT 1",(task_id,frame_hash))
    if not row:return
    from ..expense_evidence import append
    append(row['project_id'],'interval_observation',{'before':before,'after':after,'usage':usage,'matched_proof':proof},row['id'])
    meta=db.loads(row['manifest'],{});meta['billing_interval_observation']={'before':before,'after':after,'matched':proof is not None}
    if proof:meta['billing_interval_proof']=proof;db.execute('UPDATE generation_runs SET cost=?,manifest=? WHERE id=?',(proof['amount'],db.dumps(meta),row['id']))
    else:db.execute('UPDATE generation_runs SET manifest=? WHERE id=?',(db.dumps(meta),row['id']))

def record_reasoning_observation(pid,profile_id,cfg,result):
    from .profiles import record_capability
    raw=result.get('usage',{}).get('raw',{});details=raw.get('completion_tokens_details',{}) or raw.get('output_tokens_details',{})
    tokens=details.get('reasoning_tokens',raw.get('completion_thinking_tokens'))
    if type(tokens) is int and tokens>0:
        observed={'reported_reasoning_tokens':tokens,'scope':'this_response_not_a_tokenizer_or_semantic_quality_proof'}
        record_capability(pid,profile_id,'reasoning_usage','supported',observed)
        if cfg.get('thinking_enabled') is False:record_capability(pid,profile_id,'reasoning_parameter','unknown',{**observed,'requested_thinking_enabled':False,'observation':'disabled_requested_but_reasoning_usage_reported; do_not_assume_visible_output_budget'})

def finish_structured(task,profile,system,payload,manifest,envelope,aliases,tools,messages,items,frame_hash=None):
    """One bounded repair of a known complete invalid JSON response; never retries unknown sends."""
    from .identifiers import decode,repair_identifier_keys,STAGES
    structured=not tools and (manifest.get('stage') in STAGES|{'investigate','diagnostic_model_review'} or manifest.get('diagnostic_run_id'))
    if not structured or envelope.get('tool_calls'):return envelope
    try:parsed=provider.parse_json(envelope['text'])
    except (ValueError,TypeError,provider.ProviderError) as error:
        if isinstance(error,provider.ProviderError) and error.unknown:raise
        if manifest.get('structured_json_attempt',0)>=1:raise
        # The first full response remains in the existing ledger. A changed
        # instruction gives the repair its own reproducible request checkpoint.
        note=' 上一次完整响应不是合法JSON。请仅根据同一批原始材料重新返回本阶段JSON，严格闭合键名、字符串和数组，不输出说明或Markdown；不要改变事实与任务。'
        return call(task,profile,system+note,payload,{**manifest,'structured_json_attempt':1,'prior_json_error':str(error)[:300]},tools,messages,items)
    if aliases:
        parsed,repairs=repair_identifier_keys(parsed,aliases)
        if repairs and frame_hash:
            row=db.one("SELECT id,manifest FROM generation_runs WHERE task_id=? AND json_extract(manifest,'$.agent_frame_hash')=? ORDER BY rowid DESC LIMIT 1",(task['id'],frame_hash))
            if row:
                meta=db.loads(row['manifest'],{});meta['output_identifier_key_repairs']=repairs;db.execute('UPDATE generation_runs SET manifest=? WHERE id=?',(db.dumps(meta),row['id']))
        envelope={**envelope,'text':db.dumps(decode(parsed,aliases)),'identifier_aliases':aliases}
    return envelope

"""Agent transport uses the existing request ledger and configured credential only."""
import hashlib,json,re
import httpx
from .. import db,provider,adapter
from .common import digest,fail
ALIAS_NOTE=' 本次E/B/P/S/I/C/R编号仅为给定证据、书目、段落、句子、问题、章节、正文版本的局部标识，请原样使用给定编号；不得猜测原始ID，也不要将内部编号写入正文。'
JSON_REPAIR_NOTE=' 若request_parameters指定结构修复，仅根据同一批原始材料返回本阶段合法JSON，严格闭合键名、字符串和数组，不输出说明或Markdown；修复次数与上次错误只是诊断数据，不得改变事实与任务。'

def transport(system,user,cfg,images=None):
    from ..official_api_policy import assert_allowed
    assert_allowed(cfg)
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
        message=choices[0].get('message',{});text=message.get('content') or '';calls=[{'id':x.get('id'),'name':x.get('function',{}).get('name'),'arguments':x.get('function',{}).get('arguments','{}')} for x in message.get('tool_calls',[])];finish=choices[0].get('finish_reason');usage=data.get('usage',{});items=[]
    envelope={'agent_transport':True,'text':text,'tool_calls':calls,'message':message,'items':items,'finish':finish,'reported_model':data.get('model')}
    usage=adapter.normalize_usage(usage,protocol)
    cost=provider.usage_cost(cfg,usage)
    result={'text':db.dumps(envelope),'usage':usage,'cost':cost,'remote_id':data.get('id'),'response_evidence':provider.response_evidence(r,data),'request_hash':digest({'path':path,'body':body})}
    result['provider_snapshot']={name:cfg[name] for name in ('base_url','protocol','model')}
    result['actual_provider_config']=dict(cfg)
    scoped=provider._REQUEST.get() or {}
    if scoped.get('key'):result['actual_provider_credential_hash']=adapter.credential_hash(scoped['key'])
    if finish in ('length','incomplete','MAX_TOKENS'):raise provider.ProviderError('生成预算耗尽，保留不完整候选',response_result=result)
    if not text and not calls:raise provider.ProviderError('模型响应没有正文或工具调用',response_result=result)
    return result

@provider.with_attempt_budget
def call(task,profile,system,payload,manifest,tools=None,messages=None,items=None):
    from .. import ai_request_cache
    stage=manifest.get('stage','agent')
    slot={'section_id':manifest.get('writing_step_id') or manifest.get('section_id')}
    position={name:manifest.get(name,0) for name in (
        'annotation_batch','coherence_expansion_round','paper_chunk_index','writing_unit_index','structured_json_attempt')}
    position.update(message_turn=len(messages) if messages is not None else 0,
                    item_turn=len(items) if items is not None else 0)
    metadata={**manifest,'agent_slot_key':ai_request_cache.slot_key(task,slot,'agent:'+stage,position),
              'agent_stage_key':ai_request_cache.slot_key(task,slot,'agent:'+stage,0)}
    timeout=float(provider.config().get('timeout_seconds',600))+15
    # Serialize one stage even when different native-tool turns are preparing;
    # the ledger slot distinguishes their normal completed checkpoints.
    with ai_request_cache.singleflight(task,slot,'agent:'+stage,0,timeout=timeout):
        live=db.one('SELECT status,attempt,cancel_requested FROM tasks WHERE id=? AND project_id=?',
                    (task['id'],task['project_id']))
        if not live or live['cancel_requested'] or live['status']=='cancelled':
            raise InterruptedError('写作任务已取消或不存在')
        if 'attempt' in task and task['attempt']!=live['attempt']:
            fail('TASK_ATTEMPT_CHANGED','写作任务代次已变化，不能发送或复用旧阶段',409)
        return _call_locked(task,profile,system,payload,metadata,tools,messages,items)


def _call_locked(task,profile,system,payload,manifest,tools=None,messages=None,items=None):
    from .profiles import for_session,record_capability
    from ..tasks import model_call
    p,cfg,key=for_session(task['project_id'],profile['id']);cfg['agent_transport']=True
    if manifest.get('writing_step_id') and manifest.get('lease_epoch') is not None:
        from .coordinator import assert_lease
        assert_lease(manifest['writing_step_id'],manifest['lease_epoch'])
    from .identifiers import encode
    model_payload,aliases=encode(payload,manifest) if messages is None and not tools else (payload,{})
    if aliases:
        manifest={**manifest,'identifier_aliases':aliases}
        if ALIAS_NOTE not in system:system+=ALIAS_NOTE
    from .. import prompt_layout
    if prompt_layout.enabled(task) and messages is None and items is None and not tools:
        if JSON_REPAIR_NOTE not in system:system+=JSON_REPAIR_NOTE
        stage=manifest.get('stage','agent')
        index=manifest.get('writing_unit_index',manifest.get('annotation_batch',0))
        system,model_payload,layout=prompt_layout.prepare(task,system,model_payload,stage,index=index)
        manifest={**manifest,**layout}
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
    cached=db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC,rowid DESC',(task['id'],))
    for old in cached:
        meta=db.loads(old['manifest'],{})
        same_stage=(meta.get('agent_stage_key')==manifest.get('agent_stage_key')
                    or not meta.get('agent_stage_key') and meta.get('agent_transport')
                    and meta.get('stage','agent')==manifest.get('stage','agent')
                    and (meta.get('writing_step_id') or meta.get('section_id'))
                        ==(manifest.get('writing_step_id') or manifest.get('section_id')))
        if same_stage and old['status'] in ('pending','sent','outcome_unknown'):
            raise provider.ProviderError('此写作阶段仍有未确认的远端请求，不能通过新增对话或更换批次继续发送',unknown=True)
        if (same_stage and old['status']=='completed_invalid'
                and meta.get('structured_json_attempt',0)==manifest.get('structured_json_attempt',0)):
            raise provider.ProviderError('此写作阶段已保存不完整响应，不能通过新增对话再次请求',unknown=True)
        if (meta.get('agent_slot_key')==manifest.get('agent_slot_key')
                and old['status'] in ('pending','sent','outcome_unknown','completed_invalid')):
            raise provider.ProviderError('此写作位置的远端请求尚未确认或已有不完整响应，不能通过修改输入重复请求',unknown=True)
    for old in cached:
        meta=db.loads(old['manifest'],{})
        if meta.get('agent_slot_key') and meta['agent_slot_key']!=manifest.get('agent_slot_key'):continue
        if meta.get('agent_frame_hash')!=frame_hash:continue
        if old['status']=='completed' and meta.get('output_blob'):
            from .. import tasks
            tasks._validate_model_source_inputs(task['project_id'],meta)
            tasks._validate_model_presend(task,meta)
            from ..security import safe_object
            saved=json.loads(safe_object(meta['output_blob']).read_text(encoding='utf-8'))
            return finish_structured(task,profile,system,payload,manifest,saved,aliases,tools,messages,items,frame_hash)
        if old['status'] in ('pending','sent','outcome_unknown','completed_invalid'):raise provider.ProviderError('此检查点的远端结果未知，不自动重复请求',unknown=True)
    session_id=manifest.get('writing_session_id')
    if session_id:
        session=db.one('SELECT policy_json FROM writing_sessions WHERE id=?',(session_id,))
        limit=db.loads(session['policy_json'],{}).get('budget_limit') if session else None
        from ..budget_usage import charge
        used=charge(task['project_id'],manifest_scope={'writing_session_id':session_id})
        from ..summary_billing import request_reservation,BudgetExceeded
        reservation,_=request_reservation(cfg,system,db.dumps(frame))
        if limit is not None and used+reservation>limit:raise BudgetExceeded('写作会话预算不足，已完成阶段保留')
    diagnostic_id=manifest.get('diagnostic_run_id')
    if diagnostic_id:
        run=db.one('SELECT policy_json FROM diagnostic_runs WHERE id=? AND project_id=?',(diagnostic_id,task['project_id']))
        if not run:fail('NOT_FOUND','诊断运行不存在',404)
        from ..budget_usage import charge
        limit=db.loads(run['policy_json'],{})['budget_limit'];used=charge(task['project_id'],manifest_scope={'diagnostic_run_id':diagnostic_id})
        from ..summary_billing import request_reservation,BudgetExceeded
        reservation,_=request_reservation(cfg,system,db.dumps(frame))
        if used+reservation>limit:raise BudgetExceeded('诊断运行预算不足，已完成样本保留')
    requested_route=p['configuration'].get('route','primary')
    routed_task=task
    if requested_route=='gpt':
        routed_task={**task,'payload':db.dumps({**db.loads(task.get('payload'),{}),'provider_profile':'gpt'})}
    with provider.request_scope(cfg,key,route='gpt' if requested_route=='gpt' else 'deepseek'):
        from .billing import sample
        before=sample(cfg) if cfg.get('reconcile_billing') else None
        try:result=model_call(routed_task,system,db.dumps(frame),{**manifest,'agent_frame_hash':frame_hash,'agent_transport':True,'provider_route':requested_route,'automatic_retry_index':0})
        except provider.ProviderError as error:
            if error.response_result:
                observed,actual,actual_key,same_route=_observation_scope(task,p,cfg,key,error.response_result,frame_hash)
                _observe_billing(task,frame_hash,before,actual,actual_key,same_route,error.response_result,cfg.get('reconcile_billing',False))
                if observed:record_reasoning_observation(task['project_id'],observed['id'],actual,error.response_result)
            raise
        observed,actual,actual_key,same_route=_observation_scope(task,p,cfg,key,result,frame_hash)
        _observe_billing(task,frame_hash,before,actual,actual_key,same_route,result,cfg.get('reconcile_billing',False))
    parsed=json.loads(result['text'])
    if observed:
        record_reasoning_observation(task['project_id'],observed['id'],actual,result)
        evidence={'run_request_hash':result['request_hash'],'scope':'this_sample'}
        record_capability(task['project_id'],observed['id'],'text_generation','supported',evidence)
        if parsed.get('tool_calls'):record_capability(task['project_id'],observed['id'],'tools','supported',evidence)
    return finish_structured(task,profile,system,payload,manifest,parsed,aliases,tools,messages,items,frame_hash)

def _observation_scope(task,requested,cfg,key,result,frame_hash):
    """Attribute a routed response to its saved transport, never its requested model."""
    run_id=result.get('run_id')
    row=db.one('SELECT * FROM generation_runs WHERE id=? AND task_id=?',(run_id,task['id'])) if run_id else None
    if row is None:
        row=db.one("SELECT * FROM generation_runs WHERE task_id=? AND status IN ('completed','completed_invalid') AND json_extract(manifest,'$.agent_frame_hash')=? ORDER BY rowid DESC LIMIT 1",(task['id'],frame_hash))
    meta=db.loads(row['manifest'],{}) if row else {}
    from .. import provider_identity
    if row:
        actual=provider_identity.config_for(row)
        identity=provider_identity.effective(row)['effective']
    else:
        actual=result.get('actual_provider_config') or {**cfg,**result.get('provider_snapshot',{})}
        identity={**actual,'credential_hash':result.get('actual_provider_credential_hash')}
    from ..summary_billing import key_hash
    same_route=all(actual.get(name)==cfg.get(name) for name in ('base_url','model','protocol')) and identity.get('credential_hash',key_hash(key))==key_hash(key)
    observed=requested if same_route else None
    actual_key=key if same_route else None
    if not same_route and meta.get('provider_route')=='gpt':
        from .. import security,model_routing
        candidate=security.get_key(model_routing.PROFILE)
        if candidate and key_hash(candidate)==identity.get('credential_hash'):
            actual_key=candidate
            from . import profiles
            configuration={name:value for name,value in requested['configuration'].items() if name in ('tool_mode','max_turns','max_tool_calls')}
            configuration['route']='gpt'
            for name in ('max_output_tokens','context_limit','timeout_seconds'):
                if name in actual:configuration[name]=int(actual[name])
            for name in ('reasoning_effort','reconcile_billing'):
                if name in actual:configuration[name]=actual[name]
            with provider.request_scope(actual,actual_key,route='gpt'):
                resolved,_=profiles.resolve_config(configuration)
                stamp=profiles.fingerprint(resolved,actual_key)
                existing=db.one('SELECT id FROM writing_profiles WHERE project_id=? AND role=? AND route_fingerprint=? AND configuration_json=? ORDER BY created_at DESC LIMIT 1',
                                (task['project_id'],requested['role'],stamp,db.dumps(configuration)))
                observed=profiles.get(task['project_id'],existing['id']) if existing else profiles.create(task['project_id'],{'role':requested['role'],'configuration':configuration})
    if row:
        meta={**meta,'agent_requested_profile_id':requested['id'],'agent_observation_profile_id':observed['id'] if observed else None}
        if not same_route:meta['agent_provider_changed']=True
        db.execute('UPDATE generation_runs SET manifest=? WHERE id=?',(db.dumps(meta),row['id']))
    return observed,actual,actual_key,same_route


def _observe_billing(task,frame_hash,before,cfg,key,same_route,result,reconcile):
    if not reconcile or not key:return
    from .billing import sample
    with provider.request_scope(cfg,key,route='gpt' if not same_route else None):
        after=sample(cfg)
    # A primary-account baseline can never prove the charge of a GPT response.
    # Preserve an actual-account after sample for later adjacent reconciliation.
    if before or after:save_billing_interval(task['id'],frame_hash,before if same_route else None,after,result['usage'])


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
    usage=result.get('usage',{});usage=usage if isinstance(usage,dict) else {}
    raw=usage.get('raw',{});raw=raw if isinstance(raw,dict) else {}
    details=raw.get('completion_tokens_details',{}) or raw.get('output_tokens_details',{})
    details=details if isinstance(details,dict) else {}
    tokens=details.get('reasoning_tokens',raw.get('completion_thinking_tokens'))
    if type(tokens) is int and tokens>0:
        observed={'reported_reasoning_tokens':tokens,'scope':'this_response_not_a_tokenizer_or_semantic_quality_proof'}
        record_capability(pid,profile_id,'reasoning_usage','supported',observed)
        if cfg.get('thinking_enabled') is False:record_capability(pid,profile_id,'reasoning_parameter','unknown',{**observed,'requested_thinking_enabled':False,'observation':'disabled_requested_but_reasoning_usage_reported; do_not_assume_visible_output_budget'})

def finish_structured(task,profile,system,payload,manifest,envelope,aliases,tools,messages,items,frame_hash=None):
    """At most five complete JSON attempts; never retries unknown sends."""
    from .identifiers import decode,repair_identifier_keys,STAGES
    structured=not tools and (manifest.get('stage') in STAGES|{'investigate','diagnostic_model_review'} or manifest.get('diagnostic_run_id'))
    if not structured or envelope.get('tool_calls'):return envelope
    try:parsed=provider.parse_json(envelope['text'])
    except (ValueError,TypeError,provider.ProviderError) as error:
        if isinstance(error,provider.ProviderError) and (error.unknown or error.response_result is not None or getattr(error,'failure_phase',None)):raise
        attempt=int(manifest.get('structured_json_attempt',0))
        if attempt>=4:raise
        # Keep the first full response. Repair parameters (or a legacy system
        # instruction) give the next attempt its own reproducible checkpoint.
        from .. import prompt_layout
        if prompt_layout.enabled(task) and messages is None and items is None and not tools:
            parameters=payload.get(prompt_layout.PARAMETERS,{})
            if not isinstance(parameters,dict):parameters={'original_parameters':parameters}
            repair_payload={**payload,prompt_layout.PARAMETERS:{**parameters,
                'structured_json_attempt':attempt+2,'structured_json_maximum':5,
                'prior_json_error':str(error)[:300]}}
            return call(task,profile,system,repair_payload,
                {**manifest,'structured_json_attempt':attempt+1,'prior_json_error':str(error)[:300]},tools,messages,items)
        note=f' 第{attempt+2}/5次格式尝试。上一次完整响应不是合法JSON。请仅根据同一批原始材料重新返回本阶段JSON，严格闭合键名、字符串和数组，不输出说明或Markdown；不要改变事实与任务。'
        return call(task,profile,system+note,payload,{**manifest,'structured_json_attempt':attempt+1,'prior_json_error':str(error)[:300]},tools,messages,items)
    if aliases:
        parsed,repairs=repair_identifier_keys(parsed,aliases)
        if repairs and frame_hash:
            row=db.one("SELECT id,manifest FROM generation_runs WHERE task_id=? AND json_extract(manifest,'$.agent_frame_hash')=? ORDER BY rowid DESC LIMIT 1",(task['id'],frame_hash))
            if row:
                meta=db.loads(row['manifest'],{});meta['output_identifier_key_repairs']=repairs;db.execute('UPDATE generation_runs SET manifest=? WHERE id=?',(db.dumps(meta),row['id']))
        envelope={**envelope,'text':db.dumps(decode(parsed,aliases)),'identifier_aliases':aliases}
    return envelope

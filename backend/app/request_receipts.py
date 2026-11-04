"""Capability-gated read-only remote receipt lookup; never regenerate an unknown request."""
from urllib.parse import quote
from fastapi import APIRouter, HTTPException
from . import adapter,db,provider,security

router=APIRouter(prefix='/request-runs')


def _credential(run,identity):
    from . import deepseek_pool,model_routing
    candidates=[(provider.config(),provider.get_key()),(model_routing.config(),security.get_key(model_routing.PROFILE))]
    for endpoint in deepseek_pool.config()['endpoints']:
        candidates.append((deepseek_pool._endpoint_to_provider(endpoint),deepseek_pool.endpoint_key(endpoint)))
    for cfg,key in candidates:
        if key and all(cfg.get(name)==identity.get(name) for name in ('base_url','model','protocol')) and adapter.credential_hash(key)==identity.get('credential_hash'):
            return cfg,key
    raise HTTPException(409,'实际请求账户的凭据已变更或不可用；保留未知状态')


@router.get('/{run_id}/execution')
def execution_status(run_id):
    run=db.one('SELECT * FROM generation_runs WHERE id=?',(run_id,))
    if not run:raise HTTPException(404,'请求记录不存在')
    intent=db.one('SELECT i.* FROM request_intents i JOIN request_intent_runs r ON r.intent_id=i.id WHERE r.generation_run_id=?',(run_id,))
    attempts=db.rows('SELECT ordinal,state,endpoint_id,identity,reserved,usage,cost,error_code,created_at,updated_at FROM provider_attempts WHERE intent_id=? ORDER BY ordinal',(intent['id'],)) if intent else []
    for attempt in attempts:
        identity=db.loads(attempt.pop('identity'),{})
        attempt['provider']={name:identity.get(name) for name in ('base_url','model','protocol','currency')}
        attempt['usage']=db.loads(attempt['usage'],{})
    return {'status':run['status'],'intent':{'state':intent['state'],'stage':intent['stage']} if intent else None,
            'attempts':attempts,'can_regenerate_automatically':False,'scope':'已保存执行证据，不表示人工语义核验通过'}


@router.post('/{run_id}/lookup')
def lookup(run_id):
    run=db.one('SELECT * FROM generation_runs WHERE id=?',(run_id,))
    if not run:raise HTTPException(404,'请求记录不存在')
    if run['status'] in ('completed','completed_invalid'):
        return {'recovered':False,'status':run['status'],'reason':'响应已在本地保存，未发送查询或生成请求'}
    if run['status'] not in ('outcome_unknown','sent','pending'):
        raise HTTPException(409,'该请求不是等待核实状态')
    from .provider_identity import effective
    observed=effective(run)
    actual=observed['effective']
    if not observed['actual_known']:raise HTTPException(409,'没有足够的实际端点身份，不能自动查询回执')
    caps=db.setting('provider_receipt_capabilities',{})
    capability=caps.get(actual.get('base_url'),{}) if isinstance(caps,dict) else {}
    if not (capability.get('verified') is True and capability.get('method')=='GET'
            and capability.get('protocol')=='responses' and actual.get('protocol')=='responses'
            and capability.get('path')=='/v1/responses/{response_id}'
            and capability.get('credential_hash')==actual.get('credential_hash')):
        return {'recovered':False,'status':run['status'],'reason':'此端点没有已验证的只读回执查询能力；未发送任何请求'}
    remote_id=run.get('remote_id')
    if not isinstance(remote_id,str) or not remote_id:raise HTTPException(409,'没有可核对的远端响应ID；保留未知状态')
    cfg,key=_credential(run,actual)
    from .tasks import _validate_model_source_inputs,_validate_model_presend
    manifest=db.loads(run['manifest'],{})
    _validate_model_source_inputs(run['project_id'],manifest)
    task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(run['task_id'],run['project_id']))
    if not task:raise HTTPException(409,'请求所属任务不存在')
    _validate_model_presend(task,manifest)
    with provider.request_scope(cfg,key):
        with provider.client(cfg) as connection:
            response=connection.get(provider.endpoint(cfg['base_url'],'/v1/responses/'+quote(remote_id,safe='')),
                                    headers=provider.headers(cfg['protocol']))
    if response.status_code!=200:return {'recovered':False,'status':run['status'],'reason':'回执未取得，原未知状态和预留保持不变'}
    try:data=response.json()
    except ValueError:raise HTTPException(409,'回执不是有效JSON；未改变原状态')
    if not isinstance(data,dict) or data.get('id')!=remote_id or data.get('model')!=actual.get('model'):
        raise HTTPException(409,'回执身份与保存的实际请求不一致')
    if data.get('status')!='completed':return {'recovered':False,'status':run['status'],'reason':'远端响应尚未完整完成'}
    text=''.join(piece.get('text','') for item in data.get('output',[]) if isinstance(item,dict)
                 for piece in item.get('content',[]) if isinstance(piece,dict) and piece.get('type')=='output_text')
    if manifest.get('agent_transport'):
        output=data.get('output',[])
        calls=[{'id':item.get('call_id'),'name':item.get('name'),'arguments':item.get('arguments','{}')}
               for item in output if isinstance(item,dict) and item.get('type')=='function_call']
        if not text and not calls:raise HTTPException(409,'回执没有可恢复正文或工具调用')
        text=db.dumps({'agent_transport':True,'text':text,'tool_calls':calls,'message':None,
                       'items':output,'finish':'completed','reported_model':data.get('model')})
    if not text:raise HTTPException(409,'回执没有可恢复正文')
    _validate_model_source_inputs(run['project_id'],manifest)
    _validate_model_presend(task,manifest)
    with db.transaction() as connection:
        fresh=connection.execute('SELECT status FROM generation_runs WHERE id=?',(run_id,)).fetchone()
        if fresh['status'] not in ('outcome_unknown','sent','pending'):raise HTTPException(409,'查询期间本地请求状态已变化')
        saved={**manifest,'output_blob':security.put_blob(text.encode()),'receipt_recovery':{
            'method':'verified_read_only_response_lookup','remote_id':remote_id,'recovered_at':db.now(),
            'quality_verified':False,'new_generation_requests':0}}
        connection.execute("UPDATE generation_runs SET status='completed',usage=?,manifest=?,error=NULL WHERE id=?",
                           (db.dumps(adapter.normalize_usage(data.get('usage'),cfg['protocol'])),db.dumps(saved),run_id))
        db.audit(run['project_id'],'request_receipt_recovered',{'run_id':run_id,'task_id':run['task_id'],'new_generation_requests':0})
    return {'recovered':True,'status':'completed','task_resumed':False,
            'reason':'已保存原远端响应；来源/规则及候选质量仍按原恢复流程核验，未自动采用或重发'}

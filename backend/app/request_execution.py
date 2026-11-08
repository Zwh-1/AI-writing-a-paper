"""Durable logical intents and actual transport attempts, without replaying unknown work."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib,time
import json
from . import db

_SCOPE=ContextVar('request_execution_scope',default=None)
_LOGICAL=ContextVar('request_logical_intent',default=None)


@contextmanager
def logical_scope():
    if _LOGICAL.get() is not None:
        yield
        return
    token=_LOGICAL.set({'id':None})
    try:yield
    finally:_LOGICAL.reset(token)


def _event(state,kind,detail=None,attempt_id=None):
    db.execute('INSERT INTO request_execution_events(intent_id,attempt_id,event_type,detail,created_at) VALUES(?,?,?,?,?)',
               (state['id'],attempt_id,kind,db.dumps(detail or {}),db.now()))


@contextmanager
def scope(task,run_id,manifest,configuration,system,user,images=None,preflight=None):
    group=_LOGICAL.get()
    identity=(group or {}).get('id') or db.uid();stamp=db.now()
    state={'id':identity,'task':task,'run_id':run_id,'manifest':manifest,'cfg':configuration,
           'system':system,'user':user,'images':images,'preflight':preflight,'active':None,'last':None}
    if not group or group['id'] is None:
        db.execute('INSERT INTO request_intents VALUES(?,?,?,?,?,?,?,?,?,?)',(
            identity,task['project_id'],task['id'],run_id,task.get('attempt',0),
            manifest.get('writing_stage',manifest.get('stage',task['kind'])),
            hashlib.sha256((system+'\n'+user).encode()).hexdigest(),'prepared',stamp,stamp))
        if group is not None:group['id']=identity
    db.execute('INSERT INTO request_intent_runs VALUES(?,?)',(identity,run_id))
    if group is not None:group['latest_run_id']=run_id
    _event(state,'prepared')
    token=_SCOPE.set(state)
    try:yield state
    finally:
        try:
            run=db.one('SELECT status,usage,cost,remote_id,manifest FROM generation_runs WHERE id=?',(run_id,))
            if run:
                status=run['status']
                terminal=('persisted' if status in ('completed','completed_invalid','manually_settled') else
                          'known_rejected' if status in ('rejected','failed_before_send') else 'outcome_unknown')
                # Unwinding earlier rejected retries must not replace the
                # final successful result of this same logical intent.
                if group is None or group.get('latest_run_id')==run_id:
                    db.execute("UPDATE request_intents SET state=?,updated_at=? WHERE id=? AND (state!='persisted' OR ?='persisted')",
                               (terminal,db.now(),identity,terminal))
                last=state['last']
                if last and terminal=='persisted':
                    meta=db.loads(run['manifest'],{})
                    from .provider_identity import effective
                    actual=effective({**run,'manifest':run['manifest'],'model':configuration.get('model'),
                                      'protocol':configuration.get('protocol')})['effective']
                    db.execute('UPDATE provider_attempts SET state=?,identity=?,usage=?,cost=?,remote_id=?,updated_at=? WHERE id=?',
                        ('persisted',db.dumps(actual),run['usage'],run['cost'],run['remote_id'],db.now(),last['id']))
                    _event(state,'persisted',{'generation_status':status},last['id'])
                else:_event(state,terminal,{'generation_status':status})
        finally:_SCOPE.reset(token)


@contextmanager
def attempt(endpoint_id=None):
    state=_SCOPE.get()
    if state is None or state['active'] is not None:
        yield
        return
    with db.transaction() as connection:
        ordinal=connection.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM provider_attempts WHERE intent_id=?',
                                   (state['id'],)).fetchone()[0]
        value={'id':db.uid(),'ordinal':ordinal,'phase':'prepared'}
        stamp=db.now()
        connection.execute('INSERT INTO provider_attempts(id,intent_id,ordinal,endpoint_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                           (value['id'],state['id'],ordinal,endpoint_id,'prepared',stamp,stamp))
        _event(state,'attempt_prepared',{'ordinal':ordinal,'endpoint_id':endpoint_id},value['id'])
    state['active']=value;state['last']=value
    try:
        yield value
    except Exception as error:
        phase=getattr(error,'failure_phase',None)
        safe=(not getattr(error,'unknown',False) and getattr(error,'response_result',None) is None
              and (getattr(error,'safe_failure',False) or getattr(error,'fallback_allowed',False)
                   or getattr(error,'retryable',False) or phase in ('connect_before_http','policy_before_http','cancel_before_http')))
        evidence=getattr(error,'response_evidence',{}) or {}
        http_status=evidence.get('http_status')
        known_rejection=(not getattr(error,'unknown',False) and getattr(error,'response_result',None) is None
                         and type(http_status) is int and 400<=http_status<500)
        status=('outcome_unknown' if getattr(error,'unknown',False) else
                'response_received' if getattr(error,'response_result',None) is not None else
                'known_rejected' if safe or known_rejection or value['phase'] in ('prepared','reserved') else 'outcome_unknown')
        code=getattr(error,'code',type(error).__name__)
        db.execute('UPDATE provider_attempts SET state=?,error_code=?,updated_at=? WHERE id=?',
                   (status,str(code)[:100],db.now(),value['id']))
        _event(state,status,{'error_code':str(code)[:100],'failure_phase':phase},value['id'])
        raise
    else:
        db.execute('UPDATE provider_attempts SET state=?,updated_at=? WHERE id=?',('response_received',db.now(),value['id']))
    finally:state['active']=None


def before_http(request,configuration,key):
    state=_SCOPE.get()
    if state is None or request.method not in ('POST','PUT','PATCH'):return
    value=state['active']
    if value is None:return
    from .task_transitions import assert_current
    assert_current(state['task'])
    # The transport's frozen config and key are the actual endpoint identity.
    cfg=dict(configuration)
    try:body=json.loads(request.content)
    except (TypeError,ValueError):body={}
    if isinstance(body,dict) and isinstance(body.get('model'),str):cfg['model']=body['model']
    identity={name:cfg.get(name) for name in ('base_url','model','protocol','currency','provider_endpoint_id')}
    identity['credential_hash']=hashlib.sha256(str(key or '').encode()).hexdigest()
    db.execute('UPDATE provider_attempts SET identity=?,updated_at=? WHERE id=?',
               (db.dumps(identity),db.now(),value['id']))
    try:
        reserved=state['preflight'](cfg,key) if state['preflight'] else 0
    except Exception as error:
        error.failure_phase='attempt_preflight_before_http'
        raise
    value['phase']='reserved'
    db.execute('UPDATE provider_attempts SET identity=?,reserved=?,state=?,updated_at=? WHERE id=?',
               (db.dumps(identity),reserved,'reserved',db.now(),value['id']))
    _event(state,'reserved',{'reservation':reserved,'currency':cfg.get('currency')},value['id'])
    value['phase']='send_started'
    value['http_started']=time.perf_counter()
    db.execute('UPDATE provider_attempts SET state=?,updated_at=? WHERE id=?',('send_started',db.now(),value['id']))
    db.execute('UPDATE request_intents SET state=?,updated_at=? WHERE id=?',('send_started',db.now(),state['id']))
    _event(state,'send_started',{'method':request.method,'request_sha256':hashlib.sha256(request.content).hexdigest()},value['id'])


def response_received(response):
    state=_SCOPE.get()
    if state is None or state['active'] is None:return
    value=state['active'];value['phase']='response_received'
    if value.get('http_started') is not None:
        from .ai_runtime.telemetry import record
        record(state['task'],state['manifest'].get('stage',state['task']['kind']),
               'provider_response_headers','completed',(time.perf_counter()-value['http_started'])*1000)
    _event(state,'response_received',{'http_status':response.status_code,
           'provider_request_id':response.headers.get('x-request-id')},value['id'])

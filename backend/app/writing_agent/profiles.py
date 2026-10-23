from .. import db,provider,security,model_routing
from .common import owned,digest,fail

ROLES={'investigate','judge','draft','verify','edit','diagnostic'}
OPTIONS={'max_output_tokens','context_limit','timeout_seconds','thinking_enabled','reasoning_effort','tool_mode','max_turns','max_tool_calls','route','reconcile_billing'}

def resolve_config(configuration):
    route=configuration.get('route','primary')
    if route not in ('primary','gpt'):fail('INVALID_ROUTE','模型路线无效')
    cfg=dict(model_routing.config() if route=='gpt' else provider.config());key=security.get_key('gpt-fallback' if route=='gpt' else '')
    for name in ('max_output_tokens','context_limit','timeout_seconds'):
        if name in configuration:
            value=configuration[name]
            if isinstance(value,bool) or not isinstance(value,int) or value<(15 if name=='timeout_seconds' else 128) or value>int(cfg.get(name,100000)):fail('INVALID_PROFILE','阶段预算不能超过已配置上限')
            cfg[name]=value
    if 'thinking_enabled' in configuration:
        if not isinstance(configuration['thinking_enabled'],bool):fail('INVALID_PROFILE','思考配置无效')
        cfg['thinking_enabled']=configuration['thinking_enabled']
    if route=='gpt':cfg.pop('thinking_enabled',None)
    effort=configuration.get('reasoning_effort')
    if effort is not None:
        if effort not in ('low','medium','high','max'):fail('INVALID_PROFILE','推理强度无效')
        cfg['reasoning_effort']=effort
    cfg.update(max_attempts=1,streaming=False,json_mode=True)
    if 'reconcile_billing' in configuration:
        if type(configuration['reconcile_billing']) is not bool:fail('INVALID_PROFILE','账单核对开关无效')
        cfg['reconcile_billing']=configuration['reconcile_billing']
    return cfg,key

def fingerprint(cfg,key=None):
    from . import prompts
    from .models import Judgment,Draft,Verification
    from .verification import Annotation
    from .tools import schemas
    from .coherence import ExpandedReview
    return digest({'configuration':{k:cfg.get(k) for k in ('base_url','model','protocol','max_output_tokens','context_limit','thinking_enabled','reasoning_effort','json_mode','currency','input_price','output_price')},'credential_scope':digest(key) if key else None,'controller_version':'agent.0.4.1.completion2','context_policy':'phase_scoped.v4.expansion.regression','identifier_codec':'typed_aliases.v1','structured_json_policy':'known_complete_retry1','tool_policy':'snapshot_tools.v2.compact_run_records','schema_fingerprint':digest([m.model_json_schema() for m in (Judgment,Draft,Verification,Annotation,ExpandedReview)]),'tool_fingerprint':digest(schemas()),'prompt_fingerprint':digest({k:v for k,v in vars(prompts).items() if k.isupper() and isinstance(v,str)})})

def create(pid,body):
    security.require_project(pid);role=body.get('role','draft');configuration=body.get('configuration',{})
    if role not in ROLES or not isinstance(configuration,dict) or set(configuration)-OPTIONS:fail('INVALID_PROFILE','阶段配置无效')
    if configuration.get('tool_mode','json') not in ('json','native'):fail('INVALID_PROFILE','工具模式无效')
    for name,maximum in (('max_turns',6),('max_tool_calls',24)):
        if name in configuration and (type(configuration[name]) is not int or not 1<=configuration[name]<=maximum):fail('INVALID_PROFILE','工具轮数超出保护上限')
    cfg,key=resolve_config(configuration);identity=db.uid()
    capability={name:'unknown' for name in ('text_generation','json_object','strict_schema','tools','tool_state','streaming','reasoning_parameter','reasoning_usage','response_lookup')}
    db.execute('INSERT INTO writing_profiles VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,pid,role,fingerprint(cfg,key),db.dumps(capability),db.dumps(configuration),None,'unassessed',db.dumps({'adapter':'agent.v1','prompt':'agent.v1'}),db.now()))
    return get(pid,identity)

def get(pid,identity):
    r=owned('profile',identity,pid)
    cfg,key=resolve_config(db.loads(r['configuration_json'],{}))
    current=r['route_fingerprint']==fingerprint(cfg,key)
    evaluation_current=True
    if r['qualification']=='qualified_for_policy':
        evaluation=db.one('SELECT dataset_hash FROM diagnostic_runs WHERE id=? AND project_id=?',(r['evaluation_ref'],pid))
        from .evaluations import dataset
        evaluation_current=bool(evaluation and evaluation['dataset_hash']==digest(dataset()))
    return {**r,'qualification':r['qualification'] if current and evaluation_current else 'needs_review','recorded_qualification':r['qualification'],'configuration':db.loads(r['configuration_json'],{}),'capabilities':db.loads(r['capability_json'],{}),'configuration_current':current,'qualification_current':current and evaluation_current,'actual_config':{k:cfg.get(k) for k in ('model','protocol','context_limit','max_output_tokens','thinking_enabled','reasoning_effort')}}

def record_capability(pid,identity,name,value,evidence):
    r=owned('profile',identity,pid);caps=db.loads(r['capability_json'],{})
    caps[name]={'status':value,'evidence':evidence,'at':db.now()}
    db.execute('UPDATE writing_profiles SET capability_json=? WHERE id=?',(db.dumps(caps),identity))

def for_session(pid,identity):
    p=get(pid,identity)
    if not p['configuration_current']:fail('PROFILE_STALE','实际模型配置已变化，请建立新阶段配置',409)
    cfg,key=resolve_config(p['configuration'])
    if not key:fail('PROVIDER_KEY_MISSING','接口尚未配置密钥')
    return p,cfg,key

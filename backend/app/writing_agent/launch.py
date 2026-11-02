"""Check the actual first request before a user starts a multi-stage session."""
import math
from fastapi import HTTPException
from .. import db,security,provider,summary_billing
from ..source_payload import token_upper_bound
from . import profiles,context,tools,prompts,contracts
from .common import state,section

PHASE_SCOPE='本阶段只调查和判定代码命题及其必要前提；保留明确的章节依赖。完整正文表达与可选邻接内容在后续生成、语义、衔接阶段检查，不能在本阶段声称已通读它们。'

def preflight(pid,sid,profile_id=None,budget_limit=None):
    p=security.require_project(pid);s=section(sid,pid);head=state(pid)
    result={'can_start':False,'blockers':[],'warnings':[],'section_id':sid,'context':None,'budget':None}
    def block(code,message,**details):result['blockers'].append({'code':code,'message':message,**details})
    if budget_limit is not None and (not math.isfinite(budget_limit) or budget_limit<=0):block('INVALID_POLICY','会话预算必须是有效正数')
    cfg=provider.config()
    try:
        if s['state']=='archived':block('SECTION_ARCHIVED','已归档章节不能生成')
        if s['kind']=='abstract_en':block('ABSTRACT_TRANSLATION_REQUIRED','英文摘要请打开正文，翻译已保存的中文摘要')
        if not p['rule_package_id']:block('RULE_PACKAGE_REQUIRED','请先发布本项目论文规则包')
        if profile_id:_,cfg,_=profiles.for_session(pid,profile_id)
        else:
            cfg,key=profiles.resolve_config({'route':'primary'})
            if not key:block('PROVIDER_KEY_MISSING','请先在“接口与设置”中保存模型接口密钥')
            if not cfg.get('model'):block('PROVIDER_MODEL_MISSING','请先在“接口与设置”中选择写作模型')
    except HTTPException as error:
        detail=error.detail if isinstance(error.detail,dict) else {'message':str(error.detail)}
        block(detail.get('code','WRITING_NOT_READY'),detail.get('message','模型配置不可用'))
    try:initial,_=summary_billing.request_reservation(cfg,'','')
    except summary_billing.BudgetExceeded as error:
        initial=float(cfg.get('max_request_cost',2));block('REQUEST_BUDGET_EXCEEDED',str(error))
    available=summary_billing.budget(pid,initial)
    result['budget']={**available,'currency':cfg.get('currency','USD'),'request_reservation':initial,'session_limit':budget_limit if budget_limit is not None else available['available']}
    result['default_budget']=available['available']
    if not result['blockers']:
        # Context assembly can register derived evidence and build a missing baseline.
        # A savepoint keeps this preview out of project history and the worker queue.
        with db.transaction() as connection:
            connection.execute('SAVEPOINT writing_launch_preview')
            try:
                profile=profiles.for_session(pid,profile_id)[0] if profile_id else profiles.current_default(pid)
                cfg,_=profiles.resolve_config(profile['configuration'])
                if not head['core_version_id'] or not head['target_snapshot_id'] or not head['contracts'].get(sid):
                    contracts.bootstrap_project(pid);head=state(pid)
                payload,manifest=context.assemble(pid,sid,head['contracts'][sid],profile,True)
                from ..writing_continuity import status as manuscript_status
                paper=manuscript_status(pid,sid,cfg);result['paper_context']=paper
                if not paper['can_load']:block(paper['code'],paper['message'])
                payload.pop('existing_document',None)
                payload.pop('current_section_context',None);payload.pop('paper_context',None);payload.pop('continuity',None)
                payload['related_sections']=[item for item in payload.get('related_sections',[]) if item.get('scope')=='full_required_dependency']
                payload['phase_scope']=PHASE_SCOPE
                from .packing import fit,frame as request_frame
                request_cfg={**cfg,'tool_mode':profile['configuration'].get('tool_mode','json')}
                manifest['stage']='investigate';fit(payload,manifest,prompts.INVESTIGATE,request_cfg)
                request_system,frame=request_frame(payload,manifest,prompts.INVESTIGATE,request_cfg)
                serialized=db.dumps(frame);estimate=token_upper_bound(request_system,serialized)
                output=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',32000))
                result['context']={'input_estimate':estimate,'reserved_output':output,'limit':limit,'rule_count':len(payload['rules']),'evidence_count':len(payload['evidence']),'field_estimates':{name:token_upper_bound('',db.dumps(payload.get(name))) for name in ('rules','evidence','references','contract','thesis_core','related_sections')},'estimate_method':'actual_first_wire_frame_utf8_bytes_upper_bound'}
                result['context']['selection']={'rules':manifest.get('rule_selection'),'sources':manifest.get('source_selection')}
                if manifest.get('rule_selection'):result['warnings'].append(f'本次完整载入 {len(manifest["rule_selection"]["mandatory_ids"])} 条强制规则，并按章节相关性检索建议；{len(manifest["rule_selection"]["deferred_recommended_ids"])} 条未载入建议保留在规则包中，不视为已核验。')
                if manifest.get('source_selection'):result['warnings'].append(f'本次完整提供 {len(manifest["source_selection"]["provided_ids"])} 项资料片段，另有 {len(manifest["source_selection"]["catalogue_only_ids"])} 项作为可读取目录，未裁剪原文。')
                if estimate+output>limit:block('CONTEXT_BUDGET_EXCEEDED',f'本节首个请求的保守输入估算为 {estimate:,} token，另需预留输出 {output:,}，超过当前上下文上限 {limit:,}。请缩小任务证据范围、核对适用规则，或使用已验证支持更大上下文的配置。')
                reservation,cost=summary_billing.request_reservation(cfg,request_system,serialized)
                result['budget'].update(request_reservation=reservation,prices_known=cost['prices_known'])
            except HTTPException as error:
                detail=error.detail if isinstance(error.detail,dict) else {'message':str(error.detail)}
                block(detail.get('code','WRITING_NOT_READY'),detail.get('message','章节写作条件不完整'))
            except summary_billing.BudgetExceeded as error:block('REQUEST_BUDGET_EXCEEDED',str(error))
            finally:
                connection.execute('ROLLBACK TO SAVEPOINT writing_launch_preview')
                connection.execute('RELEASE SAVEPOINT writing_launch_preview')
    budget=result['budget'];reservation=budget['request_reservation'];currency=budget['currency']
    if budget['available']+1e-9<reservation:block('PROJECT_BUDGET_INSUFFICIENT',f'项目可用预算为 {budget["available"]:.4f} {currency}，首个请求需预留 {reservation:.4f} {currency}。请先核对费用预留或调整项目预算。')
    if budget['session_limit']<=0 or (budget_limit is not None and budget['session_limit']+1e-9<reservation):block('SESSION_BUDGET_INSUFFICIENT','会话预算不足以容纳首个请求的预留。')
    if budget.get('prices_known') is False:
        result['warnings'].append(f'尚未配置模型单价，每次请求按单次上限预留；基础写作流程包含 7 个阶段，调查及复核可能增加请求。7 次预留合计 {7*reservation:.4f} {currency}，这不是实际费用。')
    result['can_start']=not result['blockers']
    return result

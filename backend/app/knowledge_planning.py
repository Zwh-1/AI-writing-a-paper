"""Explainable routing and evidence-bound argument plans, without paid planning."""
from fastapi import HTTPException
import math
from . import db,knowledge_lineage as lineage

def intent(question):
    if any(t in question for t in ('探索','发现新的','还有哪些研究','开放问题','研究空白')):return 'exploration'
    if any(t in question for t in ('矛盾','分歧','相反','限制')):return 'disagreement'
    if any(t in question for t in ('研究现状','综述','比较','对比','研究路线','全库','总体')):return 'synthesis'
    if any(t in question for t in ('关系','依赖','调用','哪些方法','哪些模块')):return 'relation'
    return 'precise'

def plan(pid,question,claims,reading_plan,refs):
    allowed={(r['type'],r['id']) for r in refs};arguments=[];comparisons=[]
    for claim in claims:
        cid=claim['claim_version_id']
        if ('assertion',cid) not in allowed:continue
        data=claim.get('object',{});limitations=claim.get('conditions',[])
        sources=claim.get('sources',[]);opposing=[e for e in claim.get('evidence',[]) if e.get('relation') in ('limits','contradicts')]
        negative=claim.get('polarity')=='negative' or claim.get('support_state')=='contradicted'
        argument={'claim_id':cid,'statement':claim['proposition']['statement'],'attribution':claim.get('attribution'),
                  'claim_ref':next(r for r in refs if r['type']=='assertion' and r['id']==cid),
                  'support_sources':[] if negative else sources,'counter_sources':sources if negative else opposing,
                  'conditions':limitations,'support_state':claim.get('support_state','not_checked'),
                  'human_reviewed':bool(claim.get('human_reviewed')),'requires_adjudication':claim.get('requires_adjudication',False),
                  'adoption':'requires_original_reading_and_claim_specific_review'}
        arguments.append(argument)
        comparisons.append({'claim_id':cid,'metric':data.get('metric'),'value':data.get('value'),'unit':data.get('unit'),
                            'comparator':data.get('comparator'),'conditions':limitations,'attribution':claim.get('attribution')})
    axes=[]
    for i,left in enumerate(comparisons):
        for right in comparisons[i+1:]:
            if left['attribution']==right['attribution']:continue
            missing=[name for name in ('metric','unit','comparator') if not left[name] or not right[name] or left[name] in ('未知','unknown') or right[name] in ('未知','unknown')]
            mismatched=[name for name in ('metric','unit','comparator','conditions') if left[name]!=right[name]]
            axes.append({'claim_ids':[left['claim_id'],right['claim_id']],'status':'needs_original_comparability_review' if not missing and not mismatched else 'not_directly_comparable',
                         'missing_dimensions':missing,'different_dimensions':mismatched,'automatic_ranking_allowed':False})
            if len(axes)>=60:break
        if len(axes)>=60:break
    gaps=[]
    if not arguments:gaps.append('当前范围未定位到可组织的图谱论点，请补读原文后建立候选')
    if not any(a['counter_sources'] for a in arguments):gaps.append('未定位到反证不等于不存在反证，应继续检索限制与不同结论')
    if any(a['support_state']=='not_checked' for a in arguments):gaps.append('部分论点支持性尚未核对，不能直接作为已证实结论')
    return {'schema':'knowledge.argument-plan.v1','question':question,'intent':intent(question),'candidate_arguments':arguments,
            'comparison_dimensions':comparisons,'comparability_checks':axes,'reading_ranges':reading_plan,
            'source_coverage':len({p.get('file_id') or p.get('evidence_id') for p in reading_plan}),
            'missing_materials':gaps,'originals_read':False,'semantic_quality_verified':False}

def route_plan(pid,body):
    from .graphrag_adapter import capabilities
    wanted=body.get('backend','auto');question=body.get('query','');ns=body.get('namespace','literature')
    if wanted not in ('auto','native','global','local','drift'):lineage.fail('INVALID_BACKEND','查询后端无效',422)
    category=intent(question);recommended='drift' if category=='exploration' else 'global' if category in ('synthesis','disagreement') else 'local' if category=='relation' else 'native'
    mode=recommended if wanted=='auto' else wanted
    result={'intent':category,'requested_backend':wanted,'recommended_backend':recommended,'selected_backend':'native','artifact_id':None,'reasons':[],'paid':False}
    if mode=='native':return result
    if body.get('allow_model') is not True:
        result['reasons'].append('本次未启用收费查询，保留原生检索与阅读计划');return result
    if not capabilities()['installed']:result['reasons'].append('固定版本微软组件未安装');return result
    from .expense_evidence import preflight,resolve_config
    from . import provider
    from .summary_billing import BudgetExceeded
    try:
        cfg=resolve_config(pid,provider.config(),provider.get_key())
        with db.transaction():preflight(pid,cfg,provider.get_key())
    except (BudgetExceeded,ValueError,HTTPException) as error:
        result['reasons'].append(str(error));return result
    if type(body.get('budget_limit')) not in (int,float) or not math.isfinite(body['budget_limit']) or body['budget_limit']<=0:
        result['reasons'].append('收费探索须提供明确的正数任务预算');return result
    candidates=db.rows("SELECT * FROM knowledge_artifacts WHERE project_id=? AND kind='graphrag_index' AND state='ready' ORDER BY created_at DESC",(pid,))
    if body.get('artifact_id'):candidates=[r for r in candidates if r['id']==body['artifact_id']]
    for artifact in candidates:
        manifest=db.loads(artifact['manifest_json'],{});packet=manifest.get('packet',{})
        if body.get('file_ids') and not set(manifest.get('source_files',[]))<=set(body['file_ids']):continue
        if ns!='all' and manifest.get('namespace') not in (ns,'all'):continue
        if not packet.get('reports') or mode in ('local','drift') and not packet.get('embeddings'):continue
        try:
            lineage.version_of(pid,'artifact',artifact['id'],True)
            if mode in ('local','drift'):
                from . import retrieval
                if packet.get('embedding_model')!=retrieval.model_key(retrieval.config(pid)['model']):continue
        except (HTTPException,ValueError):continue
        return {**result,'selected_backend':mode,'artifact_id':artifact['id'],'paid':True}
    result['reasons'].append('没有当前来源、报告和向量均匹配的可用微软索引')
    return result

def execute(task,body):
    from .knowledge_router import query
    from .graphrag_adapter import query as official_query
    pid=task['project_id'];decision=route_plan(pid,body)
    if body.get('backend') in ('global','local','drift') and decision['selected_backend']!=body['backend']:
        lineage.fail('REQUESTED_BACKEND_UNAVAILABLE','；'.join(decision['reasons']),422)
    native=query(pid,body,remote=decision['paid']);native['route_decision']=decision
    if decision['paid']:
        output=official_query(task,{**body,'mode':decision['selected_backend'],'artifact_id':decision['artifact_id']})
        # Keep upstream errors visible. Never mark native fallback as official success.
        native['official_result']=output;native['official_refs']=[lineage.ref(pid,'artifact',decision['artifact_id'],True)]
        artifact=lineage.owned(pid,'artifact',decision['artifact_id']);mapping=db.loads(artifact['manifest_json'],{}).get('mapping',{})
        from .knowledge_router import balanced_plan
        from .knowledge_graph import relevance
        response=output.get('response','');response=response if isinstance(response,str) else db.dumps(response)
        candidates=[]
        for key in mapping:
            observation=lineage.owned(pid,'observation',key);meta=db.loads(observation['metadata_json'],{})
            lineage.version_of(pid,'observation',key,True)
            if observation['unit_id']:
                candidates.append({'file_id':observation['file_id'],'unit_id':observation['unit_id'],'observation_id':key,'intent':observation['intent'],
                    'char_start':meta['char_start'],'char_end':meta['char_end'],'reason':'官方社区/探索导航定位的原文，尚需读取核对','semantic_support':'not_checked',
                    '_rank':relevance(observation['text'],body['query']+' '+response[:5000])})
        candidates.sort(key=lambda r:-r.pop('_rank'))
        native['reading_plan']=balanced_plan(candidates+native['reading_plan'])
        native['knowledge_refs']+=native['official_refs']
        native['file_ids']=sorted(set(native['file_ids'])|{p['file_id'] for p in native['reading_plan'] if p.get('file_id')})
        native['argument_plan']['reading_ranges']=native['reading_plan']
        native['argument_plan']['source_coverage']=len({p.get('file_id') or p.get('evidence_id') for p in native['reading_plan']})
        native['official_navigation']={'text':response[:6000],'role':'derived_navigation_not_primary_evidence','artifact_id':artifact['id']}
    return native

"""Gold labels stay on the evaluator side, never in generation payloads."""
from pathlib import Path
from .. import db,security,provider
from ..config import ROOT
from .common import owned,blob,unblob,digest,fail
from . import profiles,provider_adapter

def dataset():return __import__('json').loads((ROOT/'prompts'/'writing-diagnostics.v3.json').read_text(encoding='utf-8'))

def run_dataset(run):
    policy=db.loads(run['policy_json'],{});data=unblob(policy['dataset_blob']) if policy.get('dataset_blob') else dataset()
    if digest(data)!=run['dataset_hash']:fail('DATASET_CHANGED','缺少该运行的原始题集版本，不能套用新的评估标准',409)
    return data

def create(pid,body,defer_enqueue=False):
    p=security.require_project(pid);profile=profiles.get(pid,body['profile_id']);data=dataset();selected=body.get('case_ids') or [c['id'] for c in data['cases']];repeat=body.get('repetitions',1);limit=body.get('budget_limit',p['budget'])
    if type(repeat) is not int or not 1<=repeat<=3 or not isinstance(selected,list) or set(selected)-{c['id'] for c in data['cases']}:fail('INVALID_DIAGNOSTIC','诊断范围或重复次数无效')
    if isinstance(limit,bool) or not isinstance(limit,(int,float)) or not 0<limit<=p['budget']:fail('INVALID_BUDGET','诊断预算必须在项目预算内')
    strategy=body.get('generation_strategy','baseline')
    if strategy not in ('baseline','judgment_then_draft','conversation','state_rebuild','mixed_annotation','frozen_annotation'):fail('INVALID_DIAGNOSTIC','诊断生成策略无效')
    identity=db.uid();policy={'case_ids':selected,'repetitions':repeat,'budget_limit':limit,'route_fingerprint':profile['route_fingerprint'],'gold_isolation':True,'generation_strategy':strategy,'comparison_id':body.get('comparison_id'),'dataset_blob':blob(data)}
    from .. import tasks
    with db.transaction():
        db.execute('INSERT INTO diagnostic_runs VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,digest(data),profile['id'],db.dumps(policy),'queued',None,db.now(),db.now()))
        for case in data['cases']:
            if case['id'] not in selected:continue
            for variant in case['variants']:
                for repetition in range(repeat):
                    item=db.uid();input_data={'instruction':case['prompt'],'context':variant['context']};task=None if defer_enqueue else tasks.enqueue(pid,'writing_diagnostic',{'item_id':item},'writing-diagnostic:'+item)
                    db.execute('INSERT INTO diagnostic_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',(item,identity,case['id'],variant['name'],repetition,digest(input_data),'[]',None,None,task['id'] if task else None,'queued'))
    return get(pid,identity)

def get(pid,identity):
    run=owned('diagnostic',identity,pid);items=db.rows('SELECT * FROM diagnostic_items WHERE run_id=? ORDER BY case_id,variant_id,repetition',(identity,))
    for item in items:
        item['result']=unblob(item['result_blob']) if item['result_blob'] else None;item['human_review']=db.loads(item['human_review_json'],None)
        from .diagnostic_scoring import reviews
        item['reviews']=reviews(pid,item['id'])
    from .diagnostic_scoring import report
    return {**run,'policy':db.loads(run['policy_json'],{}),'evaluation_dataset':run_dataset(run),'items':items,'dimensional_report':report(pid,identity),'summary':{'total':len(items),'completed':sum(i['status']=='completed' for i in items),'human_passed':sum((i['human_review'] or {}).get('decision')=='pass' for i in items),'human_failed':sum((i['human_review'] or {}).get('decision')=='fail' for i in items),'unreviewed':sum(not i['human_review'] for i in items)},'quality_claim':'未审阅结果不算通过'}

def process(task,body):
    item=db.one('SELECT i.* FROM diagnostic_items i JOIN diagnostic_runs r ON r.id=i.run_id WHERE i.id=? AND r.project_id=?',(body['item_id'],task['project_id']))
    if not item:fail('NOT_FOUND','诊断项不存在',404)
    if item['status']=='completed':return unblob(item['result_blob'])
    run=owned('diagnostic',item['run_id'],task['project_id']);policy=db.loads(run['policy_json'],{});data=run_dataset(run)
    if digest(data)!=run['dataset_hash']:fail('DATASET_CHANGED','诊断题集已变化，不能混入旧运行',409)
    case=next(c for c in data['cases'] if c['id']==item['case_id']);variant=next(v for v in case['variants'] if v['name']==item['variant_id']);payload={'instruction':case['prompt'],'context':variant['context']}
    # Do not serialize case/gold/failure_signal/other variants into this request.
    if digest(payload)!=item['input_hash']:fail('DATASET_CHANGED','诊断输入指纹不一致')
    profile=profiles.get(task['project_id'],run['profile_id']);_,cfg,_=profiles.for_session(task['project_id'],profile['id'])
    from ..summary_billing import request_reservation,BudgetExceeded
    used=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE json_extract(manifest,'$.diagnostic_run_id')=? AND status NOT IN ('rejected','failed_before_send')",(run['id'],))['n'];reservation,_=request_reservation(cfg,'请回答给定任务，仅返回JSON {"answer":"完整回答"}。',db.dumps(payload))
    try:
        system='请回答给定任务。仅根据给定材料，不虚构结果。只返回JSON {"answer":"完整回答"}，不输出私有思维链。'
        strategy=policy.get('generation_strategy','baseline')
        manifest={'diagnostic_run_id':run['id'],'diagnostic_item_id':item['id'],'dataset_hash':run['dataset_hash'],'case_id':case['id'],'gold_isolation':True,'diagnostic_comparison_id':policy.get('comparison_id')}
        def invoke(data,messages=None):
            return provider_adapter.call(task,profile,system,data,manifest,messages=messages)
        history=[];phase_results=[]
        if strategy in ('conversation','state_rebuild') or (case['id']=='D12' and variant['name']=='连续对话' and strategy=='baseline'):
            turns=['事实：用例A尚未执行。请写当前结论。','请将刚才的表达写得更正式，不能增加事实。','新事实：当前S2已执行A且通过，仍未测性能。请更新结论。','请根据当前已确认事实生成最终结论。']
            response=None
            for turn in turns:
                history.append({'role':'user','content':turn})
                if strategy=='state_rebuild':
                    canonical='用例A未执行，其他结果未知' if len(phase_results)<2 else '当前S2已执行A且通过，仍未测性能；旧状态中A未执行已被本条事实替代'
                    response=invoke({'instruction':turn,'context':variant['context'],'current_confirmed_state':canonical})
                else:response=invoke({'instruction':turn,'context':variant['context']},history)
                history.append({'role':'assistant','content':response['text']});phase_results.append({'answer':provider.parse_json(response['text']).get('answer','')})
        elif strategy=='judgment_then_draft':
            first=invoke({**payload,'stage':'judgment','stage_instruction':'先以简短命题列出材料能支持的条件、否定和结论边界，answer中不写正文，不虚构；不要输出私有思维链。'})
            checked=provider.parse_json(first['text']).get('answer','');phase_results.append({'stage':'judgment','answer':checked})
            response=invoke({**payload,'stage':'draft','frozen_judgment':checked,'stage_instruction':'将已核对命题落实为任务要求的回答，保留条件、范围及篇幅要求。'})
        elif strategy=='mixed_annotation':response=invoke({**payload,'stage_instruction':'在同一次answer中给出正文及对应的事实/缺口关联，不分后续请求。'})
        else:response=invoke(payload)
        parsed=provider.parse_json(response['text'])
        if not isinstance(parsed.get('answer'),str) or not parsed['answer'].strip():fail('DIAGNOSTIC_OUTPUT_INVALID','诊断回答为空或结构错误')
        result={'answer':parsed['answer'],'protocol_complete':True,'structured_output':True,'semantic_score':None,'human_review_required':True,'reported_model':response.get('reported_model'),'generation_strategy':strategy};status='completed'
        if strategy=='frozen_annotation' or (case['id']=='D10' and variant['name']=='先正文后映射' and strategy=='baseline'):
            second=invoke({'instruction':'对下列冻结正文关联事实和缺口，不改写正文。将对应关系放入answer字符串。','context':variant['context'],'frozen_text':parsed['answer']})
            result['annotation']=provider.parse_json(second['text']).get('answer','');result['frozen_text_hash']=digest(parsed['answer']);result['separate_annotation_stage']=True
        if phase_results:result['conversation_stages']=phase_results
    except Exception as error:
        status='budget_blocked' if isinstance(error,BudgetExceeded) else 'outcome_unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'failed';result={'error':security.redact(error),'semantic_score':None,'protocol_complete':False}
        db.execute('UPDATE diagnostic_items SET status=?,result_blob=? WHERE id=?',(status,blob(result),item['id']));db.execute('UPDATE diagnostic_runs SET status=?,updated_at=? WHERE id=?',(status,db.now(),run['id']));raise
    calls=db.rows("SELECT id FROM generation_runs WHERE json_extract(manifest,'$.diagnostic_item_id')=?",(item['id'],))
    db.execute('UPDATE diagnostic_items SET status=?,result_blob=?,generation_run_ids_json=? WHERE id=?',(status,blob(result),db.dumps([c['id'] for c in calls]),item['id']))
    remaining=db.one("SELECT COUNT(*) n FROM diagnostic_items WHERE run_id=? AND status!='completed'",(run['id'],))['n']
    db.execute('UPDATE diagnostic_runs SET status=?,updated_at=? WHERE id=?',('completed' if not remaining else 'running',db.now(),run['id']))
    return result


REAL_STANDARD={'task':'研究问题得到回应，章节职责和论证闭环清晰',
               'sources':'逐项对照冻结原文、代码和运行记录；无证据时保持unknown',
               'synthesis':'综述比较方法、条件和局限；设计解释取舍',
               'language':'信息密度、重复、术语及限定准确',
               'human_review':'模型评价不等于作者事实确认或人工盲评'}


def create_real_run(pid, profile_id, budget_limit=None):
    p=security.require_project(pid);profile=profiles.get(pid,profile_id)
    limit=p['budget'] if budget_limit is None else budget_limit
    if not isinstance(limit,(int,float)) or isinstance(limit,bool) or not 0<limit<=p['budget']:
        fail('INVALID_BUDGET','真实论文评测预算必须在项目预算内')
    data={'version':'real-paper.v1','cases':[{'id':'PAPER','prompt':'对照冻结任务和原文评价整篇候选。',
          'variants':[{'name':'candidate','context':'每个样本绑定自己的冻结上下文。'}], 'gold':REAL_STANDARD}]}
    identity=db.uid();policy={'dataset_blob':blob(data),'budget_limit':limit,'real_paper':True,
                            'route_fingerprint':profile['route_fingerprint']}
    db.execute('INSERT INTO diagnostic_runs VALUES(?,?,?,?,?,?,?,?,?)',
               (identity,pid,digest(data),profile_id,db.dumps(policy),'completed',None,db.now(),db.now()))
    return get(pid,identity)


def register_real_whole_paper(pid, run_id, case_id, variant_id, repetition, answer, profile_id, source=None):
    """Register a real whole-paper candidate as a diagnostic sample.

    This connects the actual writing chain to the diagnostic scoring harness:
    a generated (or revised) whole-paper candidate is stored as a completed
    diagnostic item so the same blinded review, disagreement adjudication and
    frozen-policy qualification used for synthetic cases can be applied to it.
    No model call is made here; the caller supplies the already-generated text.
    """
    security.require_project(pid)
    run = db.one('SELECT * FROM diagnostic_runs WHERE id=? AND project_id=?', (run_id, pid))
    if not run: fail('NOT_FOUND', '诊断运行不存在', 404)
    data = run_dataset(run)
    if digest(data) != run['dataset_hash']: fail('DATASET_CHANGED', '诊断题集已变化', 409)
    if case_id not in {c['id'] for c in data['cases']}: fail('INVALID_DIAGNOSTIC', '诊断用例不存在')
    if not isinstance(variant_id,str) or not 1<=len(variant_id)<=100:fail('INVALID_DIAGNOSTIC','真实样本变体标识无效')
    if db.loads(run['policy_json'],{}).get('real_paper'):
        case=next(c for c in data['cases'] if c['id']==case_id)
        if variant_id not in {v['name'] for v in case['variants']}:fail('INVALID_DIAGNOSTIC','真实评测变体不属于冻结题集')
    profile = profiles.get(pid, profile_id)
    if run['profile_id']!=profile_id:fail('PROFILE_MISMATCH','样本模型配置与冻结评测运行不一致',409)
    if type(repetition) is not int or not 0<=repetition<=100:fail('INVALID_DIAGNOSTIC','重复样本编号无效')
    if not isinstance(answer,str) or not answer.strip():fail('INVALID_DIAGNOSTIC','论文正文不能为空')
    if source is not None and not isinstance(source,dict):fail('INVALID_DIAGNOSTIC','样本来源须为结构化快照')
    source=dict(source or {})
    context=source.get('context') or {}
    input_data={'instruction':source.get('instruction') or '评价给定论文；缺失原文时不能确认事实支持。',
                'context':context,'real_whole_paper':True,'paper_hash':source.get('paper_hash') or digest(answer),
                'source_task_id':source.get('task_id'),'profile_fingerprint':profile['route_fingerprint'],
                'case_id':case_id,'variant_id':variant_id,'answer_hash':digest(answer)}
    if source.get('task_id'):
        task=db.one('SELECT result FROM tasks WHERE id=? AND project_id=?',(source['task_id'],pid))
        saved=db.loads(task['result'],{}) if task else {}
        if not task or saved.get('paper_hash')!=source.get('paper_hash') or saved.get('text')!=answer:
            fail('CANDIDATE_CHANGED','真实样本必须对应已保存的候选及哈希',409)
    previous=db.one('SELECT * FROM diagnostic_items WHERE run_id=? AND case_id=? AND variant_id=? AND repetition=?',
                    (run_id,case_id,variant_id,repetition))
    if previous:
        if previous['input_hash']!=digest(input_data):fail('SAMPLE_CONFLICT','同一重复编号已绑定另一份样本',409)
        return {'item_id':previous['id'],'run_id':run_id,'idempotent_replay':True}
    item = db.uid()
    result = {'answer': answer, 'protocol_complete': True, 'structured_output': True,
              'semantic_score': None, 'human_review_required': True, 'generation_strategy': 'real_whole_paper',
              'real_whole_paper': True, 'reported_model': profile.get('actual_config', {}).get('model'),
              'case_id': case_id, 'variant_id': variant_id,'evaluation_input':input_data,
              'evaluation_standard':REAL_STANDARD,'evidence_context_complete':bool(context)}
    with db.transaction():
        db.execute('INSERT INTO diagnostic_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (item, run_id, case_id, variant_id, repetition, digest(input_data), '[]',
                    blob(result), None, None, 'completed'))
        remaining=db.one("SELECT COUNT(*) n FROM diagnostic_items WHERE run_id=? AND status!='completed'",(run_id,))['n']
        db.execute('UPDATE diagnostic_runs SET status=?,updated_at=? WHERE id=?',('running' if remaining else 'completed',db.now(),run_id))
    return {'item_id': item, 'run_id': run_id, 'case_id': case_id, 'variant_id': variant_id, 'answer_characters': len(answer)}


def score_real_whole_paper(pid, run_id, item_id, profile_id):
    """Score a registered real whole-paper candidate with the model-assisted side.

    Reuses the diagnostic scoring review so the same dimension rubric applies.
    """
    from .. import tasks
    security.require_project(pid)
    run = db.one('SELECT * FROM diagnostic_runs WHERE id=? AND project_id=?', (run_id, pid))
    if not run: fail('NOT_FOUND', '诊断运行不存在', 404)
    item = db.one('SELECT * FROM diagnostic_items WHERE id=? AND run_id=?', (item_id, run_id))
    if not item or item['status'] != 'completed': fail('NOT_FOUND', '需已完成真实样本', 404)
    profile = profiles.get(pid, profile_id)
    result=unblob(item['result_blob'])
    if not result.get('real_whole_paper'):fail('INVALID_DIAGNOSTIC','请选择真实论文样本')
    if not result.get('evidence_context_complete'):fail('EVIDENCE_CONTEXT_REQUIRED','缺少冻结任务与原文，请重新登记完整样本')
    return tasks.enqueue(pid,'writing_real_diagnostic_review',{'run_id':run_id,'item_id':item_id,'profile_id':profile_id},
                         'real-paper-score:'+digest([item_id,profile['route_fingerprint'],item['input_hash']]))


def process_real_score(task, body):
    from . import diagnostic_scoring
    pid=task['project_id'];run=owned('diagnostic',body['run_id'],pid)
    item=db.one('SELECT * FROM diagnostic_items WHERE id=? AND run_id=?',(body['item_id'],run['id']))
    if not item or item['status']!='completed':fail('NOT_FOUND','真实样本不存在',404)
    result=unblob(item['result_blob']);frozen=result.get('evaluation_input') or {}
    if not result.get('real_whole_paper') or not result.get('evidence_context_complete') or digest(frozen)!=item['input_hash']:
        fail('EVIDENCE_CONTEXT_REQUIRED','真实样本缺少完整冻结上下文或指纹不符',409)
    if frozen.get('source_task_id'):
        source=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(frozen['source_task_id'],pid))
        from ..paper_writing import validate_request
        if not source:fail('CANDIDATE_CHANGED','来源写作任务不存在',409)
        validate_request(source)
        if db.loads(source['result'],{}).get('paper_hash')!=frozen['paper_hash']:fail('CANDIDATE_CHANGED','来源候选已变化',409)
    profile=profiles.get(pid,body['profile_id'])
    payload={**frozen,'answer':result['answer'],'evaluation_standard':result['evaluation_standard'],
             'dimensions':diagnostic_scoring.DIMENSIONS,'real_whole_paper':True}
    system = ('你是独立评估侧核验者。对照给定材料与标准逐项核对真实整篇候选，'
              '不以流畅度代替事实。返回JSON {"decision":"pass|fail|unknown","note":"具体总判断",'
              '"dimensions":{"每个给定维度":{"score":0或1或2或null,"applicable":true或false,"rationale":"具体依据"}}}。'
              '0为错误，1为部分满足，2为在给定范围内满足；不适用才给null并解释。未知不能判通过。')
    from . import provider_adapter as adapter
    manifest = {'diagnostic_run_id': run['id'], 'diagnostic_item_id': item['id'], 'evaluation_side': True,
                'gold_isolation': 'evaluation_only', 'stage': 'diagnostic_model_review',
                'real_whole_paper': True}
    parsed = provider.parse_json(adapter.call(task,profile,system,payload,manifest)['text'])
    return diagnostic_scoring.review(pid, run['id'], {**parsed, 'item_id': item['id'],
                                       'evaluator_id': 'model:' + profile['route_fingerprint']},
                         method='model_assisted')

def review(pid,rid,body):
    from .diagnostic_scoring import review as record
    record(pid,rid,body);return get(pid,rid)

def qualify(pid,rid,note,policy_id=None):
    from .diagnostic_scoring import qualify as calibrated
    return calibrated(pid,rid,note,policy_id)

@db.atomic
def resume(pid,rid,body):
    run=owned('diagnostic',rid,pid);ack=set(body.get('acknowledged_unknown_items',[]));from .. import tasks
    for item in db.rows('SELECT * FROM diagnostic_items WHERE run_id=?',(rid,)):
        if item['status'] in ('failed','budget_blocked'):
            db.execute("UPDATE diagnostic_items SET status='queued' WHERE id=?",(item['id'],));db.execute("UPDATE tasks SET status='queued',error=NULL,cancel_requested=0 WHERE id=?",(item['task_id'],))
        elif item['status']=='outcome_unknown' and item['id'] in ack:
            task=tasks.enqueue(pid,'writing_diagnostic',{'item_id':item['id']});db.execute("UPDATE diagnostic_items SET status='queued',task_id=? WHERE id=?",(task['id'],item['id']))
    db.execute("UPDATE diagnostic_runs SET status='queued',updated_at=? WHERE id=?",(db.now(),rid));return get(pid,rid)

"""Controlled factors share original inputs, profile and budget; scores remain separate."""
from .. import db,security,provider
from .common import owned,blob,unblob,digest,fail
from . import evaluations,profiles,provider_adapter,diagnostic_scoring

FACTORS={
    'workflow':('baseline','judgment_then_draft'),
    'revision_state':('conversation','state_rebuild'),
    'annotation':('mixed_annotation','frozen_annotation'),
}

def owned_comparison(pid,identity):
    security.require_project(pid);r=db.one('SELECT * FROM diagnostic_comparisons WHERE id=? AND project_id=?',(identity,pid))
    if not r:fail('NOT_FOUND','对照实验不属于当前项目',404)
    return r

@db.atomic
def create(pid,body):
    factor=body.get('factor');profile=profiles.get(pid,body['profile_id']);p=security.require_project(pid)
    if factor not in FACTORS:fail('INVALID_COMPARISON','请选择流程、状态重建或标注方式对照')
    case_ids=body.get('case_ids') or (['D12'] if factor=='revision_state' else ['D10'] if factor=='annotation' else ['D02','D03','D06','D08','D11','D16'])
    if factor=='revision_state' and case_ids!=['D12'] or factor=='annotation' and case_ids!=['D10']:fail('INVALID_COMPARISON','状态或标注对照需使用对应可比场景')
    budget=body.get('budget_limit',p['budget'])
    if isinstance(budget,bool) or not isinstance(budget,(int,float)) or not 0<budget<=p['budget']:fail('INVALID_BUDGET','对照总预算必须在项目预算内')
    identity=db.uid();policy={'profile_id':profile['id'],'route_fingerprint':profile['route_fingerprint'],'dataset_hash':digest(evaluations.dataset()),'case_ids':case_ids,'repetitions':body.get('repetitions',3),'budget_limit':budget,'arms':list(FACTORS[factor]),'changed_factor':factor,'controlled':['original_instruction','original_variant_context','profile','sampling_repetitions','budget_accounting'],'execution_order':'paired_interleaved_with_counterbalanced_arm_order','not_controlled':['number_of_model_calls_required_by_the_factor'],'human_semantic_acceptance':'pending'}
    db.execute('INSERT INTO diagnostic_comparisons VALUES(?,?,?,?,?,?)',(identity,pid,factor,db.dumps(policy),'queued',db.now()))
    for strategy in FACTORS[factor]:
        run=evaluations.create(pid,{'profile_id':profile['id'],'case_ids':case_ids,'repetitions':policy['repetitions'],'budget_limit':budget,'generation_strategy':strategy,'comparison_id':identity},defer_enqueue=True)
        db.execute('INSERT INTO diagnostic_comparison_members VALUES(?,?,?)',(identity,strategy,run['id']))
    pairs={}
    for row in db.rows('SELECT i.*,m.arm FROM diagnostic_items i JOIN diagnostic_comparison_members m ON m.run_id=i.run_id WHERE m.comparison_id=?',(identity,)):
        pairs.setdefault((row['case_id'],row['variant_id'],row['repetition']),[]).append(row)
    from .. import tasks
    for index,key in enumerate(sorted(pairs)):
        pair=sorted(pairs[key],key=lambda r:r['arm'],reverse=bool(index%2))
        for item in pair:
            task=tasks.enqueue(pid,'writing_diagnostic',{'item_id':item['id']},'writing-diagnostic:'+item['id']);db.execute('UPDATE diagnostic_items SET task_id=? WHERE id=?',(task['id'],item['id']))
    return report(pid,identity)

def report(pid,identity):
    r=owned_comparison(pid,identity);arms={};paired={}
    for member in db.rows('SELECT * FROM diagnostic_comparison_members WHERE comparison_id=? ORDER BY arm',(identity,)):
        run=evaluations.get(pid,member['run_id']);arm=member['arm']
        billing=db.one("SELECT COUNT(*) requests,COALESCE(SUM(budget_charge),0) charged_or_reserved,SUM(cost IS NULL) unknown_prices FROM generation_budget_usage WHERE json_extract(manifest,'$.diagnostic_run_id')=? AND status NOT IN ('rejected','failed_before_send')",(run['id'],))
        from .budget import scope_charge
        billing['charged_or_reserved']=scope_charge('diagnostic_run_id',run['id']);billing['method']='ungrouped_costs_plus_full_touched_group_upper_bound'
        arms[arm]={'run_id':run['id'],'summary':run['summary'],'dimensions':run['dimensional_report'],'billing':billing}
        for item in run['items']:
            key=(item['case_id'],item['variant_id'],item['repetition']);paired.setdefault(key,{})[arm]={'item_id':item['id'],'input_hash':item['input_hash'],'status':item['status'],'answer_characters':len((item['result'] or {}).get('answer','')),'human_review':item['human_review']}
    pairs=[{'case_id':key[0],'variant_id':key[1],'repetition':key[2],'arms':value,'original_inputs_match':len({v['input_hash'] for v in value.values()})==1} for key,value in paired.items()]
    results=[{**x,'result':unblob(x['result_blob'])} for x in db.rows('SELECT * FROM diagnostic_comparison_results WHERE comparison_id=? ORDER BY rowid',(identity,))]
    return {**r,'policy':db.loads(r['policy_json'],{}),'arms':arms,'paired_samples':pairs,'position_swap_checks':results,'all_generated':bool(arms and all(x['summary']['completed']==x['summary']['total'] for x in arms.values())),'human_acceptance_complete':bool(arms and all(x['summary']['human_passed']==x['summary']['total'] for x in arms.values())),'semantic_win_rate':None}

def score_model(task,body):
    pid=task['project_id'];run=owned('diagnostic',body['run_id'],pid);item=db.one('SELECT * FROM diagnostic_items WHERE id=? AND run_id=?',(body['item_id'],run['id']))
    if not item or item['status']!='completed':fail('NOT_FOUND','需已完成的真实样本',404)
    if unblob(item['result_blob']).get('real_whole_paper'):
        return evaluations.process_real_score(task,body)
    case=next(c for c in evaluations.run_dataset(run)['cases'] if c['id']==item['case_id']);variant=next(v for v in case['variants'] if v['name']==item['variant_id']);profile=profiles.get(pid,body['profile_id'])
    payload={'instruction':case['prompt'],'context':variant['context'],'answer':unblob(item['result_blob']).get('answer',''),'evaluation_standard':case.get('gold'),'failure_signals':case.get('failure_signals',[]),'dimensions':diagnostic_scoring.DIMENSIONS}
    system='你是独立评估侧核验者。对照给定材料与标准逐项核对，不以流畅度代替事实。返回JSON {"decision":"pass|fail|unknown","note":"具体总判断","dimensions":{"每个给定维度":{"score":0或1或2或null,"applicable":true或false,"rationale":"具体依据"}}}。0为错误，1为部分满足，2为在给定范围内满足；不适用才给null并解释。未知不能判通过。本记录只属于模型辅助，不冒充人工。'
    manifest={'diagnostic_run_id':run['id'],'diagnostic_item_id':item['id'],'evaluation_side':True,'gold_isolation':'evaluation_only','stage':'diagnostic_model_review','diagnostic_comparison_id':db.loads(run['policy_json'],{}).get('comparison_id')}
    parsed=provider.parse_json(provider_adapter.call(task,profile,system,payload,manifest)['text'])
    result=diagnostic_scoring.review(pid,run['id'],{**parsed,'item_id':item['id'],'evaluator_id':'model:'+profile['route_fingerprint']},method='model_assisted')
    return result

def position_swap(task,body):
    pid=task['project_id'];r=owned_comparison(pid,body['comparison_id']);profile=profiles.get(pid,body['profile_id']);samples=[]
    for identity in body['item_ids']:
        item=db.one('SELECT i.* FROM diagnostic_items i JOIN diagnostic_comparison_members m ON m.run_id=i.run_id WHERE i.id=? AND m.comparison_id=?',(identity,r['id']))
        if not item or item['status']!='completed':fail('INVALID_COMPARISON','需该对照中两个已完成、原输入相同的样本')
        samples.append(item)
    if len(samples)!=2 or samples[0]['input_hash']!=samples[1]['input_hash'] or samples[0]['run_id']==samples[1]['run_id']:fail('INVALID_COMPARISON','位置交换需要不同实验组的同一输入')
    source_run=owned('diagnostic',samples[0]['run_id'],pid);case=next(c for c in evaluations.run_dataset(source_run)['cases'] if c['id']==samples[0]['case_id']);variant=next(v for v in case['variants'] if v['name']==samples[0]['variant_id']);outputs=[]
    system='评估两份回答对给定事实、条件和任务的遵守情况，忽略位置和文风偏好。只返回JSON {"preferred":"A|B|tie|unknown","reason":"具体依据"}。无法判断时为unknown。'
    for order in (samples,list(reversed(samples))):
        payload={'instruction':case['prompt'],'context':variant['context'],'evaluation_standard':case.get('gold'),'A':unblob(order[0]['result_blob'])['answer'],'B':unblob(order[1]['result_blob'])['answer']}
        parsed=provider.parse_json(provider_adapter.call(task,profile,system,payload,{'diagnostic_comparison_id':r['id'],'evaluation_side':True,'position_order':[x['id'] for x in order]})['text'])
        if parsed.get('preferred') not in ('A','B','tie','unknown'):fail('INVALID_EVALUATION','位置交换评估格式无效')
        preferred=parsed['preferred'];outputs.append({**parsed,'order':[x['id'] for x in order],'preferred_item_id':order[0 if preferred=='A' else 1]['id'] if preferred in ('A','B') else preferred})
    result={'items':[s['id'] for s in samples],'orders':outputs,'position_consistent':outputs[0]['preferred_item_id']==outputs[1]['preferred_item_id'],'method':'model_assisted','human_acceptance':False}
    db.execute('INSERT INTO diagnostic_comparison_results VALUES(?,?,?,?,?,?)',(db.uid(),pid,r['id'],blob(result),'model_assisted',db.now()));return result

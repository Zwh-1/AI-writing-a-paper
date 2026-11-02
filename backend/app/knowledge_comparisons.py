"""A/B/C/D ablations with separately recorded human gold and assessments."""
import time,math
import random
from fastapi import HTTPException
from . import db, security, knowledge_lineage as lineage, knowledge_router as router

CATEGORIES=[('locating',18,'定位原文并保留数字、条件和否定'),('relations',14,'说明方法、对象与关系'),('synthesis',14,'比较多篇文献的研究路线与适用范围'),('limitations',10,'核对结论分歧与不能推出的结论'),('guidance',8,'区分指导要求、正例与反例'),('project',8,'区分代码定义与实际执行结果'),('changes',8,'核对版本、用途与权限变化后的可用性')]
MIN_ACCEPTANCE_CASES=80


def reviewer_key(value):return ' '.join(str(value or '').casefold().split())


def gold_current(pid,gold):
    if not (gold.get('annotation_origin')=='human_declared' and gold.get('compared_original') is True
            and gold.get('real_task_confirmed') is True and reviewer_key(gold.get('reviewer')) and gold.get('source_refs')):return False
    try:lineage.validate_refs(pid,gold['source_refs'])
    except HTTPException:return False
    return True

def seed(pid):
    security.require_project(pid)
    old=db.rows('SELECT * FROM knowledge_evaluation_cases WHERE project_id=?',(pid,))
    if old:return {'cases':old,'created':0}
    names=[r['name'] for r in db.rows('SELECT name FROM files WHERE project_id=? AND active=1 ORDER BY role,name',(pid,))] or ['本项目资料']
    i=0
    for category,count,question in CATEGORIES:
        for n in range(count):
            identity=db.uid();text=f'{question}：围绕《{names[i%len(names)]}》提出第{n+1}个具体核查问题。'
            db.execute('INSERT INTO knowledge_evaluation_cases VALUES(?,?,?,?,?,?,?)',(identity,pid,category,text,db.dumps({'note':'自动生成的任务草稿，需改写成真实问题并人工标注；不作为已验证金标准'}),'unlabelled',db.now()));i+=1
    return {'cases':db.rows('SELECT * FROM knowledge_evaluation_cases WHERE project_id=?',(pid,)),'created':i,'gold_verified':False}

def update_case(pid,identity,body):
    security.require_project(pid);old=db.one('SELECT * FROM knowledge_evaluation_cases WHERE id=? AND project_id=?',(identity,pid))
    if not old:lineage.fail('CASE_NOT_FOUND','评测任务不存在',404)
    question=body.get('question',old['question']);gold=body.get('gold',db.loads(old['gold_json'],{}));state=body.get('state',old['state'])
    if not isinstance(question,str) or not question.strip() or len(question)>5000 or state not in ('unlabelled','labelled') or not isinstance(gold,dict):lineage.fail('INVALID_CASE','问题或标注格式无效',422)
    if state=='labelled' and (not isinstance(body.get('reviewer'),str) or not reviewer_key(body.get('reviewer')) or not (gold.get('unit_ids') or gold.get('evidence_ids'))
            or body.get('compared_original') is not True or body.get('real_task_confirmed') is not True):
        lineage.fail('HUMAN_GOLD_REQUIRED','人工标准需要实际核对人、真实任务确认、原件核对确认及可接受原文或项目证据',422)
    for uid in gold.get('unit_ids',[]):lineage.version_of(pid,'unit',uid)
    for eid in gold.get('evidence_ids',[]):lineage.version_of(pid,'evidence',eid)
    gold={**gold,'reviewer':body.get('reviewer',gold.get('reviewer'))}
    if state=='labelled':gold.update(annotation_origin='human_declared',compared_original=True,real_task_confirmed=True,
        source_refs=[lineage.ref(pid,'unit',uid) for uid in gold.get('unit_ids',[])]+[lineage.ref(pid,'evidence',eid) for eid in gold.get('evidence_ids',[])],labelled_at=db.now())
    db.execute('UPDATE knowledge_evaluation_cases SET question=?,gold_json=?,state=? WHERE id=?',(question,db.dumps(gold),state,identity))
    return db.one('SELECT * FROM knowledge_evaluation_cases WHERE id=?',(identity,))

def run(task,body):
    pid=task['project_id'];cases=db.rows('SELECT * FROM knowledge_evaluation_cases WHERE project_id=? ORDER BY category,id',(pid,))
    chosen=set(body.get('case_ids',[]))
    if chosen:cases=[c for c in cases if c['id'] in chosen]
    elif not body.get('allow_unlabelled'):cases=[c for c in cases if c['state']=='labelled']
    if not cases:lineage.fail('NO_EVALUATION_CASES','请先建立评测问题',422)
    variants_to_run=['A','B','C','D']+(['D_official'] if body.get('include_official_subcomparison') else [])
    results=[];allrefs=[];identity=db.uid();order=random.SystemRandom().sample(variants_to_run,len(variants_to_run))
    snapshot=input_snapshot(pid)
    manifest={'task_id':task['id'],'schema':'knowledge.ablation.v1','refs':[],'generation':'model_candidates' if body.get('generate_answers') else 'retrieval_only',
              'blind_order':dict(zip(['组'+str(i+1) for i in range(len(order))],order)),'input_snapshot':snapshot,
              'cases_snapshot':[{k:c[k] for k in ('id','question','category','state','gold_json')} for c in cases],
              'note':'召回与模型评估不替代人工语义质量，未代填盲审'}
    db.execute('INSERT INTO knowledge_comparisons VALUES(?,?,?,?,?,?,?)',(identity,pid,task['id'],db.dumps(manifest),'[]','running',db.now()))
    for case in cases:
        variants={};case_result={'case_id':case['id'],'question':case['question'],'category':case['category'],'gold_state':case['state'],'variants':variants};results.append(case_result)
        for variant in variants_to_run:
            if input_snapshot(pid)!=snapshot:
                db.execute("UPDATE knowledge_comparisons SET state='stale' WHERE id=?",(identity,));lineage.fail('EVALUATION_INPUT_CHANGED','对照期间原文、权限或模型配置发生变化，请重新固定输入')
            started=time.perf_counter();request={'query':case['question'],'variant':'D' if variant=='D_official' else variant,'namespace':'guidance' if case['category']=='guidance' else 'project' if case['category']=='project' else 'literature','include_originals':body.get('include_originals',True)}
            if variant=='D_official':
                from .knowledge_planning import execute
                result=execute(task,{**body,**request,'backend':body.get('official_mode','global'),'allow_model':True})
            else:result=router.query(pid,request)
            ids=list(dict.fromkeys([i['unit_id'] for i in result['reading_plan'] if i.get('unit_id')]+[h['knowledge_unit_id'] for h in result['originals'] if h.get('knowledge_unit_id')]))
            evidence_ids=list(dict.fromkeys(p['evidence_id'] for p in result['reading_plan'] if p.get('evidence_id')))
            gold_record=db.loads(case['gold_json'],{});gold={('unit',i) for i in gold_record.get('unit_ids',[])}|{('evidence',i) for i in gold_record.get('evidence_ids',[])};ranked=[('unit',i) for i in ids]+[('evidence',i) for i in evidence_ids]
            recall=len(gold&set(ranked))/len(gold) if gold and case['state']=='labelled' else None
            mrr=next((1/(i+1) for i,key in enumerate(ranked) if key in gold),0) if recall is not None else None
            generated=router.answer(task,{'query':case['question'],'variant':variant},context=result) if body.get('generate_answers') else None
            variants[variant]={'unit_ids':ids,'evidence_ids':evidence_ids,'recall':recall,'mrr':mrr,'elapsed_seconds':round(time.perf_counter()-started,4),'knowledge_refs':result['knowledge_refs'],'answer':generated,'official_result':result.get('official_result'),'human_semantic_review':None}
            allrefs.extend(result['knowledge_refs'])
            db.execute('UPDATE knowledge_comparisons SET result_json=? WHERE id=?',(db.dumps(results),identity))
    manifest['refs']=list({(r['type'],r['id'],r['version']):r for r in allrefs}.values())
    db.execute("UPDATE knowledge_comparisons SET manifest_json=?,result_json=?,state='awaiting_human_review' WHERE id=?",(db.dumps(manifest),db.dumps(results),identity))
    return {'id':identity,'cases':len(results),'labelled':sum(c['state']=='labelled' for c in cases),'results':results,'semantic_quality_verified':False}

@db.atomic
def review(pid,identity,body):
    row=lineage.owned(pid,'comparison',identity);results=db.loads(row['result_json'],[]);reviewer=body.get('reviewer');note=body.get('note');variant=body.get('variant');manifest=db.loads(row['manifest_json'],{})
    if body.get('blind_group'):variant=db.loads(row['manifest_json'],{}).get('blind_order',{}).get(body['blind_group'])
    if not isinstance(reviewer,str) or not reviewer.strip() or not isinstance(note,str) or not note.strip() or variant not in ('A','B','C','D','D_official'):lineage.fail('INVALID_REVIEW','盲审需要核对人、理由与目标组',422)
    target=next((c for c in results if c['case_id']==body.get('case_id')),None)
    if not target:lineage.fail('CASE_NOT_FOUND','对照中没有该问题',404)
    source_case=next((c for c in manifest.get('cases_snapshot',[]) if c['id']==body['case_id']),{})
    gold=db.loads(source_case.get('gold_json'),{})
    independent=False
    if body.get('blind_group'):
        if db.one("SELECT id FROM audit WHERE project_id=? AND action='knowledge_blind_mapping_revealed' AND json_extract(detail,'$.comparison_id')=? LIMIT 1",(pid,identity)):
            lineage.fail('BLIND_ALREADY_REVEALED','组合映射已经揭示，不能再登记为独立盲审；可保留为公开复核',422)
        if not gold_current(pid,gold) or body.get('compared_original') is not True:
            lineage.fail('BLIND_ORIGINAL_REVIEW_REQUIRED','独立盲审需要已固定的真实人工标准，并确认实际对照原件',422)
        if reviewer_key(reviewer)==reviewer_key(gold.get('reviewer')):
            lineage.fail('INDEPENDENT_REVIEWER_REQUIRED','标注人与独立盲审人必须不同',422)
        independent=True
    fields={}
    for key in ('supported_claims','checkable_claims','qualifiers_preserved','qualifiers_required','wrong_citations','unsupported_expansions'):
        value=body.get(key)
        if type(value) is not int or value<0:lineage.fail('INVALID_REVIEW','语义评估数量必须为非负整数',422)
        fields[key]=value
    if fields['supported_claims']>fields['checkable_claims'] or fields['qualifiers_preserved']>fields['qualifiers_required']:lineage.fail('INVALID_REVIEW','通过数量不能大于总数',422)
    for key in ('comparisons_valid','comparisons_required','contradictions_identified','contradictions_required','user_revision_count'):
        value=body.get(key,0)
        if type(value) is not int or value<0:lineage.fail('INVALID_REVIEW','评测数量必须为非负整数',422)
        fields[key]=value
    if fields['comparisons_valid']>fields['comparisons_required'] or fields['contradictions_identified']>fields['contradictions_required']:lineage.fail('INVALID_REVIEW','通过数量不能超过评测总量',422)
    duration=body.get('completion_seconds',0)
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<0:lineage.fail('INVALID_REVIEW','完成时间必须为非负有限数值',422)
    fields['completion_seconds']=duration
    if variant not in target['variants']:lineage.fail('VARIANT_NOT_READY','该组尚未完成，不能登记评审',422)
    target['variants'][variant]['human_semantic_review']={**fields,'reviewer':reviewer,'note':note,'created_at':db.now(),
        'review_mode':'independent_blind' if independent else 'open_review','independent_blind':independent,
        'compared_original':body.get('compared_original') is True,'gold_fingerprint':lineage.digest(gold) if independent else None}
    db.execute('INSERT INTO knowledge_review_events VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,identity,body['case_id'],variant,db.dumps(target['variants'][variant]['human_semantic_review']),reviewer,db.now()))
    db.execute('UPDATE knowledge_comparisons SET result_json=? WHERE id=?',(db.dumps(results),identity))
    if body.get('blind_group'):return {**blind(pid,identity),'method':'human_declared','review_saved':True}
    if manifest.get('blind_order'):db.audit(pid,'knowledge_blind_mapping_revealed',{'comparison_id':identity,'reason':'open_review_returns_unblinded_variants'})
    return {'id':identity,'results':results,'method':'human_declared'}

def blind(pid,identity,reveal=False):
    row=lineage.owned(pid,'comparison',identity);manifest=db.loads(row['manifest_json'],{});results=db.loads(row['result_json'],[])
    if reveal:
        db.audit(pid,'knowledge_blind_mapping_revealed',{'comparison_id':identity})
        return {'id':identity,'mapping':manifest.get('blind_order',{}),'results':results,'revealed':True}
    order=manifest.get('blind_order',dict(zip(['组1','组2','组3','组4'],['A','B','C','D'])))
    cases=[]
    for result in results:
        groups=[]
        for label,variant in order.items():
            output=result['variants'].get(variant)
            if output:
                answer=output.get('answer');data=answer.get('answer',{}) if isinstance(answer,dict) else answer
                if isinstance(data,dict):data={k:data[k] for k in ('blocks','gaps') if k in data}
                groups.append({'label':label,'unit_ids':output['unit_ids'],'answer':data,'human_semantic_review':output.get('human_semantic_review')})
        cases.append({'case_id':result['case_id'],'question':result['question'],'groups':groups})
    revealed=bool(db.one("SELECT id FROM audit WHERE project_id=? AND action='knowledge_blind_mapping_revealed' AND json_extract(detail,'$.comparison_id')=? LIMIT 1",(pid,identity)))
    return {'id':identity,'cases':cases,'generation':manifest.get('generation'),'blinded':True,'revealed':revealed}

def input_snapshot(pid):
    from . import provider
    with db.read_session():
        values=[db.rows('SELECT id,sha256,role,egress,active FROM files WHERE project_id=? ORDER BY id',(pid,)),
                db.rows('SELECT h.file_id,h.parse_id,p.input_hash FROM knowledge_heads h JOIN knowledge_parses p ON p.id=h.parse_id WHERE p.project_id=? ORDER BY h.file_id',(pid,)),
                db.rows('SELECT s.id,s.text,s.locator FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? ORDER BY s.id',(pid,)),
                {k:provider.config().get(k) for k in ('base_url','model','protocol','max_output_tokens','temperature','top_p','thinking_enabled','reasoning_effort')},
                db.setting('retrieval:'+pid,{}),db.setting('reference_knowledge:'+pid,{}),
                db.rows('SELECT id,question,gold_json,state FROM knowledge_evaluation_cases WHERE project_id=? ORDER BY id',(pid,))]
    return lineage.digest(values)

def wilson(success,total):
    if not total:return None
    z=1.959963984540054;p=success/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return {'rate':p,'n':total,'success':success,'confidence_level':.95,'lower':max(0,center-half),'upper':min(1,center+half),'method':'Wilson interval; claim counts may be correlated within tasks'}

@db.read_session()
@lineage.validation_scope()
def report(pid,identity):
    row=lineage.owned(pid,'comparison',identity);results=db.loads(row['result_json'],[]);manifest=db.loads(row['manifest_json'],{});groups={}
    current_input=manifest.get('input_snapshot')==input_snapshot(pid)
    gold_by_case={c['id']:db.loads(c.get('gold_json'),{}) for c in manifest.get('cases_snapshot',[])}
    cases_current={}
    for case in results:
        saved=gold_by_case.get(case['case_id'],{});current=db.one('SELECT * FROM knowledge_evaluation_cases WHERE id=? AND project_id=?',(case['case_id'],pid))
        cases_current[case['case_id']]=bool(current and current['state']=='labelled' and current['question']==case.get('question')
            and db.loads(current['gold_json'],{})==saved and gold_current(pid,saved))
    fields=('supported_claims','checkable_claims','qualifiers_preserved','qualifiers_required','wrong_citations','unsupported_expansions','comparisons_valid','comparisons_required','contradictions_identified','contradictions_required','user_revision_count','completion_seconds')
    for case in results:
        for variant,output in case['variants'].items():
            group=groups.setdefault(variant,{'cases':0,'labelled_cases':0,'reviewed_cases':0,'independent_blind_cases':0,
                'current_human_gold_cases':0,'generated_answer_cases':0,'measurable_review_cases':0,'passing_tasks':0,
                'categories':{},'recalls':[],'mrrs':[],'elapsed_seconds':0,'totals':{f:0 for f in fields}})
            group['cases']+=1;group['labelled_cases']+=case['gold_state']=='labelled';group['elapsed_seconds']+=output['elapsed_seconds']
            category=case.get('category','unspecified');group['categories'][category]=group['categories'].get(category,0)+1
            if output.get('recall') is not None:group['recalls'].append(output['recall']);group['mrrs'].append(output['mrr'])
            review=output.get('human_semantic_review')
            gold=gold_by_case.get(case['case_id'],{})
            group['current_human_gold_cases']+=cases_current[case['case_id']]
            answer=output.get('answer');data=answer.get('answer') if isinstance(answer,dict) else None
            group['generated_answer_cases']+=bool(isinstance(data,dict) and data.get('blocks'))
            if review:
                checkable=review.get('checkable_claims',0)
                group['measurable_review_cases']+=checkable>0
                group['passing_tasks']+=bool(checkable>0 and review.get('supported_claims',0)/checkable>=.95
                    and not review.get('wrong_citations',0) and not review.get('unsupported_expansions',0))
                group['independent_blind_cases']+=bool(review.get('independent_blind') and review.get('compared_original')
                    and review.get('gold_fingerprint')==lineage.digest(gold) and reviewer_key(review.get('reviewer'))!=reviewer_key(gold.get('reviewer')))
                group['reviewed_cases']+=1
                for field in fields:group['totals'][field]+=review.get(field,0)
    for group in groups.values():
        t=group['totals'];group.update(mean_recall=sum(group['recalls'])/len(group['recalls']) if group['recalls'] else None,
            mean_mrr=sum(group['mrrs'])/len(group['mrrs']) if group['mrrs'] else None,
            support=wilson(t['supported_claims'],t['checkable_claims']),qualifiers=wilson(t['qualifiers_preserved'],t['qualifiers_required']),
            comparison_validity=wilson(t['comparisons_valid'],t['comparisons_required']),contradiction_detection=wilson(t['contradictions_identified'],t['contradictions_required']))
        group['category_coverage_complete']=all(group['categories'].get(category,0)>=count for category,count,_ in CATEGORIES)
        group['human_acceptance_complete']=bool(current_input and manifest.get('generation')=='model_candidates' and group['category_coverage_complete'] and group['cases']>=MIN_ACCEPTANCE_CASES and
            group['labelled_cases']==group['cases']==group['reviewed_cases']==group['independent_blind_cases']==group['current_human_gold_cases']==group['generated_answer_cases']==group['measurable_review_cases'] and t['checkable_claims']>0)
        group['task_accuracy']=wilson(group['passing_tasks'],group['measurable_review_cases'])
        group['support_target_95_met']=bool(group['human_acceptance_complete'] and group['support']['rate']>=.95
            and group['task_accuracy']['rate']>=.95 and not t['wrong_citations'] and not t['unsupported_expansions'])
    charge=db.one("SELECT COALESCE(SUM(budget_charge),0) amount FROM generation_budget_usage WHERE task_id=? AND status NOT IN ('rejected','failed_before_send')",(row['task_id'],))['amount']
    current_input=current_input and manifest.get('input_snapshot')==input_snapshot(pid)
    if not current_input:
        for group in groups.values():group.update(human_acceptance_complete=False,support_target_95_met=False)
    return {'id':identity,'state':row['state'],'groups':groups,'input_snapshot':manifest.get('input_snapshot'),'input_still_current':current_input,
            'cost_budget_occupation':charge,'cost_note':'包含估算和未知预留，不等于实际扣款','review_history':db.rows('SELECT * FROM knowledge_review_events WHERE comparison_id=? ORDER BY created_at,id',(identity,)),
            'semantic_quality_verified':bool(groups.get('D',{}).get('support_target_95_met')),
            'acceptance_target':{'variant':'D','minimum_real_human_labelled_cases':MIN_ACCEPTANCE_CASES,'independent_blind_review_required':True,
                'minimum_cases_by_category':{category:count for category,count,_ in CATEGORIES},
                'original_comparison_required':True,'generated_answers_required':True,'support_rate':.95,'wrong_citations_allowed':0,'unsupported_expansions_allowed':0},
            'official_subcomparison_present':'D_official' in groups,'note':'未标注、未人工盲审或无可核验主张时不计算已达标结论。'}

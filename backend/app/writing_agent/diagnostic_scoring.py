"""Immutable dimensional reviews, blinded packets, disagreements and frozen policies."""
from .. import db,security
from .common import owned,blob,unblob,digest,fail

DIMENSIONS={
    'condition_truth':'角色、条件、否定和启用状态',
    'evidence_support':'证据支持与已实现/已验证边界',
    'judgment_realization':'判定落实、遗漏和结论范围',
    'novel_claims':'无依据新增主张',
    'coherence':'句间衔接与跨章一致性',
    'revision_preservation':'修改修好/改坏及正确命题保留',
    'length_compliance':'篇幅与明确格式限制',
    'verification_calibration':'核验误放、误报及无法判断',
}
CRITICAL_CASES={'condition_truth':['D01','D02','D04','D05','D16'],'evidence_support':['D03','D07','D08','D10','D13','D14','D16'],'novel_claims':['D03','D06','D07','D10','D11','D13','D14','D16']}

def dimensions(value):
    if not isinstance(value,dict) or set(value)!=set(DIMENSIONS):fail('DIMENSION_SCORES_REQUIRED','需为全部8个维度评分或注明不适用')
    clean={}
    for key,item in value.items():
        if not isinstance(item,dict) or set(item)-{'score','rationale','applicable'}:fail('INVALID_SCORE','分项评分格式无效')
        score=item.get('score');applicable=item.get('applicable',True);reason=str(item.get('rationale','')).strip()
        if type(applicable) is not bool or len(reason)<4 or (applicable and (type(score) is not int or score not in (0,1,2))) or (not applicable and score is not None):fail('INVALID_SCORE','评分为0/1/2；不适用需空分值，每项都需具体依据')
        clean[key]={'score':score,'applicable':applicable,'rationale':reason}
    if not any(v['applicable'] for v in clean.values()):fail('INVALID_SCORE','不能将全部维度标为不适用')
    return clean

def reviews(pid,item_id):
    rows=db.rows('SELECT * FROM diagnostic_reviews WHERE project_id=? AND item_id=? ORDER BY rowid',(pid,item_id))
    for r in rows:r['dimensions']=db.loads(r['dimensions_json'],{})
    return rows

def effective(pid,item_id):
    latest={}
    for r in reviews(pid,item_id):
        if r['method']=='human_reviewed':latest[r['evaluator_id']]=r
    if not latest:return None
    decisions={r['decision'] for r in latest.values()};ids=sorted(r['id'] for r in latest.values())
    adjudication=db.one('SELECT * FROM diagnostic_adjudications WHERE project_id=? AND item_id=? ORDER BY rowid DESC LIMIT 1',(pid,item_id))
    resolved=bool(adjudication and sorted(db.loads(adjudication['review_ids_json'],[]))==ids)
    decision=adjudication['decision'] if resolved else next(iter(decisions)) if len(decisions)==1 else 'unknown'
    scores_complete=all(set(r['dimensions'])==set(DIMENSIONS) for r in latest.values())
    disagreement=len(decisions)>1 or any(len({r['dimensions'].get(k,{}).get('score') for r in latest.values()})>1 for k in DIMENSIONS)
    resolved_scores=db.loads(adjudication['resolved_dimensions_json'],{}) if resolved else {}
    return {'decision':decision,'method':'human_reviewed','reviewer_count':len(latest),'review_ids':ids,'dimensions_complete':scores_complete,'disagreement':disagreement,'adjudicated':resolved,'resolved_dimensions':resolved_scores,'qualification_ready':scores_complete and (not disagreement or (resolved and set(resolved_scores)==set(DIMENSIONS))),'note':adjudication['note'] if resolved else '；'.join(r['note'] for r in latest.values())}

@db.atomic
def review(pid,rid,body,method='human_reviewed'):
    run=owned('diagnostic',rid,pid);item_id=body.get('item_id');blinded=False
    if body.get('packet_id'):
        packet=db.setting('writing_blind_packet:'+str(body['packet_id']),{})
        if packet.get('project_id')!=pid or packet.get('run_id')!=rid or packet.get('evaluator_id')!=body.get('evaluator_id'):fail('INVALID_REVIEW','盲评包与项目、评审人不匹配')
        item_id=packet['items'].get(body.get('blind_item_token'));blinded=True
    item=db.one('SELECT * FROM diagnostic_items WHERE id=? AND run_id=?',(item_id,rid));decision=body.get('decision');note=str(body.get('note','')).strip();evaluator=str(body.get('evaluator_id','local-author')).strip()
    if not item or item['status']!='completed' or decision not in ('pass','fail','unknown') or len(note)<8 or not 1<=len(evaluator)<=100:fail('INVALID_REVIEW','请对完成样本给出评审身份、判断及具体依据')
    scores=dimensions(body['dimensions']) if 'dimensions' in body else {}
    if method=='model_assisted' and not scores:fail('DIMENSION_SCORES_REQUIRED','辅助评审需有完整分项结果')
    duration=body.get('duration_seconds')
    import math
    if duration is not None and (isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not 0<=duration<=86400):fail('INVALID_REVIEW','审阅耗时无效')
    identity=db.uid();db.execute('INSERT INTO diagnostic_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identity,pid,item_id,evaluator,method,int(blinded),db.dumps(scores),decision,note,item['input_hash'],db.now(),duration))
    if method=='human_reviewed':db.execute('UPDATE diagnostic_items SET human_review_json=? WHERE id=?',(db.dumps(effective(pid,item_id)),item_id))
    db.audit(pid,'diagnostic_review_recorded',{'review_id':identity,'item_id':item_id,'method':method,'blinded':blinded})
    return {'review_id':identity,'effective_human_review':effective(pid,item_id),'method':method}

@db.atomic
def adjudicate(pid,rid,body):
    owned('diagnostic',rid,pid);item=db.one('SELECT id FROM diagnostic_items WHERE id=? AND run_id=?',(body.get('item_id'),rid));note=str(body.get('note','')).strip()
    if not item:fail('NOT_FOUND','诊断项不存在',404)
    current=effective(pid,item['id'])
    if not current or not current['disagreement'] or body.get('decision') not in ('pass','fail','unknown') or len(note)<20 or sorted(body.get('review_ids',[]))!=current['review_ids']:fail('ADJUDICATION_REQUIRED','需对当前全部评审分歧逐项说明裁决依据',409)
    chosen=body.get('adopt_review_id');resolved={}
    if chosen:
        selected=next((r for r in reviews(pid,item['id']) if r['id']==chosen and chosen in current['review_ids']),None)
        if not selected or selected['decision']!=body['decision']:fail('INVALID_REVIEW','所选评分不属于当前评审或与裁决判断不一致')
        resolved=selected['dimensions']
    if body['decision']=='pass' and set(resolved)!=set(DIMENSIONS):fail('ADJUDICATION_REQUIRED','通过裁决需明确选用一份当前完整分项评分，并说明理由',409)
    db.execute('INSERT INTO diagnostic_adjudications VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,item['id'],db.dumps(current['review_ids']),body['decision'],note,db.now(),db.dumps(resolved)))
    result=effective(pid,item['id']);db.execute('UPDATE diagnostic_items SET human_review_json=? WHERE id=?',(db.dumps(result),item['id']));return result

@db.atomic
def blind_packet(pid,rid,evaluator):
    run=owned('diagnostic',rid,pid)
    if not isinstance(evaluator,str) or not 1<=len(evaluator)<=100:fail('INVALID_REVIEW','请先填写评审人标识')
    from .evaluations import run_dataset
    cases={c['id']:c for c in run_dataset(run)['cases']};identity=db.uid();mapping={};items=[]
    for row in db.rows("SELECT * FROM diagnostic_items WHERE run_id=? AND status='completed'",(rid,)):
        result=unblob(row['result_blob'])
        if result.get('real_whole_paper'):
            frozen=result.get('evaluation_input') or {}
            token='B-'+digest([identity,row['id']])[:20];mapping[token]=row['id']
            items.append({'blind_item_token':token,'instruction':frozen.get('instruction','缺少冻结任务，无法确认支持性'),
                          'context':frozen.get('context',{}),'answer':result.get('answer',''),
                          'evaluation_standard':result.get('evaluation_standard',{}),'dimensions':DIMENSIONS,
                          'evidence_context_complete':result.get('evidence_context_complete',False)})
            continue
        case=cases[row['case_id']];variant=next(v for v in case['variants'] if v['name']==row['variant_id']);token='B-'+digest([identity,row['id']])[:20];mapping[token]=row['id']
        items.append({'blind_item_token':token,'instruction':case['prompt'],'context':variant['context'],'answer':unblob(row['result_blob']).get('answer',''),'evaluation_standard':case.get('gold'), 'dimensions':DIMENSIONS})
    items.sort(key=lambda r:digest([identity,r['blind_item_token']]))
    db.set_setting('writing_blind_packet:'+identity,{'project_id':pid,'run_id':rid,'evaluator_id':evaluator,'items':mapping,'created_at':db.now()})
    return {'packet_id':identity,'evaluator_id':evaluator,'items':items,'hidden_fields':['model','configuration','arm','variant_name','prior_reviews'],'score_scale':{'0':'错误或未满足','1':'部分满足或仍有明确问题','2':'给定范围内满足','null':'说明不适用的理由'}}

def report(pid,rid):
    run=owned('diagnostic',rid,pid);items=db.rows('SELECT id FROM diagnostic_items WHERE run_id=?',(rid,));summary={key:{'scored':0,'mean':None,'scores':[]} for key in DIMENSIONS};assisted={key:{'scored':0,'mean':None,'scores':[]} for key in DIMENSIONS};machine_decisions={'pass':0,'fail':0,'unknown':0};disagreements=[];human_count=0;model_count=0
    for item in items:
        rr=reviews(pid,item['id']);current=effective(pid,item['id'])
        human_count+=sum(r['method']=='human_reviewed' for r in rr);model_count+=sum(r['method']=='model_assisted' for r in rr)
        if current and current['disagreement']:disagreements.append({'item_id':item['id'],'review_ids':current['review_ids'],'adjudicated':current['adjudicated']})
        latest={r['evaluator_id']:r for r in rr if r['method']=='human_reviewed'}
        for r in latest.values():
            for key,value in r['dimensions'].items():
                if value.get('applicable') and value.get('score') is not None:summary[key]['scores'].append(value['score'])
        latest_models={r['evaluator_id']:r for r in rr if r['method']=='model_assisted'}
        for r in latest_models.values():
            machine_decisions[r['decision']]+=1
            for key,value in r['dimensions'].items():
                if value.get('applicable') and value.get('score') is not None:assisted[key]['scores'].append(value['score'])
    for value in [*summary.values(),*assisted.values()]:
        scores=value.pop('scores');value['scored']=len(scores);value['mean']=sum(scores)/len(scores) if scores else None
    timing=db.one("SELECT SUM(duration_seconds) seconds,SUM(duration_seconds IS NULL) unknown FROM diagnostic_reviews WHERE method='human_reviewed' AND item_id IN (SELECT id FROM diagnostic_items WHERE run_id=?)",(rid,))
    return {'run_id':rid,'dimensions':summary,'assisted_dimensions':assisted,'assisted_decisions':machine_decisions,'human_review_records':human_count,'model_review_records':model_count,'disagreements':disagreements,'unresolved_disagreements':sum(not d['adjudicated'] for d in disagreements),'recorded_human_minutes':timing['seconds']/60 if timing['seconds'] is not None else None,'human_durations_unknown':timing['unknown'] or 0,'human_time_basis':'browser form elapsed time or reviewer-supplied duration, not attention tracking','scope':'dimension_scores_are_observations_not_population_accuracy'}

@db.atomic
def freeze_policy(pid,rid,body):
    run=owned('diagnostic',rid,pid);thresholds=body.get('thresholds');note=str(body.get('note','')).strip()
    from . import profiles
    from .evaluations import run_dataset
    profile=profiles.get(pid,run['profile_id']);summary=report(pid,rid)
    if not summary['human_review_records'] or summary['unresolved_disagreements'] or len(note)<20:fail('CALIBRATION_REQUIRED','先完成真实人工标注和分歧处理，再记录阈值依据',409)
    if not isinstance(thresholds,dict) or set(thresholds)!=set(DIMENSIONS) or any(type(v) is not int or v not in (0,1,2) for v in thresholds.values()):fail('INVALID_POLICY','需明确全部维度的最低分值')
    critical=body.get('critical_dimensions',['condition_truth','evidence_support','novel_claims'])
    if not isinstance(critical,list) or not set(critical)<=set(DIMENSIONS) or not {'condition_truth','evidence_support','novel_claims'}<=set(critical):fail('INVALID_POLICY','不得取消条件、证据及无依据主张的关键错误门槛')
    all_cases=[c['id'] for c in run_dataset(run)['cases']]
    content={'diagnostic_run_id':rid,'dataset_hash':run['dataset_hash'],'profile_fingerprint':profile['route_fingerprint'],'thresholds':thresholds,'critical_dimensions':critical,'critical_cases':{key:CRITICAL_CASES.get(key,all_cases) for key in critical},'critical_minimum':2,'minimum_repetitions':3,'minimum_reviewers':1,'require_disagreement_resolution':True,'note':note,'role':profile['role'],'scope':'assisted_writing_requires_current_human_adoption_review'}
    identity=db.uid();db.execute('INSERT INTO writing_quality_policies VALUES(?,?,?,?,?,?)',(identity,pid,profile['role'],db.dumps(content),digest(content),db.now()))
    return {'id':identity,**content,'content_hash':digest(content)}

def qualify(pid,rid,note,policy_id):
    from .evaluations import get,dataset
    from . import profiles
    run=get(pid,rid)
    if run['policy'].get('real_paper'):fail('CALIBRATION_REQUIRED','单篇论文评价不能授予模型通用资格，请另用完整校准题集',409)
    profile=profiles.get(pid,run['profile_id']);policy=db.one('SELECT * FROM writing_quality_policies WHERE id=? AND project_id=?',(policy_id,pid))
    if not policy:fail('CALIBRATION_REQUIRED','请先依据实际人工分项标注冻结角色政策',409)
    p=db.loads(policy['content_json'],{});expected={(c['id'],v['name'],r) for c in run['evaluation_dataset']['cases'] for v in c['variants'] for r in range(p['minimum_repetitions'])};seen={(i['case_id'],i['variant_id'],i['repetition']) for i in run['items']}
    if p['dataset_hash']!=run['dataset_hash'] or digest(dataset())!=run['dataset_hash'] or p['profile_fingerprint']!=profile['route_fingerprint'] or not profile['configuration_current'] or seen!=expected or len(str(note))<12:fail('QUALIFICATION_INCOMPLETE','当前配置、完整重复样本或政策尚未满足',409)
    for item in run['items']:
        current=effective(pid,item['id'])
        if item['status']!='completed' or not current or current['decision']!='pass' or not current['qualification_ready']:fail('QUALIFICATION_INCOMPLETE','未审阅、失败或未裁决样本不能授予资格',409)
        applicable_reviews=[r for r in reviews(pid,item['id']) if r['id'] in current['review_ids']]
        if current['disagreement'] and current['adjudicated']:applicable_reviews=[{'dimensions':current['resolved_dimensions']}]
        for r in applicable_reviews:
            for key,value in r['dimensions'].items():
                threshold=max(p['thresholds'][key],p['critical_minimum'] if key in p['critical_dimensions'] else 0)
                if key in p['critical_dimensions'] and item['case_id'] in p.get('critical_cases',CRITICAL_CASES).get(key,[]) and not value['applicable']:fail('QUALIFICATION_INCOMPLETE','关键样例的必需评分不能标为不适用',409)
                if value['applicable'] and value['score']<threshold:fail('QUALIFICATION_INCOMPLETE','关键错误或分项分数未满足冻结政策',409)
    result={'diagnostic_run_id':rid,'policy_id':policy_id,'note':note,'qualification_scope':p['scope'],'role':profile['role'],'no_truth_guarantee':True,'created_at':db.now()}
    db.execute("UPDATE writing_profiles SET qualification='qualified_for_policy',evaluation_ref=? WHERE id=?",(rid,profile['id']));db.execute('UPDATE diagnostic_runs SET report_blob=? WHERE id=?',(blob(result),rid));return result

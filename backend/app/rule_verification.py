"""Budgeted semantic review of existing candidates with deterministic evidence guards."""
import hashlib,re
from collections import Counter,deque
from . import db,provider,source_payload,rule_governance,summary_billing,model_routing
from .rule_review_contract import SYSTEM,VERSION,validate
from . import rule_review_runtime as runtime


def fingerprint(rule,source,profile='primary'):
    cfg=model_routing.config() if profile=='gpt' else provider.config()
    conditions=db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(rule['id'],))
    policy=rule_governance.source_policy(rule['project_id']).get(source['file_id'],'course')
    value=[VERSION,profile,rule['id'],rule['statement'],rule['modality'],rule.get('scope','global'),db.loads(rule.get('operations'),[]),db.loads(conditions['expression'],{}) if conditions else {},source_payload.source_hash(source),source.get('source_file',''),cfg['base_url'],cfg['protocol'],cfg['model'],policy]
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def legacy_fingerprint(rule,source):
    cfg=provider.config()
    return hashlib.sha256(db.dumps(['rule-semantic-review-v1',rule['id'],rule['statement'],rule['modality'],source_payload.source_hash(source),cfg['base_url'],cfg['model'],rule_governance.source_policy(rule['project_id'])]).encode()).hexdigest()


def save_attempt(pid,fp,tid,state,value,error=None):
    db.execute('INSERT OR REPLACE INTO rule_verification_attempts VALUES(?,?,?,?,?,?,?)',(pid,fp,tid,state,db.dumps(value),error,db.now()))
    db.execute('INSERT INTO task_events(task_id,event_type,payload,created_at) VALUES(?,?,?,?)',(tid,'rule_review_decision',db.dumps({'rule_id':value.get('rule_id'),'fingerprint':fp,'state':state,'error':error}),db.now()))


def task_coverage(tid):
    scope=set();latest={}
    for row in db.rows("SELECT event_type,payload FROM task_events WHERE task_id=? AND event_type IN ('rule_review_scope','rule_review_decision') ORDER BY id",(tid,)):
        item=db.loads(row['payload'],{})
        if row['event_type']=='rule_review_scope':scope.update(item.get('rule_ids',[]))
        elif item.get('rule_id'):latest[item['rule_id']]=item['state']
    valid={'machine_verified','knowledge','example','conditional_reference','needs_resolution'}
    return {'total':len(scope),'checked':sum(latest.get(rid) in valid for rid in scope),'accepted':sum(latest.get(rid)=='machine_verified' for rid in scope),'reference':sum(latest.get(rid) in ('knowledge','example','conditional_reference') for rid in scope),'uncertain':sum(latest.get(rid)=='needs_resolution' for rid in scope)}


def guards(statement,quote):
    def chinese_number(raw):
        digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits.update({'〇':0,'两':2})
        if all(c in digits for c in raw):return int(''.join(str(digits[c]) for c in raw))
        total=section=value=0
        for char in raw:
            if char in digits:value=digits[char]
            else:
                unit={'十':10,'百':100,'千':1000,'万':10000}[char]
                if unit==10000:total+=(section+value)*unit;section=value=0
                else:section+=(value or 1)*unit;value=0
        return total+section+value
    def numbers(text):
        text=re.sub(r'([零〇一二两三四五六七八九十百千万]+)(?=篇|字|个|项|倍|年|页|厘米|毫米)',lambda m:str(chinese_number(m[1])),text)
        text=re.sub(r'^\s*(?:第?\d+[条项、.：:]\s*)','',text)
        return [(value,unit.replace('％','%').lower()) for value,unit in re.findall(r'(\d+(?:\.\d+)?)[ \t]*(篇|字|个|项|倍|%|％|年|页|厘米|毫米|cm|mm|pt|磅)?',text)]
    def logic(text):
        text=text.replace('不得不','必须')
        # Bounds are not a prohibition on the action itself.
        text=re.sub(r'不得超过|不超过|不高于','上限',text)
        text=re.sub(r'不少于|不低于','下限',text)
        result=set()
        for pattern,label in [(r'下限|至少|最低','minimum'),(r'上限|至多|最多','maximum'),(r'不得|禁止|不能|不允许|不应','forbidden'),(r'不必|无需|无须|不需要','not_required'),(r'如果|除非|除.{0,20}外|当.{1,30}时|仅当|只有','conditional')]:
            if re.search(pattern,text):result.add(label)
        return result
    def strength(text):
        if re.search(r'不必|无需|无须|不需要',text):return 'not_required'
        if re.search(r'建议|推荐|宜',text):return 'recommended'
        if re.search(r'必须|应当|须|需要|应(?:对|交代|包含|提供|说明|采用|使用|确保)',text):return 'required'
        if '可以' in text:return 'permitted'
        return None
    a,b=strength(statement),strength(quote)
    def grades(text):return {g.upper() for g in re.findall(r'([ABCDＡＢＣＤ])\s*级',text,re.I)}
    return sorted(numbers(statement))==sorted(numbers(quote)) and logic(statement)==logic(quote) and grades(statement)==grades(quote) and (not a or not b or a==b)


def review(task,payload,job_id,sources):
    from .tasks import model_call,event
    from .rule_pipeline import exact_quote
    pid=task['project_id'];lookup={s['id']:s for s in sources};policy=rule_governance.source_policy(pid)
    # Completed local context can span multiple old search chunks. Keep its
    # derived source reviewable within exactly the selected original pages.
    pages={s.get('page_id') for s in sources if s.get('page_id')}
    for extra in db.rows("SELECT s.*,f.sha256 AS file_sha256,f.name AS source_file,p.text AS page_text FROM sources s JOIN files f ON f.id=s.file_id JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 AND f.role='rules' AND f.status='ready' AND p.review_state!='not_applicable' AND json_extract(s.locator,'$.kind')='rule_excerpt'",(pid,)):
        if extra['page_id'] in pages:lookup[extra['id']]=extra
    requested_profile=payload.get('provider_profile','auto' if payload.get('allow_gpt') else 'primary')
    if requested_profile not in ('primary','gpt','auto'):raise ValueError('审核模型路线无效')
    selected_config=model_routing.config() if requested_profile=='gpt' else provider.config()
    cached={}
    for run in db.rows("SELECT * FROM generation_runs WHERE project_id=? AND status='completed' AND json_extract(manifest,'$.purpose')='rule_verification' ORDER BY created_at",(pid,)):
        for fp in db.loads(run['manifest'],{}).get('verification_fingerprints',[]):cached[fp]=run
    rule_governance.review_project(pid,list(lookup))
    candidates=[]
    for rule in db.rows("SELECT * FROM rules WHERE project_id=? AND state='candidate' AND modality NOT IN ('example','counterexample') ORDER BY created_at,id",(pid,)):
        source=lookup.get(rule['source_id'])
        if not source:continue
        decision=db.one('SELECT outcome FROM rule_decisions WHERE rule_id=? AND restored=0 ORDER BY id DESC LIMIT 1',(rule['id'],))
        if decision and decision['outcome'] not in ('needs_resolution',):continue
        fp=fingerprint(rule,source,requested_profile);prior=db.one('SELECT * FROM rule_verification_attempts WHERE project_id=? AND fingerprint=?',(pid,fp))
        unresolved=db.one("SELECT * FROM rule_verification_attempts WHERE project_id=? AND state='unknown' AND json_extract(result,'$.rule_id')=? AND json_extract(result,'$._review_source_hash')=? LIMIT 1",(pid,rule['id'],source_payload.source_hash(source)))
        if unresolved and not payload.get('retry_unknown'):
            cached[fp]={'id':'unknown:'+fp,'manifest':'{}','blocked_error':'相同规则及原文存在未决审核请求，未自动跨模型重复发送'}
        old_fp=legacy_fingerprint(rule,source)
        if fp not in cached and old_fp in cached and cached[old_fp]['model']==selected_config['model']:
            cached[fp]={**cached[old_fp],'legacy_review':True}
        if prior and prior['state']=='invalid' and payload.get('retry_invalid'):
            cached.pop(fp,None);prior=None
        if prior and prior['state']=='unknown' and payload.get('retry_unknown'):
            cached.pop(fp,None);prior=None
        if prior and fp not in cached and prior['state'] not in ('running','budget_wait'):
            raw=db.loads(prior['result'],{})
            cached[fp]={'id':'decision:'+fp,'model':selected_config['model'],'manifest':'{}','cached_decisions':[raw] if raw.get('rule_id') else [],'blocked_error':prior.get('error') if prior['state']=='unknown' else None}
        if prior and prior['state']=='running' and fp not in cached:
            db.execute("UPDATE rule_verification_attempts SET state='unknown',error='复核进程中断，等待核对远端结果' WHERE project_id=? AND fingerprint=?",(pid,fp))
            cached[fp]={'id':'unknown:'+fp,'manifest':'{}','blocked_error':'先前审核结果未知，未自动重复发送'}
        candidates.append((rule,source,fp))
    db.execute('INSERT INTO task_events(task_id,event_type,payload,created_at) VALUES(?,?,?,?)',(task['id'],'rule_review_scope',db.dumps({'version':VERSION,'rule_ids':[r['id'] for r,_,_ in candidates]}),db.now()))
    checked=0;errors=[];warnings=[];chunks=[];chunk=[];cached_groups={};budget_blocked=False;reused=0
    def outgoing(items):
        used={s['id']:s for _,s,_ in items}
        return db.dumps({'sources':[{**source_payload.outgoing(s),'source_file':s.get('source_file',''),'authority':policy.get(s['file_id'],'course')} for s in used.values()],'candidates':[{'rule_id':r['id'],'source_id':r['source_id'],'statement':r['statement'],'scope':r['scope'],'modality':r['modality'],'conditions':db.loads((db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(r['id'],)) or {}).get('expression'),{})} for r,_,_ in items]})
    cfg=runtime.request_config(selected_config,runtime.output_limit(selected_config,4),requested_profile!='gpt')
    for item in candidates:
        if item[2] in cached:
            cached_groups.setdefault(cached[item[2]]['id'],[]).append(item);continue
        proposed=chunk+[item]
        if chunk and (len(chunk)>=4 or len(outgoing(proposed))>5000 or source_payload.token_upper_bound(SYSTEM,outgoing(proposed))+runtime.output_limit(cfg,len(proposed))>cfg['context_limit']):chunks.append(chunk);chunk=[]
        chunk.append(item)
    if chunk:chunks.append(chunk)
    pending=deque((items,0) for items in list(cached_groups.values())+chunks)
    failure_counts=Counter();failure_details=[];pause_reason=None;route_failures=0;reasoning_failures=0;recovery_requests=0;index=0
    def progress():
        coverage=task_coverage(task['id'])
        value={**coverage,'semantic_checked':coverage['checked'],'semantic_total':coverage['total'],'unreviewed_count':coverage['total']-coverage['checked'],'failure_counts':dict(failure_counts),'failure_details':failure_details,'recovery_requests':recovery_requests,'pause_reason':pause_reason}
        previous=db.loads((db.one('SELECT result FROM tasks WHERE id=?',(task['id'],)) or {}).get('result'),{}) or {}
        db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps({**previous,'review':{**previous.get('review',{}),**value}}),task['id']))
        return coverage
    while pending:
        if pause_reason:break
        items,depth=pending.popleft();index+=1
        prior_run=cached.get(items[0][2]);limit=runtime.output_limit(selected_config,len(items))
        content=outgoing(items)
        if not prior_run and source_payload.token_upper_bound(SYSTEM,content)+limit>cfg['context_limit']:
            if len(items)>1:
                pending.extendleft((part,depth) for part in reversed(runtime.smaller(items)));continue
            message='该条原文超过接口上下文上限，保留候选并继续审核其他条款'
            errors.append(message);failure_counts['context_limit']+=1
            failure_details.append({'kind':'context_limit','rule_ids':[r['id'] for r,_,_ in items],'message':message})
            for rule,source,fp in items:save_attempt(pid,fp,task['id'],'invalid',{'rule_id':rule['id'],'_review_source_hash':source_payload.source_hash(source)},message)
            progress();continue
        coverage=progress()
        event(task['id'],f'{selected_config["model"]} 疑难规则复核：已审核 {coverage["checked"]}/{coverage["total"]} 条，本批 {len(items)} 条'+('（缩小批次补齐）' if depth else ''),95)
        from .security import put_blob
        manifest={'file_ids':sorted({s['file_id'] for _,s,_ in items}),'source_ids':sorted({s['id'] for _,s,_ in items}),'rule_ids':[r['id'] for r,_,_ in items],'source_hashes':{s['id']:source_payload.source_hash(s) for _,s,_ in items},'review_version':VERSION,'review_input_blob':put_blob(content.encode()),'rule_job_id':job_id,'purpose':'rule_verification','verification_fingerprints':[fp for _,_,fp in items],'progress_percent':95,'streaming_text':True,'recovery_attempt':depth,'request_options':{'max_output_tokens':limit,'reasoning_effort':'low'}}
        selected={**task,'payload':db.dumps({**payload,'provider_profile':'primary'})}
        def request(gpt):
            base=model_routing.config() if gpt else provider.config()
            target_cfg=runtime.request_config(base,runtime.output_limit(base,len(items)),not gpt)
            reserve,_=summary_billing.request_reservation(target_cfg,SYSTEM,content);job=db.one('SELECT * FROM rule_jobs WHERE id=?',(job_id,))
            blocked=not summary_billing.budget(pid,reserve)['can_call'] or summary_billing.job_charge(job_id)+reserve>job['budget_limit'] or gpt and summary_billing.job_charge(job_id,'gpt')+reserve>job['gpt_budget']
            if blocked:
                for profile in (('gpt-fallback','material-summary') if gpt else ('material-summary','gpt-fallback')):
                    if profile=='gpt-fallback' and not model_routing.ready():continue
                    summary_billing.reconcile(pid,profile=profile)
                    if summary_billing.budget(pid,reserve)['can_call'] and summary_billing.job_charge(job_id)+reserve<=job['budget_limit'] and (not gpt or summary_billing.job_charge(job_id,'gpt')+reserve<=job['gpt_budget']):break
            if gpt:_,key=model_routing.snapshot()
            else:
                with provider.settings_lock():state=provider._REQUEST.get();key=state['key'] if state else provider.get_key()
            routed={**selected,'payload':db.dumps({**payload,'provider_profile':'gpt' if gpt else 'primary'})}
            with provider.request_scope(target_cfg,key):return model_call(routed,SYSTEM,content,manifest)
        try:
            if prior_run:
                from .security import safe_object
                if prior_run.get('blocked_error'):raise provider.ProviderError(prior_run['blocked_error'],unknown=True)
                response={'text':db.dumps({'decisions':prior_run['cached_decisions']}) if 'cached_decisions' in prior_run else safe_object(db.loads(prior_run['manifest'])['output_blob']).read_text(encoding='utf-8')}
            else:
                for rule,source,fp in items:save_attempt(pid,fp,task['id'],'running',{'rule_id':rule['id'],'_review_source_hash':source_payload.source_hash(source)})
                use_gpt=requested_profile=='gpt'
                if depth:recovery_requests+=1
                try:response=request(bool(use_gpt))
                except provider.ProviderError as primary_error:
                    if primary_error.unknown or primary_error.response_result is not None or not primary_error.fallback_allowed or use_gpt or payload.get('provider_profile')=='primary' or not payload.get('allow_gpt') or not model_routing.ready():raise
                    response=request(True)
            parsed=provider.parse_json(response['text'])
            choices,invalid,extra=validate(parsed,items,guards,exact_quote,legacy=bool(prior_run and prior_run.get('legacy_review')))
            warnings.extend(extra)
        except (provider.ProviderError,ValueError) as error:
            message=provider.redact(error);kind=runtime.failure_kind(error);failure_counts[kind]+=1
            failure_details.append({'kind':kind,'rule_ids':[r['id'] for r,_,_ in items],'message':message,'attempt':depth+1,'cached':bool(prior_run)})
            if isinstance(error,summary_billing.BudgetExceeded):
                for rule,source,fp in items:db.execute("UPDATE rule_verification_attempts SET state='budget_wait',error=? WHERE project_id=? AND fingerprint=?",(provider.redact(error),pid,fp))
                budget_blocked=True;errors.append(message);progress();break
            state='unknown' if isinstance(error,provider.ProviderError) and error.unknown else 'invalid'
            for rule,source,fp in items:save_attempt(pid,fp,task['id'],state,{'rule_id':rule['id'],'_review_source_hash':source_payload.source_hash(source)},provider.redact(error))
            if not prior_run:
                reasoning_failures=reasoning_failures+1 if kind=='reasoning_exhausted' else 0
                route_failures=route_failures+1 if kind=='outcome_unknown' else 0
                if reasoning_failures>=2 or route_failures>=2 or kind in ('provider_rejected','rate_limited'):
                    pause_reason=runtime.PAUSE_MESSAGES.get(kind,message)
                if not pause_reason and kind in ('invalid_output','reasoning_exhausted') and depth+1<runtime.MAX_CONTENT_ATTEMPTS and requested_profile!='gpt':
                    pending.extendleft((part,depth+1) for part in reversed(runtime.smaller(items)))
                    warnings.append('已知不可用响应已保存，使用同一主模型缩小批次补齐；未切换 GPT')
                    progress();continue
            errors.append(message);progress();continue
        route_failures=reasoning_failures=0
        with db.transaction():
            for before,source,fp in items:
                current=db.one('SELECT * FROM rules WHERE id=?',(before['id'],));live_source=db.one('SELECT s.*,f.sha256 AS file_sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(source['id'],))
                if current!=before or not live_source or source_payload.source_hash(live_source)!=source_payload.source_hash(source):
                    message='审核期间规则或来源已变化，未覆盖当前内容：'+before['id'];errors.append(message)
                    save_attempt(pid,fp,task['id'],'stale',{'rule_id':before['id']},message);continue
                if before['id'] in invalid:
                    message=invalid[before['id']];errors.append(message)
                    rule_governance.record(pid,before,current,source,'needs_resolution',[message])
                    save_attempt(pid,fp,task['id'],'invalid',{'rule_id':before['id'],'_review_error':message},message);continue
                item=choices[before['id']];relation=item['relation'];quote=item['quote']
                outcome='needs_resolution';reasons=['复核仍不足以确定条款，原内容保留']
                if relation=='equivalent' and quote:
                    semantic=item.get('kind') in ('requirement','recommendation','criterion','conditional')
                    proposed=before if semantic else {**before,'statement':quote}
                    outcome,updates,reasons=rule_governance.review_one(proposed,source,policy,semantic=semantic,evidence=quote)
                    if outcome=='machine_verified':
                        db.execute('UPDATE rules SET state=?,modality=?,scope=?,checker=?,rule_key=?,value=?,category=? WHERE id=?',(*(updates[k] for k in ('state','modality','scope','checker','rule_key','value','category')),before['id']))
                        reasons=['语义复核与原文校验通过，候选表述未改写','原文依据：'+quote,*reasons]
                    elif outcome in ('knowledge','example','conditional_reference'):
                        db.execute("UPDATE rules SET state='reference_only' WHERE id=?",(before['id'],))
                elif relation in ('not_a_rule','example','counterexample'):
                    outcome='knowledge' if relation=='not_a_rule' else 'example';reasons=['模型复核为知识说明或示例，保留内容并从自动约束中排除']
                    db.execute("UPDATE rules SET state='reference_only' WHERE id=?",(before['id'],))
                after=db.one('SELECT * FROM rules WHERE id=?',(before['id'],))
                rule_governance.record(pid,before,after,source,outcome,reasons)
                save_attempt(pid,fp,task['id'],outcome,{**item,'_review_source_hash':source_payload.source_hash(source)});checked+=1
                if prior_run:reused+=1
            progress()
    if pause_reason:errors.append(pause_reason)
    review=rule_governance.review_project(pid,list(lookup))
    remaining=db.one("SELECT COUNT(*) n FROM rules r JOIN rule_decisions d ON d.id=(SELECT MAX(id) FROM rule_decisions WHERE rule_id=r.id AND restored=0) WHERE r.project_id=? AND r.state='candidate' AND d.outcome IN ('needs_resolution','conflict')",(pid,))['n']
    coverage=task_coverage(task['id'])
    return {**review,'semantic_checked':coverage['checked'],'semantic_total':coverage['total'],'accepted_count':coverage['accepted'],'reference_count':coverage['reference'],'uncertain_count':coverage['uncertain'],'reused_decisions':reused,'unreviewed_count':coverage['total']-coverage['checked'],'coverage_complete':coverage['checked']==coverage['total'],'response_warnings':warnings,'unresolved_rule_count':remaining,'verification_errors':errors,'budget_blocked':budget_blocked,'requested_profile':requested_profile,'failure_counts':dict(failure_counts),'failure_details':failure_details,'recovery_requests':recovery_requests,'pause_reason':pause_reason}

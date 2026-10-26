"""Optional exact interval reconciliation. Ambiguous intervals retain reservations."""
from decimal import Decimal,InvalidOperation
from .. import provider,db

def sample(cfg):
    try:
        with provider.client({**cfg,'timeout_seconds':15}) as client:response=client.get(provider.usage_endpoint(cfg['base_url']),headers=provider.headers('openai'))
        if response.status_code!=200:return None
        raw=response.json();normal=provider.normalize_account_usage(raw)
        if normal['unit_source']!='response' or normal['unit'].upper()!=str(cfg.get('currency','')).upper():return None
        payload=raw.get('data',raw) if normal['response_scope']=='data' else raw
        stat=next((s for s in payload.get('model_stats',[]) if s.get('model')==cfg['model']),None)
        if not stat or any(type(stat.get(k)) is not int or stat[k]<0 for k in ('requests','input_tokens','output_tokens')):return None
        amounts={k:str(stat[k]) for k in ('cost','actual_cost','account_cost') if k in stat}
        if not amounts:return None
        from ..summary_billing import key_hash
        snapshot=provider._REQUEST.get();credential=snapshot['key'] if snapshot else provider.get_key()
        return {'model':cfg['model'],'base_url':cfg['base_url'],'credential_hash':key_hash(credential),'unit':normal['unit'],'requests':stat['requests'],'input_tokens':stat['input_tokens'],'output_tokens':stat['output_tokens'],'amounts':amounts,'remaining':normal['remaining_exact'],'at':db.now()}
    except Exception:return None

def reconcile(before,after,usage,request_count=1):
    if not before or not after or before['unit']!=after['unit'] or before['model']!=after['model'] or after['requests']-before['requests']!=request_count:return None
    for field in ('credential_hash','base_url'):
        if before.get(field)!=after.get(field):return None
    inp=usage.get('input_tokens');out=usage.get('output_tokens');raw=usage.get('raw',{});cached=raw.get('prompt_cache_hit_tokens',0) or raw.get('prompt_tokens_details',{}).get('cached_tokens',0) or 0
    if type(inp) is not int or type(out) is not int or type(cached) is not int or not 0<=cached<=inp:return None
    remote_input=after['input_tokens']-before['input_tokens']
    remote_output=after['output_tokens']-before['output_tokens'];outputs={out:'reported_output'}
    total=raw.get('total_tokens');raw_input=raw.get('prompt_tokens',raw.get('input_tokens'));raw_output=raw.get('completion_tokens',raw.get('output_tokens'))
    if type(total) is int and type(raw_input) is int and type(raw_output) is int and raw_input==inp and raw_output==out and total>=inp+out:
        outputs.setdefault(total-inp,'reported_total_minus_input')
    if remote_output not in outputs or remote_input not in (inp-cached,inp,inp+cached):return None
    try:
        deltas=[Decimal(after['amounts'][k])-Decimal(v) for k,v in before['amounts'].items() if k in after['amounts']]
        if not deltas or any(not v.is_finite() or v<0 for v in deltas):return None
        if before.get('remaining') is not None and after.get('remaining') is not None:
            balance=Decimal(before['remaining'])-Decimal(after['remaining'])
            if not balance.is_finite() or balance<0:return None
            deltas.append(balance)
        amount=max(deltas)
        return {'amount':float(amount),'currency':after['unit'],'method':'exact_model_interval_matching_request_count_and_token_deltas','matched_requests':request_count,'before':before,'after':after,'usage_match':{'input_tokens':inp,'output_tokens':out,'cached_tokens_if_reported':cached,'provider_input_delta':remote_input,'input_counting':'cache_excluded' if remote_input==inp-cached and cached else 'cache_included' if remote_input==inp else 'cache_reported_separately','provider_output_delta':remote_output,'output_counting':outputs[remote_output]}}
    except (InvalidOperation,ValueError,TypeError):return None

def recover_adjacent(pid):
    """Recheck old completed intervals using preserved adjacent bill boundaries."""
    rows=db.rows("SELECT * FROM generation_runs WHERE project_id=? AND status NOT IN ('rejected','failed_before_send') ORDER BY created_at,rowid",(pid,));settled=[]
    for i,row in enumerate(rows):
        if row['cost'] is not None or row['status'] not in ('completed','completed_invalid'):continue
        meta=db.loads(row['manifest'],{});observation=meta.get('billing_interval_observation',{});befores=[observation.get('before')];afters=[observation.get('after')];sources=[]
        # Concurrent responses may finish out of creation order. Search a small
        # same-route neighborhood, then require an exact one-request/token match.
        neighbors=[(i-distance,'before') for distance in range(1,9)]+[(i+distance,'after') for distance in range(1,9)]
        for neighbor_index,field in neighbors:
            if neighbor_index<0 or neighbor_index>=len(rows):continue
            neighbor=rows[neighbor_index];nm=db.loads(neighbor['manifest'],{})
            if nm.get('credential_hash')!=meta.get('credential_hash') or nm.get('provider',{}).get('base_url')!=meta.get('provider',{}).get('base_url') or neighbor['model']!=row['model']:continue
            proof=nm.get('billing_interval_proof',{});observed=nm.get('billing_interval_observation',{})
            if field=='before':befores.extend([proof.get('after'),observed.get('after')]);sources.append(neighbor['id'])
            if field=='after':afters.extend([proof.get('before'),observed.get('before')]);sources.append(neighbor['id'])
        proof=None
        for before in befores:
            for after in afters:
                if not before or not after:continue
                key=meta.get('credential_hash');base=meta.get('provider',{}).get('base_url')
                b={**before,'credential_hash':before.get('credential_hash',key),'base_url':before.get('base_url',base)};a={**after,'credential_hash':after.get('credential_hash',key),'base_url':after.get('base_url',base)}
                if b['credential_hash']!=key or a['credential_hash']!=key or b['base_url']!=base or a['base_url']!=base:continue
                proof=reconcile(b,a,db.loads(row['usage'],{}))
                if proof:break
            if proof:break
        if not proof:continue
        proof['boundary_provenance']={'kind':'stored_or_adjacent_completed_request_boundaries','source_run_ids':sources};meta['billing_interval_proof']=proof
        with db.transaction() as c:
            changed=c.execute('UPDATE generation_runs SET cost=?,manifest=? WHERE id=? AND cost IS NULL AND status IN (\'completed\',\'completed_invalid\')',(proof['amount'],db.dumps(meta),row['id'])).rowcount
        if changed:settled.append(row['id'])
    return {'settled_run_ids':settled,'unknowns_not_matching_remain_reserved':True}

def recover_closed_group(pid,current_sample=None):
    """Settle only a closed consecutive block, never distribute a group bill arbitrarily."""
    from .common import digest
    rows=db.rows("SELECT * FROM generation_runs WHERE project_id=? AND status NOT IN ('rejected','failed_before_send') ORDER BY created_at,rowid",(pid,));i=0;groups=[]
    while i<len(rows):
        if rows[i]['cost'] is not None or db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?',(rows[i]['id'],)):i+=1;continue
        start=i
        while i<len(rows) and rows[i]['cost'] is None and not db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?',(rows[i]['id'],)):i+=1
        block=rows[start:i]
        if any(x['status'] not in ('completed','completed_invalid') for x in block):continue
        metas=[db.loads(x['manifest'],{}) for x in block];keys={(m.get('credential_hash'),m.get('provider',{}).get('base_url'),x['model']) for m,x in zip(metas,block)}
        if len(keys)!=1:continue
        key,base,model=next(iter(keys));before=metas[0].get('billing_interval_observation',{}).get('before');after=metas[-1].get('billing_interval_observation',{}).get('after')
        def same_route(row):
            m=db.loads(row['manifest'],{});return m.get('credential_hash')==key and m.get('provider',{}).get('base_url')==base and row['model']==model
        if not before and start and same_route(rows[start-1]):before=db.loads(rows[start-1]['manifest'],{}).get('billing_interval_proof',{}).get('after')
        if not after and i<len(rows) and same_route(rows[i]):after=db.loads(rows[i]['manifest'],{}).get('billing_interval_proof',{}).get('before')
        if not after and i==len(rows) and current_sample and current_sample.get('credential_hash')==key and current_sample.get('base_url')==base:after=current_sample
        # Legacy observations are scoped by their trusted request metadata;
        # never attach an unscoped or different-account current observation.
        if before:before={**before,'credential_hash':before.get('credential_hash',key),'base_url':before.get('base_url',base)}
        if after:after={**after,'credential_hash':after.get('credential_hash',key),'base_url':after.get('base_url',base)}
        if (before and (before['credential_hash']!=key or before['base_url']!=base)) or (after and (after['credential_hash']!=key or after['base_url']!=base)):continue
        usage=[db.loads(x['usage'],{}) for x in block]
        if any(type(u.get('input_tokens')) is not int or type(u.get('output_tokens')) is not int for u in usage):continue
        cached=sum(u.get('raw',{}).get('prompt_cache_hit_tokens',0) or u.get('raw',{}).get('prompt_tokens_details',{}).get('cached_tokens',0) or 0 for u in usage)
        total={'input_tokens':sum(u['input_tokens'] for u in usage),'output_tokens':sum(u['output_tokens'] for u in usage),'raw':{'prompt_cache_hit_tokens':cached}}
        proof=reconcile(before,after,total,len(block))
        if not proof:continue
        if len(block)==1:
            meta=metas[0];meta['billing_interval_proof']=proof
            db.execute('UPDATE generation_runs SET cost=?,manifest=? WHERE id=? AND cost IS NULL',(proof['amount'],db.dumps(meta),block[0]['id']));groups.append({'single_run':block[0]['id'],'amount':proof['amount']});continue
        group=db.one('SELECT * FROM generation_billing_groups WHERE project_id=? AND base_url=? AND credential_hash=? AND model=?',(pid,base,key,model))
        gid=digest([pid,[x['id'] for x in block],proof]);proof['run_ids']=[x['id'] for x in block];proof['per_request_costs_unknown']=True
        with db.transaction():
            if group:
                original=db.one('SELECT * FROM generation_billing_groups WHERE id=?',(group['id'],));prior=db.loads(original['evidence'],{})
                if original['currency']!=proof['currency'] or prior.get('method') not in ('exact_model_interval_matching_request_count_and_token_deltas','disjoint_exact_intervals_sum'):continue
                if any(db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?',(x['id'],)) for x in block):continue
                intervals=prior.get('intervals',[prior]);combined={'method':'disjoint_exact_intervals_sum','intervals':[*intervals,proof],'run_ids':[*prior.get('run_ids',[]),*proof['run_ids']],'per_request_costs_unknown':True};gid=original['id']
                db.execute('UPDATE generation_billing_groups SET amount=?,evidence=?,updated_at=? WHERE id=?',(float(Decimal(str(original['amount']))+Decimal(str(proof['amount']))),db.dumps(combined),db.now(),gid))
            else:db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,pid,base,key,model,proof['amount'],proof['currency'],block[0]['id'],db.dumps(proof),db.now()))
            for row in block:db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(row['id'],gid))
        groups.append({'group_id':gid,'requests':len(block),'amount':proof['amount']})
    return {'matched_closed_intervals':groups,'unmatched_reservations_retained':True}

@db.atomic
def recover_overlapping_intervals(pid):
    """Match a whole interval including already settled concurrent neighbors.

    An exact single call may finish inside another call's observed interval.
    Keep its original cost as evidence, but let the budget view count the exact
    group once. Never infer the other call's price by subtracting the first.
    """
    from .common import digest
    rows=db.rows("SELECT * FROM generation_runs WHERE project_id=? AND status IN ('completed','completed_invalid') ORDER BY created_at,rowid",(pid,));matches=[]
    for index,row in enumerate(rows):
        if row['cost'] is not None or db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?',(row['id'],)):continue
        meta=db.loads(row['manifest'],{});observation=meta.get('billing_interval_observation',{});before=observation.get('before');after=observation.get('after')
        if not before or not after:continue
        size=after.get('requests',0)-before.get('requests',0)
        if type(size) is not int or not 2<=size<=8:continue
        key=meta.get('credential_hash');base=meta.get('provider',{}).get('base_url');model=row['model']
        before={**before,'credential_hash':before.get('credential_hash',key),'base_url':before.get('base_url',base)};after={**after,'credential_hash':after.get('credential_hash',key),'base_url':after.get('base_url',base)}
        if before['credential_hash']!=key or after['credential_hash']!=key or before['base_url']!=base or after['base_url']!=base:continue
        for start in range(max(0,index-size+1),index+1):
            block=rows[start:start+size]
            if len(block)!=size or row['id'] not in {r['id'] for r in block}:continue
            metas=[db.loads(x['manifest'],{}) for x in block]
            if any(m.get('credential_hash')!=key or m.get('provider',{}).get('base_url')!=base or x['model']!=model or db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?',(x['id'],)) for x,m in zip(block,metas)):continue
            usage=[db.loads(x['usage'],{}) for x in block]
            if any(type(u.get('input_tokens')) is not int or type(u.get('output_tokens')) is not int for u in usage):continue
            cached=sum(u.get('raw',{}).get('prompt_cache_hit_tokens',0) or u.get('raw',{}).get('prompt_tokens_details',{}).get('cached_tokens',0) or 0 for u in usage)
            total={'input_tokens':sum(u['input_tokens'] for u in usage),'output_tokens':sum(u['output_tokens'] for u in usage),'raw':{'prompt_cache_hit_tokens':cached}}
            proof=reconcile(before,after,total,size)
            if not proof:continue
            proof.update(run_ids=[x['id'] for x in block],per_request_costs_unknown=True,includes_prior_single_cost_evidence=True)
            existing=db.one('SELECT * FROM generation_billing_groups WHERE project_id=? AND base_url=? AND credential_hash=? AND model=?',(pid,base,key,model))
            gid=digest([pid,proof])
            if existing:
                prior=db.loads(existing['evidence'],{})
                if existing['currency']!=proof['currency'] or prior.get('method') not in ('exact_model_interval_matching_request_count_and_token_deltas','disjoint_exact_intervals_sum'):continue
                intervals=prior.get('intervals',[prior]);combined={'method':'disjoint_exact_intervals_sum','intervals':[*intervals,proof],'run_ids':[*prior.get('run_ids',[]),*proof['run_ids']],'per_request_costs_unknown':True};gid=existing['id']
                db.execute('UPDATE generation_billing_groups SET amount=?,evidence=?,updated_at=? WHERE id=?',(float(Decimal(str(existing['amount']))+Decimal(str(proof['amount']))),db.dumps(combined),db.now(),gid))
            else:db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,pid,base,key,model,proof['amount'],proof['currency'],row['id'],db.dumps(proof),db.now()))
            for x in block:db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(x['id'],gid))
            matches.append({'group_id':gid,'requests':size,'amount':proof['amount'],'prior_costs_retained_as_evidence_not_double_counted':True});break
    return {'matched_overlapping_intervals':matches,'unknown_per_call_costs_not_invented':True}

@db.atomic
def audit_preconnect_failure(pid,run_id,baseline_archive):
    """Narrow correction of the old adapter's overbroad unknown classification.

    Require the exact old trusted error format, no output/usage, single-attempt
    agent transport and unchanged installed runtime against its release archive.
    Read/write/protocol failures are never reclassified by this operation.
    """
    import hashlib,json,zipfile
    from pathlib import Path
    from ..config import ROOT
    row=db.one('SELECT * FROM generation_runs WHERE id=? AND project_id=?',(run_id,pid))
    if not row:return {'reclassified':False,'reason':'not_found'}
    meta=db.loads(row['manifest'],{});allowed={'模型请求已发出，远端状态未知：ConnectError':'ConnectError','模型请求已发出，远端状态未知：ConnectTimeout':'ConnectTimeout'}
    if row['status']!='outcome_unknown' or row['error'] not in allowed or not meta.get('agent_transport') or row['remote_id'] or meta.get('output_blob') or db.loads(row['usage'],{}) or meta.get('provider',{}).get('max_attempts')!=1:return {'reclassified':False,'reason':'not_proven_preconnect'}
    archive=Path(baseline_archive)
    if not archive.is_file():return {'reclassified':False,'reason':'original_runtime_baseline_unavailable'}
    files=['.runtime/python-deps/httpx/_exceptions.py','.runtime/python-deps/httpx/_transports/default.py','.runtime/python-deps/httpcore/_sync/connection.py','.runtime/python-deps/httpcore/_backends/sync.py']
    observed={}
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('便携包清单.json').decode('utf-8-sig'))['files']
        for relative in files:
            path=ROOT/relative;actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if manifest.get(relative)!=actual:return {'reclassified':False,'reason':'runtime_different_from_original_baseline'}
            observed[relative]=actual
    proof={'method':'source_audited_pre_http_connection_failure','original_status':row['status'],'original_error':row['error'],'exception':allowed[row['error']],'http_request_sent':False,'unchanged_runtime_sha256':observed,'contract_source':'https://www.python-httpx.org/exceptions/','basis':'Connection establishment precedes HTTP request handling in the verified installed runtime; read/write failures excluded.','at':db.now(),'bill_amount_not_invented':True}
    meta['transport_failure_audit']=proof
    db.execute("UPDATE generation_runs SET status='failed_before_send',manifest=? WHERE id=?",(db.dumps(meta),run_id))
    step=db.one('SELECT * FROM writing_steps WHERE task_id=?',(row['task_id'],))
    if step and step['state']=='outcome_unknown':
        db.execute("UPDATE writing_steps SET state='failed',error_json=? WHERE id=?",(db.dumps({'message':'经传输库源代码与原运行版本核对，连接建立失败，未发送模型HTTP请求','status':'failed','source_run_id':run_id}),step['id']))
        db.execute("UPDATE tasks SET status='failed',error=? WHERE id=?",('连接建立前失败；原误分类保留在传输审计记录中',row['task_id']))
        db.execute("UPDATE writing_sessions SET status='failed' WHERE id=? AND status='outcome_unknown'",(step['session_id'],))
    db.audit(pid,'writing_transport_failure_reclassified',{'generation_run_id':run_id,**proof})
    return {'reclassified':True,'run_id':run_id,'classification':'failed_before_send','proof':proof}

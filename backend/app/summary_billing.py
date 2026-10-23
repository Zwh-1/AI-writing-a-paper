"""Reconcile a closed set of dedicated-key requests against relay billing totals.

Keep per-request unknown costs intact. Aggregate bills replace reservations only
when key lifetime, request count, model, project, currency and token totals match.
"""
import hashlib,math,os
from datetime import datetime
from . import db,security,provider

class BudgetExceeded(ValueError):pass

def key_hash(key):return hashlib.sha256(key.encode()).hexdigest()

def budget(pid,reservation=0):
    project=security.require_project(pid)
    row=db.one("SELECT COALESCE(SUM(budget_charge),0) used,COUNT(*) calls FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))
    unknown=db.one("SELECT COUNT(*) n,COALESCE(SUM(g.reserved),0) amount FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.project_id=? AND g.cost IS NULL AND m.run_id IS NULL AND g.status NOT IN ('rejected','failed_before_send')",(pid,))
    groups=db.one('SELECT COALESCE(SUM(amount),0) amount,COUNT(*) n FROM generation_billing_groups WHERE project_id=?',(pid,))
    pending=db.one("SELECT COUNT(*) n,COALESCE(SUM(g.reserved),0) amount FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.project_id=? AND g.cost IS NULL AND m.run_id IS NULL AND g.status IN ('completed','completed_invalid')",(pid,))
    unresolved=db.one("SELECT COUNT(*) n,COALESCE(SUM(reserved),0) amount FROM generation_runs WHERE project_id=? AND status IN ('sent','pending','outcome_unknown')",(pid,))
    estimated=db.one("SELECT COALESCE(SUM(g.cost),0) amount FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.project_id=? AND m.run_id IS NULL AND g.status NOT IN ('rejected','failed_before_send')",(pid,))
    return {'limit':project['budget'],'used':row['used'],'available':max(0,project['budget']-row['used']),'unknown_calls':unknown['n'],'unknown_reserved':unknown['amount'],'reconciled_amount':groups['amount'],'reconciled_groups':groups['n'],'pending_bill_calls':pending['n'],'pending_bill_reserved':pending['amount'],'unresolved_calls':unresolved['n'],'unresolved_reserved':unresolved['amount'],'estimated_cost':estimated['amount'],'reservation':reservation,'can_call':row['used']+reservation<=project['budget']}


def request_reservation(cfg,system,user):
    from .source_payload import token_upper_bound
    ceiling=float(cfg.get('max_request_cost',2))
    prices=[cfg.get('input_price'),cfg.get('output_price')]
    if not all(finite(v) for v in prices):
        return ceiling,{'method':'unknown_price_request_ceiling','currency':cfg.get('currency'),'prices_known':False}
    upper=token_upper_bound(system,user);output=int(cfg.get('max_output_tokens',8192))
    amount=(upper*prices[0]+output*prices[1])/1_000_000
    if amount>ceiling:raise BudgetExceeded('本次请求的保守费用上界超过单次费用上限；请缩小请求或调整明确的单次上限')
    return amount,{'method':'utf8_bytes_upper_bound','input_token_upper_bound':upper,'output_token_limit':output,'input_price':prices[0],'output_price':prices[1],'currency':cfg.get('currency'),'prices_known':True,'cost_basis':'configured_price_estimate'}


def job_charge(job_id,route=None):
    where="g.project_id=j.project_id AND json_extract(g.manifest,'$.rule_job_id')=? AND g.status NOT IN ('rejected','failed_before_send')"
    args=[job_id]
    if route:where+=" AND json_extract(g.manifest,'$.provider_route')=?";args.append(route)
    ungrouped=db.one("SELECT COALESCE(SUM(COALESCE(g.cost,g.reserved)),0) amount FROM generation_runs g JOIN rule_jobs j ON j.id=json_extract(g.manifest,'$.rule_job_id') LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE "+where+' AND m.run_id IS NULL',args)['amount']
    # A group can be anchored in a different, older task. Charging only the
    # anchor would erase this job's spending after reconciliation. With no per
    # request prices, attribute the whole touched group as a conservative bound.
    groups=db.one("SELECT COALESCE(SUM(amount),0) amount FROM generation_billing_groups WHERE id IN (SELECT DISTINCT m.group_id FROM generation_runs g JOIN rule_jobs j ON j.id=json_extract(g.manifest,'$.rule_job_id') JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE "+where+')',args)['amount']
    return ungrouped+groups

def finite(value):return not isinstance(value,bool) and isinstance(value,(float,int)) and math.isfinite(value) and value>=0

def reconcile(pid,profile='material-summary'):
    from . import material_summary as summary
    if profile=='gpt-fallback':
        from . import model_routing
        c,key=model_routing.snapshot();scope=provider.request_scope(c,key)
    elif profile=='material-summary':scope=summary.scope()
    else:raise ValueError('账单接口类型无效')
    with scope:
        cfg=provider.config()
        identity=key_hash(provider._REQUEST.get()['key']);model=cfg['model'];base=cfg['base_url'].rstrip('/').removesuffix('/v1')
        try:
            with provider.client({**cfg,'timeout_seconds':20}) as client:
                response=client.get(provider.usage_endpoint(cfg['base_url']),headers=provider.headers('openai'))
            if response.status_code!=200:raise ValueError('余额接口返回 HTTP '+str(response.status_code))
            raw=response.json();normalized=provider.normalize_account_usage(raw)
            payload=raw.get('data',raw) if normalized['response_scope']=='data' else raw
        except (provider.httpx.RequestError,ValueError,provider.ProviderError) as error:
            return {'reconciled':False,'reason':'账单查询暂不可用：'+security.redact(error),'budget':budget(pid,cfg['max_request_cost'])}
        account={**normalized,'fetched_at':db.now()};db.set_setting('gpt_fallback_account_usage' if profile=='gpt-fallback' else 'material_summary_account_usage',account)
        if normalized['unit_source']!='response' or normalized['unit'].upper()!=str(cfg['currency']).upper():return {'reconciled':False,'reason':'服务商未明确返回相同币种，保留未知费用预留','account':account,'budget':budget(pid,cfg['max_request_cost'])}
        stat=next((x for x in payload.get('model_stats',[]) if isinstance(x,dict) and x.get('model')==model),None)
        if not stat or type(stat.get('requests')) is not int:return {'reconciled':False,'reason':'服务商缺少可核对的模型请求汇总','account':account,'budget':budget(pid,cfg['max_request_cost'])}
        amounts=[stat[k] for k in ('cost','actual_cost','account_cost') if k in stat]
        if not amounts or not all(finite(x) for x in amounts):return {'reconciled':False,'reason':'服务商费用字段无效，保留预留','account':account,'budget':budget(pid,cfg['max_request_cost'])}
        # Older runs can be attributed only if this DPAPI key predates every run.
        credential=security.DATA/(profile+'.dpapi');key_env='PAPER_GPT_API_KEY' if profile=='gpt-fallback' else 'PAPER_SUMMARY_API_KEY';legacy_after=credential.stat().st_mtime if credential.exists() and not os.environ.get(key_env) else None
        candidates=[]
        for run in db.rows('SELECT g.*,t.kind FROM generation_runs g JOIN tasks t ON t.id=g.task_id WHERE g.model=? ORDER BY g.created_at',(model,)):
            manifest=db.loads(run['manifest'],{});snap=manifest.get('provider',{})
            if snap.get('base_url','').rstrip('/').removesuffix('/v1')!=base:continue
            fingerprint=manifest.get('credential_hash')
            if fingerprint and fingerprint!=identity:continue
            if not fingerprint:
                try:created=datetime.fromisoformat(run['created_at']).timestamp()
                except ValueError:continue
                if legacy_after is None or created<legacy_after:continue
            candidates.append(run)
        executed=[x for x in candidates if x['status'] in ('completed','completed_invalid') or (x['status']=='rejected' and '输出被截断' in (x['error'] or ''))]
        # Unresolved calls stay outside this matched group and keep reservations.
        if len(executed)!=stat['requests'] or not executed or any(x['project_id']!=pid for x in executed):return {'reconciled':False,'reason':'服务商账单与本机请求范围不完全匹配，保留未知费用预留','account':account,'budget':budget(pid,cfg['max_request_cost'])}
        complete_usage=[db.loads(x['usage'],{}) for x in executed]
        token_accounting={}
        for local_name,remote_name in [('input_tokens','input_tokens'),('output_tokens','output_tokens')]:
            values=[u.get(local_name) for u in complete_usage]
            known=sum(v for v in values if type(v) is int and v>=0)
            remote=stat.get(remote_name)
            cached=[]
            if local_name=='input_tokens':
                for u in complete_usage:
                    raw=u.get('raw',{});details=raw.get('prompt_tokens_details',{}) if isinstance(raw,dict) else {}
                    value=details.get('cached_tokens',0) if isinstance(details,dict) else 0
                    cached.append(value if type(value) is int and value>=0 and type(u.get('input_tokens')) is int and value<=u['input_tokens'] else None)
            alternatives={known:'reported_total'}
            if cached and all(v is not None for v in cached):alternatives.setdefault(known-sum(cached),'input_minus_reported_cached')
            all_known=all(type(v) is int and v>=0 for v in values)
            if type(remote) is not int or remote<0 or (all_known and remote not in alternatives) or (not all_known and remote<min(alternatives)):return {'reconciled':False,'reason':'账单 Token 数与本机记录不符，保留预留','account':account,'budget':budget(pid,cfg['max_request_cost'])}
            token_accounting[local_name]={'local_reported_total':known,'remote_total':remote,'method':alternatives.get(remote,'partial_local_usage_lower_bound'),'reported_cached_tokens':sum(cached) if cached and all(v is not None for v in cached) else None}
        # Use the largest reported amount, avoiding an assumption about discounts.
        amount=max([*amounts,sum(x['cost'] for x in executed if finite(x['cost']))]);ids=[x['id'] for x in executed];anchor=ids[0]
        evidence={'model_stats':{k:stat[k] for k in ('model','requests','input_tokens','output_tokens','cost','actual_cost','account_cost') if k in stat},'method':'matched_dedicated_key_model_total_conservative_max','token_accounting':token_accounting,'run_ids':ids,'key_lifetime_checked':True,'account_fetched_at':account['fetched_at']}
        with db.transaction() as conn:
            if any(conn.execute('SELECT status FROM generation_runs WHERE id=?',(x,)).fetchone()[0] in ('pending','sent','outcome_unknown') for x in ids):raise ValueError('账单核对期间请求状态已变化')
            conflict=conn.execute('SELECT g.id FROM generation_billing_group_runs m JOIN generation_billing_groups g ON g.id=m.group_id WHERE m.run_id IN ('+','.join('?' for _ in ids)+') AND (g.credential_hash<>? OR g.project_id<>?) LIMIT 1',(*ids,identity,pid)).fetchone()
            if conflict:raise ValueError('请求已有不同的账单归属，拒绝覆盖')
            old=conn.execute('SELECT id FROM generation_billing_groups WHERE project_id=? AND base_url=? AND credential_hash=? AND model=?',(pid,base,identity,model)).fetchone();gid=old[0] if old else db.uid()
            if old:
                prior={r[0] for r in conn.execute('SELECT run_id FROM generation_billing_group_runs WHERE group_id=?',(gid,)).fetchall()}
                if not prior.issubset(set(ids)):
                    return {'reconciled':False,'reason':'账单快照已过期，现有核对结果已覆盖更多请求，请稍后重查','account':account,'budget':budget(pid,cfg['max_request_cost'])}
                amount=max(amount,conn.execute('SELECT amount FROM generation_billing_groups WHERE id=?',(gid,)).fetchone()[0])
            # Historical truncation consumed tokens; it must not be treated as a rejection before execution.
            for run in executed:
                if run['status']=='rejected':conn.execute("UPDATE generation_runs SET status='completed_invalid' WHERE id=?",(run['id'],))
            conn.execute('INSERT OR REPLACE INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,pid,base,identity,model,amount,normalized['unit'],anchor,db.dumps(evidence),db.now()))
            for rid in ids:conn.execute('INSERT OR REPLACE INTO generation_billing_group_runs VALUES(?,?)',(rid,gid))
            db.audit(pid,'summary_billing_reconciled',{'group_id':gid,'requests':len(ids),'amount':amount,'currency':normalized['unit'],'method':evidence['method']})
        return {'reconciled':True,'requests':len(ids),'amount':amount,'account':account,'budget':budget(pid,cfg['max_request_cost'])}

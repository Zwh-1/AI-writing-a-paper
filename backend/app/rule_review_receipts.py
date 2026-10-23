"""Local-only review usage receipts. Never label a shared account total as a job fee."""
from collections import Counter
from . import db


def billing_baseline(pid):
    return {g['id']:{'amount':g['amount'],'currency':g['currency'],'run_ids':[r['run_id'] for r in db.rows('SELECT run_id FROM generation_billing_group_runs WHERE group_id=?',(g['id'],))]} for g in db.rows('SELECT id,amount,currency FROM generation_billing_groups WHERE project_id=?',(pid,))}


def receipt(task):
    runs=db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at',(task['id'],))
    payload=db.loads(task['payload'],{}) if isinstance(task['payload'],str) else task['payload']
    models=Counter(r['model'] for r in runs);inp=out=cached=reasoning=0;usage_complete=True;reasoning_complete=True
    for run in runs:
        usage=db.loads(run['usage'],{});usage=usage if isinstance(usage,dict) else {};raw=usage.get('raw',{}) or {};raw=raw if isinstance(raw,dict) else {}
        for name in ('input_tokens','output_tokens'):
            if type(usage.get(name)) is not int:usage_complete=False
        inp+=usage.get('input_tokens') if type(usage.get('input_tokens')) is int else 0;out+=usage.get('output_tokens') if type(usage.get('output_tokens')) is int else 0
        details=raw.get('prompt_tokens_details') or {};details=details if isinstance(details,dict) else {}
        hit=raw.get('prompt_cache_hit_tokens',0) or details.get('cached_tokens',0)
        cached+=hit if type(hit) is int and hit>=0 else 0
        completion=raw.get('completion_tokens_details') or {};completion=completion if isinstance(completion,dict) else {}
        value=completion.get('reasoning_tokens')
        if type(value) is int:reasoning+=value
        else:reasoning_complete=False
    job_id=payload.get('rule_job_id')
    job=db.one('SELECT * FROM rule_jobs WHERE id=? AND project_id=?',(job_id,task['project_id'])) if job_id else None
    confirmed=None;currency=None;fee_state='no_calls' if not runs else 'unattributed';scope='task'
    if job:
        policy=db.loads(job['policy'],{});baseline=policy.get('billing_baseline')
        if isinstance(baseline,dict):
            scope='task_chain';job_runs=db.rows("SELECT * FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.rule_job_id')=? AND status NOT IN ('rejected','failed_before_send')",(task['project_id'],job_id))
            ids={r['id'] for r in job_runs};groups=db.rows("SELECT DISTINCT b.* FROM generation_billing_groups b JOIN generation_billing_group_runs m ON m.group_id=b.id JOIN generation_runs g ON g.id=m.run_id WHERE g.project_id=? AND json_extract(g.manifest,'$.rule_job_id')=?",(task['project_id'],job_id))
            proof=policy.get('review_fee_receipt',{})
            if set(proof.get('run_ids',[]))==ids and proof.get('verified'):
                confirmed=proof['amount'];currency=proof['currency'];fee_state='reconciled'
            covered=set();amount=0;units=set();valid=True
            for group in groups:
                now={r['run_id'] for r in db.rows('SELECT run_id FROM generation_billing_group_runs WHERE group_id=?',(group['id'],))}
                prior=baseline.get(group['id'],{'amount':0,'run_ids':[],'currency':group['currency']});before=set(prior['run_ids']);delta=now-before
                if not before.issubset(now) or not delta.issubset(ids) or prior['currency']!=group['currency'] or group['amount']<prior['amount']:valid=False;break
                covered|=delta;amount+=group['amount']-prior['amount'];units.add(group['currency'])
            if confirmed is None and valid and covered==ids and len(units)<=1:
                confirmed=round(amount,10);currency=next(iter(units),policy.get('currency'));fee_state='reconciled'
    if not runs:confirmed=0;fee_state='no_calls';scope='task'
    evidence=db.setting('rule_review_task_receipt:'+task['id'],{})
    executable={r['id'] for r in runs if r['status'] not in ('rejected','failed_before_send')}
    if runs and evidence.get('verified') and evidence.get('project_id')==task['project_id'] and set(evidence.get('run_ids',[]))==executable:
        confirmed=evidence['amount'];currency=evidence['currency'];fee_state='reconciled';scope='task'
    audit=db.setting('rule_review_integrity:'+task['project_id'],{})
    return {'calls':len(runs),'models':[{'model':m,'calls':n} for m,n in sorted(models.items())],'completed_calls':sum(r['status'] in ('completed','completed_invalid') for r in runs),'rejected_calls':sum(r['status'] in ('rejected','failed_before_send') for r in runs),'input_tokens':inp,'output_tokens':out,'cached_input_tokens':cached,'usage_complete':usage_complete,'reasoning_tokens':reasoning if reasoning_complete else None,'fee_state':fee_state,'confirmed_amount':confirmed,'currency':currency,'fee_scope':scope,'integrity_findings':sum(f.get('task_id')==task['id'] for f in audit.get('findings',[])),'note':'历史复用模型不计入本次调用；账户归并总额不直接当成本次费用'}


def freeze_receipt(task):
    value=receipt(task)
    if value['fee_state']!='reconciled' or value['fee_scope']!='task_chain':return
    payload=db.loads(task['payload'],{}) if isinstance(task['payload'],str) else task['payload'];job_id=payload.get('rule_job_id')
    row=db.one('SELECT * FROM rule_jobs WHERE id=? AND project_id=?',(job_id,task['project_id']))
    if not row:return
    ids=[r['id'] for r in db.rows("SELECT id FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.rule_job_id')=? AND status NOT IN ('rejected','failed_before_send')",(task['project_id'],job_id))]
    policy=db.loads(row['policy'],{});policy['review_fee_receipt']={'verified':True,'amount':value['confirmed_amount'],'currency':value['currency'],'run_ids':ids,'captured_at':db.now()}
    db.execute('UPDATE rule_jobs SET policy=? WHERE id=?',(db.dumps(policy),job_id))


def backfill_closed_receipts(pid):
    """Use saved successful billing audits and closed request sets, never balance deltas."""
    import math
    from datetime import datetime
    from decimal import Decimal
    groups={}
    for group in db.rows('SELECT * FROM generation_billing_groups WHERE project_id=?',(pid,)):
        members=db.rows("SELECT g.id,g.created_at FROM generation_runs g JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE m.group_id=? AND g.status IN ('completed','completed_invalid')",(group['id'],))
        prefixes=[]
        for event in db.rows("SELECT * FROM audit WHERE project_id=? AND action='summary_billing_reconciled' ORDER BY id",(pid,)):
            data=db.loads(event['detail'],{})
            if data.get('group_id')!=group['id'] or data.get('method')!='matched_dedicated_key_model_total_conservative_max' or type(data.get('requests')) is not int or type(data.get('amount')) not in (int,float) or not math.isfinite(data['amount']) or data['amount']<0 or data.get('currency')!=group['currency']:continue
            try:ids={r['id'] for r in members if datetime.fromisoformat(r['created_at'])<=datetime.fromisoformat(event['created_at'])}
            except (TypeError,ValueError):continue
            if len(ids)==data['requests']:prefixes.append({'ids':ids,'amount':Decimal(str(data['amount'])),'event_id':event['id']})
        groups[group['id']]={'currency':group['currency'],'prefixes':prefixes}
    restored=[]
    for task in db.rows("SELECT * FROM tasks WHERE project_id=? AND kind='rules_ai'",(pid,)):
        runs=db.rows("SELECT g.id,g.status,m.group_id FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.task_id=? AND g.status NOT IN ('rejected','failed_before_send')",(task['id'],))
        if not runs or any(r['status'] not in ('completed','completed_invalid') or not r['group_id'] for r in runs):continue
        total=Decimal(0);units=set();proof=[];valid=True
        for gid in {r['group_id'] for r in runs}:
            own={r['id'] for r in runs if r['group_id']==gid};group=groups.get(gid)
            if not group:valid=False;break
            match=None
            for before in reversed(group['prefixes']):
                if before['ids']&own:continue
                for after in group['prefixes']:
                    if after['event_id']>before['event_id'] and after['ids']-before['ids']==own and before['ids'].issubset(after['ids']) and after['amount']>=before['amount']:
                        match=(before,after);break
                if match:break
            if not match:valid=False;break
            a,b=match;total+=b['amount']-a['amount'];units.add(group['currency']);proof.append({'group_id':gid,'before_audit_id':a['event_id'],'after_audit_id':b['event_id'],'requests':len(own)})
        if valid and len(units)==1:
            data={'verified':True,'project_id':pid,'run_ids':[r['id'] for r in runs],'amount':float(total),'currency':next(iter(units)),'method':'closed_request_sets_between_saved_matched_billing_audits','proof':proof}
            db.set_setting('rule_review_task_receipt:'+task['id'],data);restored.append({'task_id':task['id'],'amount':data['amount'],'currency':data['currency']})
    return restored

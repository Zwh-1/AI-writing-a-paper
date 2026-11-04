"""Actual provider evidence and conservative scope budgets use one projection."""
from copy import deepcopy

import httpx
import pytest

from test_workbench import client, project
from app import budget_usage, db, expense_evidence, provider, provider_identity, rule_review_receipts, security, summary_billing, tasks
from app.writing_agent import budget as agent_budget


def run(pid,task,manifest,*,cost=None,reserved=2.,model='requested-model',protocol='openai',status='completed'):
    identity=db.uid()
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (identity,task['id'],pid,status,model,protocol,db.dumps(manifest),
                db.dumps({'input_tokens':100,'output_tokens':10}),cost,reserved,db.now()))
    return identity


def requested():
    return {'base_url':'https://requested.example/v1','model':'requested-model','protocol':'openai',
            'currency':'USD','input_price':1.,'output_price':2.}


def actual():
    return {'base_url':'https://actual.example/v1','model':'actual-model','protocol':'responses',
            'currency':'USD','input_price':0.2,'output_price':0.4}


def manifest():
    return {'provider':requested(),'credential_hash':'requested-account',
            'pool_used':'pool','actual_provider':actual(),
            'provider_snapshot':{key:actual()[key] for key in ('base_url','model','protocol')},
            'actual_provider_credential_hash':'actual-account'}


def test_actual_identity_never_rewrites_requested_evidence_or_inherits_missing_fields():
    value=manifest();row={'model':'requested-model','protocol':'openai','manifest':value};original=deepcopy(row)
    projection=provider_identity.effective(row)
    assert projection['requested']['model']=='requested-model'
    assert projection['effective']['model']=='actual-model'
    assert projection['effective']['credential_hash']=='actual-account' and projection['actual_known']
    assert provider_identity.config_for(row)['input_price']==0.2
    assert row==original
    value.pop('actual_provider');value['provider_snapshot']={'model':'actual-model'}
    unknown=provider_identity.effective(row)
    assert unknown['effective']['model']=='actual-model'
    assert all(unknown['effective'][name] is None for name in ('base_url','protocol','currency'))
    assert not unknown['actual_known']
    assert provider_identity.config_for(row).get('input_price') is None


def test_old_pool_without_actual_snapshot_is_unknown_and_nonpool_legacy_stays_readable():
    row={'model':'requested-model','protocol':'openai','manifest':{'provider':requested(),'credential_hash':'requested-account','pool_used':'pool'}}
    assert provider_identity.effective(row)['source']=='unknown_pool'
    assert all(value is None for value in provider_identity.effective(row)['effective'].values())
    row['manifest'].pop('pool_used')
    assert provider_identity.effective(row)['source']=='legacy_requested'
    assert provider_identity.effective(row)['effective']['model']=='requested-model'


def test_scopes_charge_a_touched_group_once_even_when_anchored_in_another_task(client):
    pid=project(client);older=tasks.enqueue(pid,'provider_probe',{});current=tasks.enqueue(pid,'provider_probe',{})
    one=run(pid,older,{'writing_session_id':'session','knowledge_budget_scope_id':'scope'},cost=0.1)
    two=run(pid,current,{'writing_session_id':'session','knowledge_budget_scope_id':'scope'},reserved=3.)
    direct=run(pid,current,{'writing_session_id':'session'},cost=0.2)
    excluded=run(pid,current,{},cost=8.,status='rejected')
    group=db.uid()
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',
        (group,pid,'https://actual.example','account','model',0.3,'USD',one,'{}',db.now()))
    for identity in (one,two):db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(identity,group))
    assert budget_usage.charge(pid)==pytest.approx(0.5)
    assert budget_usage.charge(pid,task_id=current['id'])==pytest.approx(0.5)
    assert budget_usage.charge(pid,manifest_scope={'writing_session_id':'session'})==pytest.approx(0.5)
    assert budget_usage.charge_tasks(pid,[current['id']],{'knowledge_budget_scope_id':'scope'})==pytest.approx(0.5)
    assert agent_budget.scope_charge('writing_session_id','session')==pytest.approx(0.5)
    assert summary_billing.budget(pid)['used']==pytest.approx(0.5)
    assert budget_usage.charge(pid,task_id=current['id'],exclude_run_ids=[two])==pytest.approx(0.2)
    assert budget_usage.charge(pid,exclude_run_ids=[one])==pytest.approx(0.5)
    assert budget_usage.charge(pid,exclude_run_ids=[one,two,direct,excluded])==0


def test_receipt_and_expense_ledger_show_actual_destination_without_rewriting_the_run(client):
    pid=project(client);task=tasks.enqueue(pid,'rules_ai',{});value=manifest()
    identity=run(pid,task,value)
    receipt=rule_review_receipts.receipt(task)
    assert receipt['models']==[{'model':'actual-model','calls':1}]
    ledger=expense_evidence.ledger(pid)
    item=next(item for item in ledger['runs'] if item['id']==identity)
    assert item['model']=='actual-model' and item['base_url']=='https://actual.example/v1'
    assert item['provider_identity']['requested']['model']=='requested-model'
    saved=db.one('SELECT model,protocol,manifest FROM generation_runs WHERE id=?',(identity,))
    assert saved['model']=='requested-model' and saved['protocol']=='openai'
    assert db.loads(saved['manifest'])==value


def test_summary_reconciliation_matches_actual_account_model_and_protocol(client,monkeypatch):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    cfg={**provider.DEFAULT,**actual(),'model':'deepseek-actual','protocol':'openai','max_request_cost':2.}
    security.save_key('fake-actual-account')
    db.set_setting('provider',cfg)
    value=manifest();value['actual_provider']=cfg
    value['provider_snapshot']={key:cfg[key] for key in ('base_url','model','protocol')}
    value['actual_provider_credential_hash']=summary_billing.key_hash('fake-actual-account')
    identity=run(pid,task,value)
    response={'remaining':20.,'unit':'USD','model_stats':[{'model':cfg['model'],'requests':1,
              'input_tokens':100,'output_tokens':10,'cost':0.01}]}
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
    result=summary_billing.reconcile(pid,profile='primary')
    assert result['reconciled'] and result['requests']==1
    assert db.one('SELECT run_id FROM generation_billing_group_runs')['run_id']==identity
    saved=db.one('SELECT model,manifest FROM generation_runs WHERE id=?',(identity,))
    assert saved['model']=='requested-model' and db.loads(saved['manifest'])==value


def test_strict_actual_attempt_preflight_excludes_only_its_current_hold(client,monkeypatch):
    from test_expense_evidence import configured, import_tariff
    pid=project(client);cfg=configured(monkeypatch,pid)
    import_tariff(client,pid,cfg)
    task=tasks.enqueue(pid,'expense_test',{})
    resolved=expense_evidence.resolve_config(pid,cfg,'expense-fixture-secret')
    current=run(pid,task,{'provider':resolved,'credential_hash':summary_billing.key_hash('expense-fixture-secret')},status='sent')
    result=expense_evidence.preflight(pid,resolved,'expense-fixture-secret',task=task,
                                      reservation=0.1,exclude_run_id=current)
    assert result['estimate_only']
    other=run(pid,task,{'provider':resolved},status='outcome_unknown')
    with pytest.raises(summary_billing.BudgetExceeded,match='费用未核实'):
        expense_evidence.preflight(pid,resolved,'expense-fixture-secret',task=task,
                                  reservation=0.1,exclude_run_id=current)
    with pytest.raises(ValueError,match='pending reservation'):
        expense_evidence.preflight(pid,resolved,'expense-fixture-secret',task=task,
                                  reservation=0.1,exclude_run_id=other)


def test_strict_generation_with_real_client_hooks_does_not_reject_its_own_reservation(client,monkeypatch):
    from test_expense_evidence import configured, import_tariff
    pid=project(client);cfg=configured(monkeypatch,pid)
    cfg.update(max_output_tokens=128,context_limit=10000)
    db.set_setting('provider',cfg)
    import_tariff(client,pid,cfg)
    original=httpx.Client;sent=[]
    def handler(request):
        sent.append(request)
        return httpx.Response(200,json={'id':'actual-fixture','choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':10}})
    def connection(**kwargs):
        kwargs['transport']=httpx.MockTransport(handler)
        return original(**kwargs)
    monkeypatch.setattr(provider.httpx,'Client',connection)
    task=tasks.enqueue(pid,'expense_test',{})
    result=tasks.model_call(task,'Original system.','Original evidence.',{})
    assert len(sent)==1 and result['text']=='{"ok":true}'
    assert db.one('SELECT state FROM provider_attempts')['state']=='persisted'
    assert db.one('SELECT state FROM request_intents')['state']=='persisted'
    assert summary_billing.budget(pid)['used']==pytest.approx(0.00014)


def test_imported_receipt_matches_actual_pool_destination_and_preserves_requested_record(client):
    import json
    from test_expense_evidence import receipt, upload
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{});value=manifest()
    identity=run(pid,task,value,status='outcome_unknown')
    db.execute('UPDATE generation_runs SET remote_id=? WHERE id=?',('response-1',identity))
    document=receipt(actual())
    response=upload(client,identity,document)
    assert response.status_code==200,response.text
    saved=db.one('SELECT * FROM generation_runs WHERE id=?',(identity,))
    assert saved['status']=='manually_settled' and saved['cost']==document['amount']
    assert saved['model']=='requested-model'
    assert db.loads(saved['manifest'])['provider']==requested()

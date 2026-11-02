import json
import sqlite3
import hashlib
import httpx
import pytest
from test_workbench import client, project
from app import db, provider, tasks, expense_evidence as expense, security
from app.summary_billing import BudgetExceeded, budget


def configured(monkeypatch, pid):
    monkeypatch.setattr(expense, 'DEFAULT_POLICY', {'require_pricing_basis': True, 'block_on_unresolved': True})
    monkeypatch.setattr(provider, 'get_key', lambda: 'expense-fixture-secret')
    monkeypatch.setattr(security, 'get_key', lambda profile='': 'expense-fixture-secret')
    cfg = {**provider.DEFAULT, 'base_url': 'https://billing.example/v1', 'model': 'test-model', 'input_price': 1., 'output_price': 4., 'currency': 'USD', 'max_attempts': 1}
    db.set_setting('provider', cfg)
    return cfg


def tariff(cfg):
    return {k: cfg[k] for k in ('base_url', 'model', 'currency', 'input_price', 'output_price')} | {
        'source_url': 'https://billing.example/pricing', 'captured_at': '2026-01-01T00:00:00+00:00', 'unit': 'per_million_tokens'}


def import_tariff(client, pid, cfg):
    response = client.post(f'/api/v1/projects/{pid}/expenses/tariffs/primary', files={'file': ('tariff.json', json.dumps(tariff(cfg)).encode(), 'application/json')})
    assert response.status_code == 200, response.text
    return response.json()


def seed(pid, cfg, status='completed', cost=None, remote_id='response-1', extra=None):
    task = tasks.enqueue(pid, 'expense_test', {})
    rid = db.uid()
    cfg = expense.resolve_config(pid, cfg, 'expense-fixture-secret')
    meta = {'provider': cfg, 'credential_hash': hashlib.sha256(b'expense-fixture-secret').hexdigest(), **(extra or {})}
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,remote_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
               (rid, task['id'], pid, status, cfg['model'], 'openai', db.dumps(meta), db.dumps({'input_tokens': 100, 'output_tokens': 50}), cost, 2, remote_id, db.now()))
    return rid


def receipt(cfg):
    return {k: cfg[k] for k in ('base_url', 'model', 'currency')} | {'receipt_id': 'bill-001', 'request_id': 'response-1',
        'amount': 0.03125, 'billed_at': '2026-01-01T00:00:00+00:00', 'source_url': 'https://billing.example/invoices'}


def upload(client, rid, doc):
    return client.post('/api/v1/usage/' + rid + '/receipt', files={'file': ('bill.json', json.dumps(doc).encode(), 'application/json')})


def test_strict_default_blocks_unknown_prices_before_network(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid)
    monkeypatch.setattr(provider, 'generate', lambda *args: pytest.fail('No paid request may be sent'))
    task = tasks.enqueue(pid, 'expense_test', {})
    with pytest.raises(BudgetExceeded, match='计价依据'):
        tasks._model_call(task, 'system', 'input', {})
    assert not db.rows('SELECT * FROM generation_runs')


def test_imported_tariff_scope_expiry_currency_and_rate_match(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    resolved = expense.resolve_config(pid, cfg, 'expense-fixture-secret')
    assert expense.preflight(pid, resolved, 'expense-fixture-secret')['estimate_only']
    for changed in ({'model': 'other-model'}, {'base_url': 'https://other.example'}, {'input_price': 9}, {'currency': 'CNY'}):
        with pytest.raises(BudgetExceeded):
            expense.preflight(pid, expense.resolve_config(pid, {**cfg, **changed}, 'expense-fixture-secret'), 'expense-fixture-secret')
    assert not expense.source_for(resolved, 'different-key-hash')
    assert not expense.source_for({**resolved, 'input_price': 5}, resolved['expense_pricing_basis']['credential_hash'])
    with pytest.raises(BudgetExceeded, match='图像输入'):
        expense.preflight(pid, resolved, 'expense-fixture-secret', images=True)


def test_strict_blocks_further_calls_after_unknown_or_unpriced_response(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    rid = seed(pid, cfg, status='outcome_unknown')
    with pytest.raises(BudgetExceeded, match='未核实'):
        expense.preflight(pid, expense.resolve_config(pid, cfg, 'expense-fixture-secret'), 'expense-fixture-secret')
    db.execute("UPDATE generation_runs SET status='completed' WHERE id=?", (rid,))
    with pytest.raises(BudgetExceeded, match='未核实'):
        expense.preflight(pid, expense.resolve_config(pid, cfg, 'expense-fixture-secret'), 'expense-fixture-secret')


def test_unbacked_legacy_numeric_amount_does_not_unlock_new_calls(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    seed(pid,cfg,cost=9.)
    with pytest.raises(BudgetExceeded,match='已登记金额'):
        expense.preflight(pid,expense.resolve_config(pid,cfg,'expense-fixture-secret'),'expense-fixture-secret')


def test_new_call_persists_ids_usage_and_price_basis_without_context_leak(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    seen = []
    def handle(request):
        seen.append(request.headers['X-Client-Request-Id'])
        return httpx.Response(200, headers={'X-Request-Id': 'provider-123'}, json={'id': 'response-1', 'model': cfg['model'], 'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handle)))
    task = tasks.enqueue(pid, 'expense_test', {})
    result = tasks._model_call(task, 'system', 'input', {})
    row = db.one('SELECT * FROM generation_runs WHERE id=?', (result['run_id'],))
    meta = db.loads(row['manifest'])
    assert seen == [row['id']] == [meta['client_request_id']]
    assert meta['response_evidence']['provider_request_id'] == 'provider-123'
    assert len(meta['response_evidence']['response_sha256']) == 64
    assert meta['expense_pricing_basis']['estimate_only']
    assert provider._CLIENT_REQUEST_ID.get() is None and provider._REQUEST.get() is None
    ledger = expense.ledger(pid)
    assert ledger['runs'][0]['kind'] == 'supported_estimate'
    assert ledger['by_currency']['USD']['supported_estimate'] == .0003
    assert not ledger['all_actual_costs_verified']


def test_generic_failure_after_send_retains_hold(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    def invalid(*args): raise TypeError('malformed successful response')
    monkeypatch.setattr(provider, 'generate', invalid)
    with pytest.raises(TypeError): tasks._model_call(tasks.enqueue(pid, 'expense_test', {}), 'sys', 'input', {})
    row = db.one('SELECT * FROM generation_runs')
    assert row['status'] == 'outcome_unknown' and budget(pid)['used'] == row['reserved']


@pytest.mark.parametrize('field,value', [('request_id', 'other'), ('model', 'other'), ('base_url', 'https://other.example'), ('currency', 'CNY'), ('amount', -1), ('amount', True), ('amount', float('nan')), ('amount', float('inf')), ('billed_at', '2099-01-01T00:00:00+00:00')])
def test_receipt_cannot_settle_unmatched_or_invalid_bill(client, monkeypatch, field, value):
    pid = project(client); cfg = configured(monkeypatch, pid); rid = seed(pid, cfg)
    response = upload(client, rid, {**receipt(cfg), field: value})
    assert response.status_code == 422
    assert db.one('SELECT cost FROM generation_runs WHERE id=?', (rid,))['cost'] is None
    assert not db.rows('SELECT * FROM expense_evidence')


def test_receipt_immutable_idempotent_and_original_preserved(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); rid = seed(pid, cfg, status='outcome_unknown')
    assert client.post('/api/v1/usage/' + rid + '/settle', json={'cost': 0}).status_code == 422
    response = upload(client, rid, receipt(cfg)); assert response.status_code == 200, response.text
    eid = response.json()['evidence_id']
    assert upload(client, rid, receipt(cfg)).json()['idempotent']
    assert upload(client, rid, {**receipt(cfg), 'amount': .02}).status_code == 409
    saved = db.one('SELECT * FROM expense_evidence WHERE id=?', (eid,))
    assert db.loads(saved['content'])['prior_status'] == 'outcome_unknown'
    assert expense.ledger(pid)['runs'][0]['kind'] == 'imported_receipt'
    assert not expense.ledger(pid)['all_actual_costs_verified']
    with pytest.raises(sqlite3.IntegrityError, match='append only'):
        db.execute('UPDATE expense_evidence SET kind=? WHERE id=?', ('other', eid))
    with pytest.raises(sqlite3.IntegrityError, match='append only'):
        db.execute('DELETE FROM expense_evidence WHERE id=?', (eid,))
    assert hashlib.sha256(client.get(f'/api/v1/projects/{pid}/expenses/evidence/{eid}/document').content).hexdigest() == saved['document_sha256']
    other = project(client, '隔离项目')
    assert client.get(f'/api/v1/projects/{other}/expenses/evidence/{eid}').status_code == 404
    assert budget(pid)['used'] == .03125


def test_unknown_without_matching_remote_id_never_settled_by_local_guess(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); rid = seed(pid, cfg, status='outcome_unknown', remote_id=None)
    assert upload(client, rid, {**receipt(cfg), 'client_request_id': rid}).status_code == 422
    assert budget(pid)['used'] == 2


def test_unknown_can_match_preserved_client_request_id_only(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); rid = seed(pid, cfg, status='outcome_unknown', remote_id=None, extra={'client_request_id': 'local-preserved-request'})
    assert upload(client, rid, {**receipt(cfg), 'client_request_id': 'local-preserved-request'}).status_code == 200


def test_group_amount_counted_once_not_divided_into_requests(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); a = seed(pid, cfg); b = seed(pid, cfg)
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)', ('group-1', pid, cfg['base_url'], 'hash', cfg['model'], .5, 'USD', a, db.dumps({'method': 'conservative_max', 'actual_cost': .05, 'cost': .5}), db.now()))
    for rid in (a, b): db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)', (rid, 'group-1'))
    data = expense.ledger(pid)
    assert data['by_currency']['USD']['group_bill_upper_bound'] == .5
    assert sum(r['budget_occupation'] for r in data['runs']) == .5
    assert all(r['amount'] is None for r in data['runs'])
    assert upload(client, b, receipt(cfg)).status_code == 409


def test_bad_usage_and_unsupported_costs_never_claimed_as_supported(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); seed(pid, cfg, cost=1)
    assert expense.ledger(pid)['runs'][0]['kind'] == 'unsupported_cost'
    for value in (-2, True, '100', 1.2, float('inf')):
        assert provider.usage_cost(cfg, {'input_tokens': value, 'output_tokens': 20}) is None


def test_account_observation_retains_different_fields_and_never_releases_hold(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); seed(pid, cfg, status='outcome_unknown')
    monkeypatch.setattr(security, 'get_key', lambda profile='': 'expense-fixture-secret' if not profile else '')
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'unit': 'USD', 'remaining': 7, 'secret': 'must not be stored', 'model_stats': [{'model': 'test-model', 'requests': 1, 'cost': 1, 'actual_cost': .1, 'account_cost': 1, 'api_key': 'must not be stored'}]}))))
    result = client.post(f'/api/v1/projects/{pid}/expenses/account-observations')
    assert result.status_code == 200 and budget(pid)['used'] == 2
    stored = db.loads(db.one("SELECT content FROM expense_evidence WHERE kind='account_observation'")['content'])
    assert stored['model_stats'][0]['actual_cost'] == .1 and stored['model_stats'][0]['account_cost'] == 1
    assert 'must not be stored' not in db.dumps(stored)


def test_secret_receipts_rejected_and_export_is_complete(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); rid = seed(pid, cfg)
    assert upload(client, rid, {**receipt(cfg), 'nested': {'Authorization': 'key'}}).status_code == 422
    assert upload(client, rid, {**receipt(cfg), 'note': 'expense-fixture-secret'}).status_code == 422
    imported = import_tariff(client, pid, cfg)
    result = client.get(f'/api/v1/projects/{pid}/expenses/export')
    assert result.status_code == 200
    assert result.json()['evidence_records'][0]['id'] == imported['evidence_id']
    assert 'content' in result.json()['evidence_records'][0]


def test_http_unknown_retains_provider_request_id_for_bill_lookup(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(524, headers={'X-Request-Id': 'remote-uncertain-524'}, text='upstream timeout'))))
    with pytest.raises(provider.ProviderError):
        tasks._model_call(tasks.enqueue(pid, 'expense_test', {}), 'sys', 'input', {})
    row = db.one('SELECT * FROM generation_runs')
    assert row['status'] == 'outcome_unknown'
    assert db.loads(row['manifest'])['response_evidence']['provider_request_id'] == 'remote-uncertain-524'
    assert upload(client, row['id'], {**receipt(cfg), 'request_id': 'remote-uncertain-524'}).status_code == 200


def test_modified_tariff_document_cannot_authorize_charge(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    resolved = expense.resolve_config(pid, cfg, 'expense-fixture-secret')
    record = db.one("SELECT * FROM expense_evidence WHERE kind='tariff'")
    security.safe_object(record['document_sha256']).write_bytes(b'altered tariff')
    with pytest.raises(BudgetExceeded, match='计价依据'):
        expense.preflight(pid, resolved, 'expense-fixture-secret')


def test_project_currency_cannot_follow_global_settings_change(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    expense.preflight(pid, expense.resolve_config(pid, cfg, 'expense-fixture-secret'), 'expense-fixture-secret')
    db.set_setting('provider', {**cfg, 'currency': 'CNY'})
    assert expense.policy(pid)['currency'] == 'USD'
    with pytest.raises(BudgetExceeded, match='币种'):
        expense.preflight(pid, {**cfg, 'currency': 'CNY'}, 'expense-fixture-secret')


def test_image_route_requires_tariff_and_preserves_returned_usage_on_invalid_image(client, monkeypatch):
    from app import image_generation
    from test_image_generation import enqueue
    pid = project(client); configured(monkeypatch, pid)
    cfg={**image_generation.DEFAULT, 'base_url': 'https://image.example', 'image_price': .2}
    db.set_setting('image_generation_provider', cfg)
    monkeypatch.setattr(image_generation,'snapshot',lambda: (cfg,'expense-fixture-secret'))
    task=enqueue(client,pid)
    def paid_request(*args,**kwargs):pytest.fail('No request without pricing evidence')
    monkeypatch.setattr(image_generation,'request_image',paid_request)
    with pytest.raises(BudgetExceeded,match='计价依据'):image_generation.generate(task)
    assert not db.rows('SELECT * FROM generation_runs')
    quote={k:cfg[k] for k in ('base_url','model','currency','image_price')}|{'source_url':'https://image.example/pricing','captured_at':'2026-01-01T00:00:00+00:00','unit':'per_image'}
    uploaded=client.post(f'/api/v1/projects/{pid}/expenses/tariffs/image-generation',files={'file':('image-price.json',json.dumps(quote).encode(),'application/json')})
    assert uploaded.status_code==200,uploaded.text
    monkeypatch.setattr(image_generation,'request_image',lambda *args,**kwargs:{'id':'bad-image-response','data':[{'b64_json':'invalid'}],'usage':{'input_tokens':11,'output_tokens':22}})
    with pytest.raises(ValueError):image_generation.generate(task)
    run=db.one('SELECT * FROM generation_runs')
    assert run['status']=='completed_invalid' and run['cost']==.2
    assert db.loads(run['usage'])['input_tokens']==11 and run['remote_id']=='bad-image-response'
    assert expense.ledger(pid)['runs'][0]['kind']=='supported_estimate'
    assert provider._CLIENT_REQUEST_ID.get() is None


def test_unverifiable_closure_books_stated_basis_and_clears_the_block(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    rid = seed(pid, cfg, status='outcome_unknown', cost=None)
    db.execute("UPDATE generation_runs SET usage='{}',remote_id=NULL,reserved=0.5 WHERE id=?", (rid,))
    assert budget(pid)['unknown_calls'] == 1
    closed = expense.close_unverifiable(pid, [rid], '批量知识维护中断，服务商不提供逐请求账单，用量无法取得', 'reserved')
    assert closed['closed'] == 1 and closed['amount'] == 0.5
    run = db.one('SELECT * FROM generation_runs WHERE id=?', (rid,))
    assert run['cost'] == 0.5 and run['status'] == 'manually_settled'
    assert budget(pid)['unknown_calls'] == 0
    item = next(r for r in expense.ledger(pid)['runs'] if r['id'] == rid)
    assert item['kind'] == 'unverified_closure' and item['gaps'] == []
    assert item['basis']['amount_basis'] == 'reserved' and '服务商' in item['basis']['reason']
    assert expense.unsupported_cost_runs(pid) == []
    assert db.rows("SELECT * FROM audit WHERE project_id=? AND action='expense_unverified_closed'", (pid,))


def test_zero_basis_releases_the_reservation(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    rid = seed(pid, cfg, status='sent', cost=None)
    db.execute("UPDATE generation_runs SET usage='{}',remote_id=NULL,reserved=0.75 WHERE id=?", (rid,))
    occupied = budget(pid)['used']
    expense.close_unverifiable(pid, [rid], '断网后未收到响应，服务端无该请求记录，按未扣费处理', 'zero')
    assert db.one('SELECT cost FROM generation_runs WHERE id=?', (rid,))['cost'] == 0
    assert budget(pid)['used'] == occupied - 0.75


def test_closure_refuses_ambiguous_or_already_settled_calls(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    pending = seed(pid, cfg, status='completed', cost=0.01)
    with pytest.raises(HTTPException := __import__('fastapi').HTTPException) as settled:
        expense.close_unverifiable(pid, [pending], '已经有金额的调用不能按无法核实结转', 'reserved')
    assert settled.value.status_code == 409
    unknown = seed(pid, cfg, status='outcome_unknown', cost=None)
    with pytest.raises(__import__('fastapi').HTTPException) as short:
        expense.close_unverifiable(pid, [unknown], '不清楚', 'reserved')
    assert short.value.status_code == 422
    with pytest.raises(__import__('fastapi').HTTPException) as basis:
        expense.close_unverifiable(pid, [unknown], '设备关机导致结果未知且无服务商记录', 'guess')
    assert basis.value.status_code == 422
    expense.close_unverifiable(pid, [unknown], '设备关机导致结果未知且无服务商记录', 'reserved')
    with pytest.raises(__import__('fastapi').HTTPException) as twice:
        expense.close_unverifiable(pid, [unknown], '设备关机导致结果未知且无服务商记录', 'reserved')
    assert twice.value.status_code == 409


def test_closure_route_requires_explicit_run_ids(client, monkeypatch):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    rid = seed(pid, cfg, status='outcome_unknown', cost=None)
    body = {'run_ids': [rid], 'reason': '自动任务中断，服务商无逐请求账单可核', 'amount_basis': 'reserved'}
    response = client.post(f'/api/v1/projects/{pid}/expenses/close-unverifiable', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['closed'] == 1
    empty = client.post(f'/api/v1/projects/{pid}/expenses/close-unverifiable', json={**body, 'run_ids': []})
    assert empty.status_code == 422

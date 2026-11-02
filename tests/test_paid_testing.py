import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from test_workbench import client, project
from test_expense_evidence import configured, import_tariff, seed
from app import db, expense_evidence as expense, paid_testing as testing, provider, tasks
from app.summary_billing import BudgetExceeded


def setup_allocation(client, monkeypatch, limit=.1, history=True):
    pid = project(client); cfg = configured(monkeypatch, pid)
    import_tariff(client, pid, cfg)
    if history:
        rid = seed(pid, cfg, status='outcome_unknown')
        db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=(SELECT task_id FROM generation_runs WHERE id=?)", (rid,))
        db.execute('UPDATE projects SET budget=2 WHERE id=?', (pid,))
    else:
        rid = None
    cfg = expense.resolve_config(pid, cfg, 'expense-fixture-secret')
    grant = testing.create(pid, limit, f'用户明确授权本轮新增{limit} USD测试费用', cfg, 'expense-fixture-secret')
    return pid, cfg, grant, rid


def bound_task(pid, grant, kind='knowledge_paid_calibration', **extra):
    task = tasks.enqueue(pid, kind, {'budget_limit': grant['limit'], 'provider_profile': 'primary', **extra})
    testing.bind(pid, grant['id'], task)
    return task


def transport(monkeypatch, seen, fail=False, usage=(100, 50)):
    def handler(request):
        seen.append(request.headers['X-Client-Request-Id'])
        if fail:
            raise httpx.ReadTimeout('after send', request=request)
        return httpx.Response(200, headers={'X-Request-Id': 'upstream-' + seen[-1]}, json={
            'id': 'response-' + seen[-1], 'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}],
            'usage': {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}})
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handler)))


def test_testing_preserves_history_and_only_unlocks_bound_tasks(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    original = db.one('SELECT * FROM generation_runs WHERE id=?', (rid,)); initial_budget = db.one('SELECT budget FROM projects WHERE id=?', (pid,))
    seen = []; transport(monkeypatch, seen)
    task = bound_task(pid, grant)
    result = tasks._model_call(task, 'system', 'input', {})
    assert len(seen) == 1 and result['cost'] == .0003
    assert testing.report(pid, grant['id'])['used'] == .0003
    assert db.one('SELECT * FROM generation_runs WHERE id=?', (rid,)) == original
    assert db.one('SELECT budget FROM projects WHERE id=?', (pid,)) == initial_budget
    assert expense.policy(pid)['block_on_unresolved']
    assert expense.ledger(pid)['counts']['reservation'] == 1
    meta = db.loads(db.one('SELECT manifest FROM generation_runs WHERE id=?', (result['run_id'],))['manifest'])
    assert meta['paid_testing_allocation_id'] == grant['id']
    assert meta['expense_pricing_basis']['paid_testing']['historical_holds_retained']
    with pytest.raises(BudgetExceeded, match='未核实'):
        tasks._model_call(tasks.enqueue(pid, 'ordinary_test', {}), 'system', 'input', {})
    assert len(seen) == 1


@pytest.mark.parametrize('value', [None, 0, -1, True, float('nan'), float('inf')])
def test_no_allocation_without_valid_concrete_amount(client, monkeypatch, value):
    pid = project(client); cfg = configured(monkeypatch, pid); import_tariff(client, pid, cfg)
    cfg = expense.resolve_config(pid, cfg, 'expense-fixture-secret')
    with pytest.raises(ValueError):
        testing.create(pid, value, 'user permission', cfg, 'expense-fixture-secret')
    assert not db.rows("SELECT id FROM expense_evidence WHERE kind='paid_testing_allocation'")


def test_shared_allocation_limit_includes_other_tasks_and_reservations(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch, limit=.04)
    seen = []; transport(monkeypatch, seen, usage=(10000, 1000))
    first = bound_task(pid, grant); second = bound_task(pid, grant)
    tasks._model_call(first, 'system', 'input', {})
    assert testing.report(pid, grant['id'])['used'] == .014
    with pytest.raises(BudgetExceeded, match='授权上限'):
        tasks._model_call(second, 'system', 'input', {})
    assert len(seen) == 1


def test_new_unknown_stops_entire_allocation_without_retry(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    seen = []; transport(monkeypatch, seen, fail=True)
    first = bound_task(pid, grant); second = bound_task(pid, grant)
    with pytest.raises(provider.ProviderError) as error:
        tasks._model_call(first, 'system', 'input', {})
    assert error.value.unknown
    with pytest.raises(BudgetExceeded, match='新增请求'):
        tasks._model_call(second, 'system', 'different input', {})
    assert len(seen) == 1
    assert testing.report(pid, grant['id'])['used'] > 0
    assert expense.ledger(pid)['counts']['reservation'] == 2


def test_new_unknown_from_unbound_task_also_stops_testing(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    seed(pid, cfg, status='sent', remote_id='unrelated-new-request')
    seen = []; transport(monkeypatch, seen)
    with pytest.raises(BudgetExceeded, match='新增请求'):
        tasks._model_call(bound_task(pid, grant), 'system', 'input', {})
    assert not seen


def test_task_binding_is_immutable_and_provider_scope_cannot_change(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = bound_task(pid, grant); seen = []; transport(monkeypatch, seen)
    with provider.request_scope({**cfg, 'model': 'other'}, 'expense-fixture-secret'):
        with pytest.raises(BudgetExceeded, match='快照不一致'):
            tasks._model_call(task, 'system', 'input', {})
    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps({'provider_profile': 'primary', 'budget_limit': 999}), task['id']))
    with pytest.raises(BudgetExceeded, match='参数或归属'):
        tasks._model_call(task, 'system', 'input', {})
    assert not seen


def test_cannot_bind_arbitrary_or_forged_task(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = tasks.enqueue(pid, 'write', {'provider_profile': 'primary', 'budget_limit': .1})
    with pytest.raises(ValueError, match='知识测试'):
        testing.bind(pid, grant['id'], task)
    task = tasks.enqueue(pid, 'knowledge_paid_calibration', {'provider_profile': 'primary', 'budget_limit': .1})
    with pytest.raises(ValueError, match='完整参数'):
        testing.bind(pid, grant['id'], {**task, 'payload': '{}'})
    other = project(client, 'another')
    with pytest.raises(BudgetExceeded):
        testing.bind(other, grant['id'], task)


def test_closing_allocation_revokes_already_bound_tasks(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = bound_task(pid, grant)
    testing.close(pid, grant['id'], 'test complete')
    seen = []; transport(monkeypatch, seen)
    with pytest.raises(BudgetExceeded, match='已关闭'):
        tasks._model_call(task, 'system', 'input', {})
    assert not seen and testing.report(pid, grant['id'])['closed']


def test_group_reconciliation_keeps_full_conservative_bound(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = bound_task(pid, grant); seen = []; transport(monkeypatch, seen)
    result = tasks._model_call(task, 'system', 'input', {})
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',
               ('mixed', pid, cfg['base_url'], 'hash', cfg['model'], .2, 'USD', rid, '{}', db.now()))
    for run in (rid, result['run_id']):
        db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)', (run, 'mixed'))
    assert testing.report(pid, grant['id'])['used'] == .2
    with pytest.raises(BudgetExceeded, match='授权上限'):
        tasks._model_call(task, 'system', 'input2', {})


def test_atomic_reservations_prevent_concurrent_overspend(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch, limit=.05)
    bound = [bound_task(pid, grant) for _ in range(2)]
    # Both calls would cost .03. The first reservation remains pending while
    # the second transaction checks the same shared allowance.
    def reserve(task):
        try:
            with db.transaction():
                scope = testing.guard(task, cfg, 'expense-fixture-secret', .03)
                db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                           (db.uid(), task['id'], pid, 'pending', cfg['model'], 'openai', db.dumps({'paid_testing_allocation_id': scope['allocation_id']}), .03, db.now()))
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, bound))
    assert results.count(True) == 1 and testing.report(pid, grant['id'])['used'] == .03


def test_baseline_hash_prevents_silently_altering_historical_holds(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = bound_task(pid, grant); seen = []; transport(monkeypatch, seen)
    db.execute('UPDATE generation_runs SET reserved=1 WHERE id=?', (rid,))
    with pytest.raises(BudgetExceeded, match='历史待核请求记录'):
        tasks._model_call(task, 'system', 'input', {})
    assert not seen


def test_no_implicit_allocation_from_task_payload_or_manifest(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = tasks.enqueue(pid, 'knowledge_paid_calibration', {'provider_profile': 'primary', 'budget_limit': 1000,
                                                           'paid_testing_allocation_id': grant['id']})
    seen = []; transport(monkeypatch, seen)
    with pytest.raises(BudgetExceeded, match='未核实'):
        tasks._model_call(task, 'system', 'input', {'paid_testing_allocation_id': grant['id']})
    assert not seen


def test_further_calls_require_pricing_evidence_even_for_bound_tests(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    task = bound_task(pid, grant); seen = []; transport(monkeypatch, seen)
    document = db.one("SELECT document_sha256 FROM expense_evidence WHERE project_id=? AND kind='tariff'", (pid,))
    from app import security
    security.safe_object(document['document_sha256']).write_text('tampered', encoding='utf-8')
    with pytest.raises(BudgetExceeded, match='计价依据'):
        tasks._model_call(task, 'system', 'input', {})
    assert not seen


def test_missing_usage_keeps_reservation_and_stops_following_tests(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    def handler(request):
        return httpx.Response(200, json={'id': 'no-usage', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]})
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handler)))
    first = bound_task(pid, grant)
    result = tasks._model_call(first, 'system', 'input', {})
    assert result['cost'] is None
    second = bound_task(pid, grant)
    with pytest.raises(BudgetExceeded, match='新增请求'):
        tasks._model_call(second, 'system', 'different input', {})


def test_active_grants_cannot_be_reset_to_obtain_a_fresh_cap(client, monkeypatch):
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    with pytest.raises(BudgetExceeded, match='已有开放'):
        testing.create(pid, .1, 'another amount authorization', cfg, 'expense-fixture-secret')


@pytest.mark.parametrize('unknown', [False, True])
def test_real_runner_calibration_path_saves_results_or_unknown_without_fake_review(client, monkeypatch, tmp_path, unknown):
    from pathlib import Path
    import runpy
    from types import SimpleNamespace
    from test_workbench import file
    from app import knowledge_units
    pid, cfg, grant, rid = setup_allocation(client, monkeypatch)
    original = file(client, pid, '本研究仅测试单用户环境，并未验证并发性能。'*20)
    knowledge_units.build(pid, original['id'])
    seen = []
    def handler(request):
        seen.append(request.headers['X-Client-Request-Id'])
        if unknown:
            raise httpx.ReadTimeout('after send', request=request)
        body = json.loads(request.content)
        observation = json.loads(body['messages'][-1]['content'])['observation']
        content = {'claims': [{'statement': '研究仅测试单用户环境', 'quote': '本研究仅测试单用户环境',
                               'observation_id': observation['id'], 'conditions': ['单用户'], 'predicate': 'limits'}]}
        return httpx.Response(200, json={'id': 'calibration-reply', 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(content)}}],
                                        'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handler)))
    runner = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    args = SimpleNamespace(allocation=grant['id'], phase='calibration', file=[], max_items=None,
                           artifact=None, query=None, output=tmp_path/'runner-results')
    report = runner['run_phase'](pid, args)
    assert report['status'] == ('outcome_unknown' if unknown else 'succeeded')
    assert len(seen) == 1 and not report['human_acceptance_claimed']
    assert list(args.output.glob('calibration-*.json'))
    assert not db.rows('SELECT * FROM knowledge_review_events')
    if unknown:
        again = runner['run_phase'](pid, args)
        assert again['status'] != 'succeeded' and len(seen) == 1
        assert expense.ledger(pid)['counts']['reservation'] == 2
    else:
        assert report['allocation']['used'] == .0003


def test_semantic_phase_can_defer_maintenance_without_claiming_semantic_quality(client, monkeypatch):
    from app import knowledge_builds as builds, knowledge_compiler as compiler, knowledge_maintenance
    from test_workbench import file
    from app import knowledge_units
    pid=project(client);source=file(client,pid,'这项研究只验证了单用户场景。')
    knowledge_units.build(pid,source['id'])
    real_compile=compiler.compile_file
    monkeypatch.setattr(compiler,'compile_file',lambda task,fid,mode,progress,ns:real_compile(task,fid,'literal',progress,ns))
    monkeypatch.setattr(knowledge_maintenance,'run',lambda *a,**k:pytest.fail('deferred maintenance must not consume extraction allocation'))
    task=builds.start(pid,{'mode':'llm','namespace':'literature','file_order':'smallest_first','defer_maintenance':True,
                          'support_checks':False,'discover_topics':False,'budget_limit':1})
    result=builds.process(task,db.loads(task['payload'],{}))
    assert result['maintenance_deferred'] and result['maintenance'] is None
    assert result['complete'] and not result['semantic_quality_verified']


def test_paid_index_phase_uses_complete_scope_and_same_allocation(client, monkeypatch, tmp_path):
    from pathlib import Path
    import runpy
    from types import SimpleNamespace
    from app import graphrag_adapter
    pid,cfg,grant,rid=setup_allocation(client,monkeypatch)
    calls=[]
    def build(task,payload):
        calls.append((task,payload));return {'artifact_id':'controlled-index','scope':'full_current_scope'}
    monkeypatch.setattr(graphrag_adapter,'build',build)
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    args=SimpleNamespace(allocation=grant['id'],phase='index',file=[],max_items=None,artifact=None,query=None,output=tmp_path/'index-report')
    report=runner['run_phase'](pid,args)
    assert report['status']=='succeeded'
    task,payload=calls[0]
    assert task['kind']=='knowledge_graphrag_build' and payload['budget_scope_id']==grant['id']
    assert payload['full_index'] and payload['reports'] and payload['embeddings']
    assert paid_testing_bindings(pid,grant['id'])==[task['id']]


def paid_testing_bindings(pid,allocation):
    return [r['task_id'] for r in testing.bindings(pid,allocation)]


def test_maintenance_caught_unknown_still_stops_paid_pipeline(client,monkeypatch,tmp_path):
    from pathlib import Path
    import runpy
    from types import SimpleNamespace
    from app import knowledge_maintenance
    pid,cfg,grant,rid=setup_allocation(client,monkeypatch)
    monkeypatch.setattr(knowledge_maintenance,'prepare',lambda *a,**k:{'items':[{'id':'controlled-item','status':'queued'}]})
    def run(task,payload):
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (db.uid(),task['id'],pid,'outcome_unknown',cfg['model'],cfg['protocol'],'{}',.01,db.now()))
        return {'complete':False,'completed_item_ids':['earlier-completed-item']}
    monkeypatch.setattr(knowledge_maintenance,'run',run)
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    args=SimpleNamespace(allocation=grant['id'],phase='maintenance',file=[],max_items=1,artifact=None,query=None,output=tmp_path/'unknown-maintenance')
    report=runner['run_phase'](pid,args)
    assert report['status']=='outcome_unknown'
    assert db.one('SELECT status FROM tasks WHERE id=?',(report['task_id'],))['status']=='outcome_unknown'


def test_runner_only_retries_connection_failure_proven_before_http(client, monkeypatch, tmp_path):
    from pathlib import Path
    import runpy
    from types import SimpleNamespace
    from test_workbench import file
    from app import knowledge_units
    pid,cfg,grant,rid=setup_allocation(client,monkeypatch)
    original=file(client,pid,'本研究只验证单用户环境，未验证并发性能。'*20);knowledge_units.build(pid,original['id'])
    seen=[]
    def handler(request):
        seen.append(request.headers['X-Client-Request-Id'])
        if len(seen)==1:raise httpx.ConnectTimeout('before HTTP',request=request)
        body=json.loads(request.content);obs=json.loads(body['messages'][-1]['content'])['observation']
        content={'claims':[{'statement':'研究只验证单用户环境','quote':'本研究只验证单用户环境','observation_id':obs['id'],'conditions':['单用户']}]}
        return httpx.Response(200,json={'id':'known-connection-retry','choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handler)))
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    report=runner['run_phase'](pid,SimpleNamespace(allocation=grant['id'],phase='calibration',file=[],max_items=None,
        artifact=None,query=None,output=tmp_path/'known-before-send'))
    assert report['status']=='succeeded' and len(seen)==2
    statuses=[r['status'] for r in db.rows('SELECT status FROM generation_runs WHERE task_id=? ORDER BY created_at',(report['task_id'],))]
    assert statuses==['failed_before_send','completed']
    assert report['allocation']['used']==.0003


def test_cache_only_reuses_known_response_and_never_sends_on_miss(client, monkeypatch, tmp_path):
    from pathlib import Path
    import runpy
    from types import SimpleNamespace
    from test_workbench import file
    from app import knowledge_units
    pid,cfg,grant,rid=setup_allocation(client,monkeypatch)
    original=file(client,pid,'此研究仅在单用户环境中验证过性能。'*20);knowledge_units.build(pid,original['id'])
    seen=[]
    def handler(request):
        seen.append(request.headers['X-Client-Request-Id']);body=json.loads(request.content)
        obs=json.loads(body['messages'][-1]['content'])['observation']
        content={'claims':[{'statement':'只验证单用户环境','quote':'此研究仅在单用户环境中验证过性能','observation_id':obs['id'],'conditions':[]}]}
        return httpx.Response(200,json={'id':'cacheable-source-reply','choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}],
                                       'usage':{'prompt_tokens':100,'completion_tokens':50}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handler)))
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/knowledge_paid_testing.py'))
    args=SimpleNamespace(allocation=grant['id'],phase='calibration',file=[original['id']],max_items=None,
        artifact=None,query=None,output=tmp_path/'cache-only',cache_only=False)
    assert runner['run_phase'](pid,args)['status']=='succeeded'
    count=db.one('SELECT COUNT(*) n FROM generation_runs')['n']
    args.cache_only=True
    assert runner['run_phase'](pid,args)['status']=='succeeded'
    other=file(client,pid,'另一研究没有完成并发场景验证，需要另行检查。'*20);knowledge_units.build(pid,other['id']);args.file=[other['id']]
    miss=runner['run_phase'](pid,args)
    assert miss['status']=='failed' and '缓存' in miss['error']
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n']==count and len(seen)==1

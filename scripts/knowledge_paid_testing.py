"""Prepare or run bounded real knowledge tests using a separately authorized cap.

The prepare command performs no model calls and creates no allocation. An
allocation is a local administrative action, never inferred from a timeout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / 'runner.py'), run_name='runtime_setup')
from app import db, expense_evidence as expense, paid_testing, provider, security, tasks
from app.summary_billing import BudgetExceeded, budget


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def primary(pid):
    key = provider.get_key()
    return expense.resolve_config(pid, provider.config(), key), key


def snapshot(pid):
    from app import knowledge_acceptance, knowledge_builds
    cfg, key = primary(pid)
    plan = knowledge_builds.preview(pid, {'namespace': 'literature', 'mode': 'llm', 'budget_limit': 0})
    account_rows = db.rows("SELECT id,content FROM expense_evidence WHERE project_id=? AND kind='account_observation' ORDER BY rowid DESC LIMIT 4", (pid,))
    observations = [{'evidence_id': r['id'], **db.loads(r['content'], {})} for r in account_rows]
    fingerprints = {}
    for table in ('generation_runs', 'generation_billing_groups', 'generation_billing_group_runs'):
        rows = sorted(db.dumps(row) for row in db.rows('SELECT * FROM ' + table))
        fingerprints[table] = {'rows': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    result = {'created_at': db.now(), 'project_id': pid, 'paid_requests': 0,
              'new_testing_allocation_created': False, 'budget': budget(pid),
              'provider': {k: cfg.get(k) for k in ('base_url', 'model', 'protocol', 'currency', 'input_price', 'output_price', 'max_output_tokens', 'max_request_cost')},
              'valid_pricing_basis': bool(key and expense.source_for(cfg, hashlib.sha256(key.encode()).hexdigest())),
              'account_observations': observations, 'semantic_plan': plan,
              'acceptance': knowledge_acceptance.status(pid), 'historical_fingerprints': fingerprints,
              'required_external_evidence': ['服务商逐请求执行与账单凭证', '真实人工标注与盲审', '用户实际云服务访问配置', '另一台清洁机器访问条件'],
              'cost_note': '以下为配置计价下的输出预算上界，未含输入、复核、综合及DRIFT，不能当作预计或实际支出。'}
    if expense.finite(cfg.get('output_price')):
        result['extraction_output_only_ceiling'] = plan['estimated_extraction_calls'] * cfg['max_output_tokens'] * cfg['output_price'] / 1_000_000
    return result


def run_phase(pid, args):
    from app import knowledge_builds, knowledge_maintenance, knowledge_compiler, graphrag_adapter
    allocation = paid_testing.get(pid, args.allocation)
    if paid_testing.closed(pid, args.allocation):
        raise BudgetExceeded('测试额度已关闭')
    cfg, key = primary(pid)
    payload = {'provider_profile': 'primary', 'budget_limit': allocation['limit'],
               'budget_scope_id': args.allocation, 'namespace': 'literature'}
    if getattr(args,'cache_only',False):payload['knowledge_cache_only']=True
    if getattr(args,'resume_authorization',None):payload['paid_testing_resume_id']=args.resume_authorization
    if getattr(args,'batch_extraction',False):payload['batch_extraction']=True
    if getattr(args,'parallel_requests',1)!=1:payload['knowledge_parallel_requests']=args.parallel_requests
    if args.phase == 'semantic':
        payload.update(mode='llm', file_ids=args.file, file_order='smallest_first',
                       defer_maintenance=True, support_checks=False, discover_topics=False)
        with db.transaction():
            task = knowledge_builds.start(pid, payload)
            if task['status'] != 'queued':
                raise ValueError('相同范围已有任务正在执行，请先查看现有任务')
            paid_testing.bind(pid, args.allocation, task)
            db.execute("UPDATE tasks SET status='running',attempt=attempt+1,heartbeat=?,lease_until=? WHERE id=?", (time.time(), time.time() + 120, task['id']))
    else:
        if args.phase == 'replay':
            original=db.one('SELECT * FROM generation_runs WHERE id=? AND project_id=?',(args.unknown_run,pid))
            from app.paid_testing import resume_authorization,replay_allowed
            metadata=db.loads(original['manifest'],{}) if original else {}
            references=metadata.get('knowledge_refs',[])
            operation=metadata.get('knowledge_operation')
            if (not original or original['status']!='outcome_unknown' or
                    operation not in ('knowledge_extract','knowledge_extract_batch') or
                    not references or any(r.get('type')!='observation' for r in references) or
                    len({r['id'] for r in references})!=len(references) or
                    (operation=='knowledge_extract' and len(references)!=1)):
                raise ValueError('仅能明确重试已有未知原文抽取请求及其原始范围')
            from app import knowledge_lineage
            knowledge_lineage.validate_refs(pid,references,remote=True)
            observations=[knowledge_lineage.owned(pid,'observation',r['id']) for r in references]
            if operation=='knowledge_extract_batch':
                from app.knowledge_batch_compiler import payload_for
                from app.knowledge_model_bridge import SYSTEM
                wire_hash=hashlib.sha256((SYSTEM+'\n'+db.dumps(payload_for(observations))).encode()).hexdigest()
                if wire_hash!=metadata.get('outgoing_context_hash'):
                    raise ValueError('原批次输入或协议已变化，不能将不同请求当作原请求重试')
            payload.update(replay_observation_ids=[r['id'] for r in references],replay_run_id=original['id'],
                           replay_operation=operation)
            kind='knowledge_paid_calibration'
        elif args.phase == 'maintenance':
            # Prepare a durable source-bound queue before any paid call.
            plan = knowledge_maintenance.prepare(pid, 'literature', True, True, True, args.file or None)
            candidates = [r for r in plan['items'] if r['status'] in ('queued', 'budget_blocked', 'failed')]
            if args.max_items:
                candidates = candidates[:args.max_items]
            if not candidates:
                return {'phase': args.phase, 'no_available_items': True, 'allocation': paid_testing.report(pid, args.allocation)}
            payload.update(item_ids=[r['id'] for r in candidates], file_ids=args.file, support_checks=False, discover_topics=False)
            kind = 'knowledge_maintenance'
        elif args.phase == 'index':
            payload.update(reports=True,embeddings=True,full_index=True,max_assertions=10000)
            kind='knowledge_graphrag_build'
        elif args.phase == 'drift':
            if not args.artifact or not args.query:
                raise ValueError('DRIFT测试须明确选择真实索引与问题，不会自动重发旧未知请求')
            payload.update(artifact_id=args.artifact, mode='drift', query=args.query)
            kind = 'knowledge_graphrag_query'
        else:
            from app.material_policy import research_sql
            units = db.rows("SELECT u.id,u.text,f.id AS file_id FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id "
                            "JOIN files f ON f.id=h.file_id WHERE f.project_id=? AND f.active=1 AND " + research_sql() +
                            " AND u.review_state!='excluded' AND length(u.text)>=200 AND length(u.text)<=1800 ORDER BY length(u.text),u.id", (pid,))
            selected = next((u for u in units if (not args.file or u['file_id'] in args.file)
                             and security.file_allows_remote(u['file_id'], pid)), None)
            if not selected:
                raise ValueError('未找到适合小额校准的真实研究原文')
            payload['unit_id'] = selected['id']
            kind = 'knowledge_paid_calibration'
        with db.transaction():
            task = tasks.enqueue(pid, kind, payload)
            paid_testing.bind(pid, args.allocation, task)
            db.execute("UPDATE tasks SET status='running',attempt=attempt+1,heartbeat=?,lease_until=? WHERE id=?", (time.time(), time.time() + 120, task['id']))
    task = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    done = threading.Event()
    heartbeat = threading.Thread(target=tasks.heartbeat_task, args=(task['id'], task['attempt'], done), daemon=True)
    heartbeat.start(); started = time.perf_counter(); result = None; error = None
    try:
        # Keep the one configured account/model; grant guard also rejects any
        # fallback, changed price or changed task payload before submission.
        request_cfg={**cfg,'max_attempts':3}
        if getattr(args,'connect_timeout',None) is not None:
            request_cfg['connect_timeout_seconds']=args.connect_timeout
        if getattr(args,'stream',False):request_cfg['streaming']=True
        with provider.connection_pool(),provider.request_scope(request_cfg, key):
            if args.phase == 'semantic':
                result = knowledge_builds.process(task, db.loads(task['payload'], {}))
            elif args.phase == 'maintenance':
                result = knowledge_maintenance.run(task, payload)
            elif args.phase == 'index':
                result=graphrag_adapter.build(task,payload)
            elif args.phase == 'drift':
                result = graphrag_adapter.query(task, payload)
            else:
                if args.phase=='replay':
                    from app import knowledge_lineage
                    from app.paid_testing import replay_allowed
                    if not replay_allowed(task,original):raise BudgetExceeded('该未知请求没有有效的单次明确重试授权，未发送')
                    if payload['replay_operation']=='knowledge_extract_batch':
                        from app.knowledge_batch_compiler import extract_batch
                        observations=[knowledge_lineage.owned(pid,'observation',oid) for oid in payload['replay_observation_ids']]
                        compiled=extract_batch(task,observations)
                        result={'observation_ids':payload['replay_observation_ids'],'source_bound_rows':compiled,
                                'run_ids':list(dict.fromkeys(rid for row in compiled.values() for rid in row['run_ids'])),
                                'quotes_validated':True,'human_review':False,'complete':True}
                    else:observation=knowledge_lineage.owned(pid,'observation',payload['replay_observation_ids'][0])
                else:observation = knowledge_compiler.observe_unit(pid, payload['unit_id'], remote=True, intent='research')
                if result is None:
                    candidates, runs = knowledge_compiler.extracted(task, observation, 'llm')
                    result = {'observation_id': observation['id'], 'source_bound_candidates': candidates, 'run_ids': runs,
                              'quotes_validated': True, 'human_review': False, 'complete': True}
        unresolved=db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown')",(task['id'],))
        state = 'outcome_unknown' if unresolved else 'incomplete' if result.get('complete') is False else 'succeeded'
    except BaseException as exception:
        error = security.redact(exception)
        unknown = db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown')", (task['id'],))
        state = 'outcome_unknown' if unknown else 'budget_blocked' if isinstance(exception, BudgetExceeded) else 'cancelled' if isinstance(exception, (KeyboardInterrupt,InterruptedError)) else 'failed'
    finally:
        done.set(); heartbeat.join(timeout=1)
    report = {'project_id': pid, 'task_id': task['id'], 'phase': args.phase, 'status': state,
              'elapsed_seconds': time.perf_counter() - started, 'result': result, 'error': error,
              'runs': db.rows('SELECT id,status,usage,cost,reserved,remote_id,created_at FROM generation_runs WHERE task_id=?', (task['id'],)),
              'allocation': paid_testing.report(pid, args.allocation), 'human_acceptance_claimed': False}
    db.execute('UPDATE tasks SET status=?,result=?,error=?,lease_until=NULL,updated_at=? WHERE id=?',
               (state, db.dumps({'phase': args.phase, 'result': result}), error, db.now(), task['id']))
    write(args.output / (args.phase + '-' + task['id'] + '.json'), report)
    return {k: v for k, v in report.items() if k not in ('result', 'runs')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project')
    parser.add_argument('--output', type=Path, default=ROOT / '.runtime/knowledge-paid-testing-20261028')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    allocate = commands.add_parser('allocate')
    allocate.add_argument('--limit', type=float, required=True)
    allocate.add_argument('--authorization-file', type=Path, required=True)
    run = commands.add_parser('run')
    run.add_argument('--allocation', required=True)
    run.add_argument('--phase', choices=['calibration', 'replay', 'semantic', 'maintenance', 'index', 'drift'], required=True)
    run.add_argument('--unknown-run',help='需要明确单次重试的原运行ID')
    run.add_argument('--artifact'); run.add_argument('--query')
    run.add_argument('--file', action='append', default=[])
    run.add_argument('--max-items', type=int)
    run.add_argument('--cache-only',action='store_true',help='只重用已完成模型响应；缓存缺失时不发送请求')
    run.add_argument('--resume-authorization',help='明确人工续编授权记录；保留原预留与原预算')
    run.add_argument('--stream',action='store_true',help='本次执行使用SSE流；不改变全局接口配置')
    run.add_argument('--batch-extraction',action='store_true',help='完整读取每个范围并小批量语义抽取，保留逐段出处与检查点')
    run.add_argument('--parallel-requests',type=int,choices=[1,2,3],default=1)
    run.add_argument('--connect-timeout',type=int,choices=range(1,121),metavar='SECONDS',help='本次运行的建连等待时间；不改变全局接口和计价配置')
    report = commands.add_parser('report'); report.add_argument('--allocation', required=True)
    close = commands.add_parser('close'); close.add_argument('--allocation', required=True); close.add_argument('--reason', required=True)
    args = parser.parse_args()
    projects = db.rows('SELECT id FROM projects WHERE archived=0')
    pid = args.project or (projects[0]['id'] if len(projects) == 1 else None)
    if not pid:
        parser.error('请选择真实项目')
    if args.command == 'prepare':
        result = snapshot(pid); write(args.output / 'prepared-plan.json', result)
        print(json.dumps({'project_id': pid, 'paid_requests': 0, 'files': result['semantic_plan']['file_count'],
                          'extraction_calls': result['semantic_plan']['estimated_extraction_calls'],
                          'extraction_output_only_ceiling': result.get('extraction_output_only_ceiling'),
                          'expense_gaps': result['acceptance']['expense_evidence_gaps'], 'path': str(args.output / 'prepared-plan.json')}, ensure_ascii=False))
        return 0
    if args.command == 'allocate':
        cfg, key = primary(pid)
        result = paid_testing.create(pid, args.limit, args.authorization_file.read_text(encoding='utf-8-sig'), cfg, key)
    elif args.command == 'run':
        if args.max_items is not None and args.max_items <= 0:
            parser.error('--max-items必须为正整数')
        result = run_phase(pid, args)
    elif args.command == 'close':
        result = paid_testing.close(pid, args.allocation, args.reason)
    else:
        result = paid_testing.report(pid, args.allocation)
    write(args.output / (args.command + '-latest.json'), result)
    display={**result,'allocation':{k:result['allocation'][k] for k in ('allocation_id','limit','currency','used','closed')}} if result.get('allocation') else result
    print(json.dumps(display, ensure_ascii=False, indent=2))
    return int(result.get('status', 'succeeded') != 'succeeded')


if __name__ == '__main__':
    raise SystemExit(main())

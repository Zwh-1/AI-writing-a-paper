"""Explicit, immutable testing allocations; historical holds remain charged.

Creating an allocation requires a concrete user-authorized amount. Merely
preparing a test plan never changes a budget or authorizes network requests.
Only locally bound knowledge tasks can use the historical-hold exception.
"""
import hashlib
from decimal import Decimal

from . import db, expense_evidence as expense, security
from .config import MAX_PARALLEL_REQUESTS
from .summary_billing import BudgetExceeded, finite, key_hash

KINDS = {'knowledge_paid_calibration', 'graph_wiki_build',
         'knowledge_maintenance', 'knowledge_graphrag_query', 'knowledge_graphrag_build'}

# Requests that belong to *other* live tasks in the same project are genuinely
# in flight; they are tolerated, not exempted, but only up to this many at once
# so a runaway fan-out cannot hide behind the same rule.
EXTERNAL_INFLIGHT_LIMIT = 9


def digest(value):
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def unresolved(pid):
    return db.rows("SELECT g.* FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id "
                   "WHERE g.project_id=? AND g.status NOT IN ('rejected','failed_before_send') AND m.run_id IS NULL "
                   "AND (g.status IN ('pending','sent','outcome_unknown') OR g.cost IS NULL)", (pid,))


def get(pid, allocation_id):
    row = db.one("SELECT * FROM expense_evidence WHERE id=? AND project_id=? AND kind='paid_testing_allocation'", (allocation_id, pid))
    if not row:
        raise BudgetExceeded('测试额度不存在或不属于此项目')
    return db.loads(row['content'], {})


def closed(pid, allocation_id):
    return bool(db.one("SELECT id FROM expense_evidence WHERE project_id=? AND kind='paid_testing_closed' "
                       "AND json_extract(content,'$.allocation_id')=?", (pid, allocation_id)))


@db.atomic
def create(pid, amount, authorization, cfg, key):
    project = security.require_project(pid)
    if not finite(amount) or amount <= 0 or amount > 1000000:
        raise ValueError('须提供用户明确授权的正数测试支出上限')
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('须记录用户对具体支出上限的授权依据')
    if not key or not expense.source_for(cfg, key_hash(key)):
        raise BudgetExceeded('测试额度也必须具有绑定接口、账号、模型和币种的计价依据')
    if cfg.get('currency') != expense.budget_currency(pid):
        raise BudgetExceeded('测试额度与项目账本币种不一致')
    if db.one("SELECT id FROM tasks WHERE project_id=? AND status IN ('queued','running')", (pid,)):
        raise BudgetExceeded('请在已有任务结束后创建独立测试额度')
    for row in db.rows("SELECT id FROM expense_evidence WHERE project_id=? AND kind='paid_testing_allocation'", (pid,)):
        if not closed(pid, row['id']):
            raise BudgetExceeded('已有开放的测试额度，请继续使用或明确关闭后再创建')
    if expense.ledger(pid)['counts']['unsupported_cost']:
        raise BudgetExceeded('历史已登记金额仍缺少计价依据，不能以测试额度掩盖')
    baseline = unresolved(pid)
    content = {'limit': float(amount), 'currency': cfg['currency'],
               'authorization': authorization.strip(), 'authorized_at': db.now(),
               'provider': {k: cfg.get(k) for k in ('base_url', 'model', 'protocol', 'currency', 'input_price', 'output_price')},
               'credential_hash': key_hash(key),
               'historical_unresolved': {r['id']: digest(r) for r in baseline},
               'historical_reservation': expense._sum(r['reserved'] for r in baseline),
               'historical_actual_charges_known': False,
               'project_budget_at_authorization': project['budget'],
               'scoped_project_ceiling': float(Decimal(str(project['budget'])) + Decimal(str(amount))),
               'scope': 'explicitly_bound_knowledge_tests_only',
               'stop_on_new_unresolved': True}
    identity = expense.append(pid, 'paid_testing_allocation', content)
    db.audit(pid, 'paid_testing_allocation_created', {'allocation_id': identity, **content})
    return {'id': identity, **content}


@db.atomic
def bind(pid, allocation_id, task):
    allocation = get(pid, allocation_id)
    if closed(pid, allocation_id):
        raise BudgetExceeded('测试额度已关闭')
    stored = db.one('SELECT * FROM tasks WHERE id=? AND project_id=?', (task['id'], pid))
    if not stored or stored['payload'] != task.get('payload') or stored['kind'] != task.get('kind'):
        raise ValueError('仅可绑定本项目实际保存的任务与完整参数')
    payload = db.loads(stored['payload'], {})
    if stored['kind'] not in KINDS or payload.get('provider_profile') != 'primary':
        raise ValueError('独立测试额度仅允许明确使用主接口的知识测试')
    if not finite(payload.get('budget_limit')) or payload['budget_limit'] > allocation['limit']:
        raise ValueError('任务子预算不能超过本轮测试总上限')
    existing = db.one("SELECT * FROM expense_evidence WHERE project_id=? AND kind='paid_testing_task' "
                      "AND json_extract(content,'$.task_id')=?", (pid, task['id']))
    content = {'allocation_id': allocation_id, 'task_id': task['id'],
               'kind': stored['kind'], 'payload_sha256': digest(stored['payload'])}
    if existing:
        if db.loads(existing['content'], {}) != content:
            raise BudgetExceeded('任务已绑定其他额度或任务参数发生变化')
        return existing['id']
    return expense.append(pid, 'paid_testing_task', content)


def bindings(pid, allocation_id):
    return [db.loads(r['content'], {}) for r in db.rows(
        "SELECT content FROM expense_evidence WHERE project_id=? AND kind='paid_testing_task' "
        "AND json_extract(content,'$.allocation_id')=?", (pid, allocation_id))]


def charge(pid, allocation_id):
    """Count each touched bill group in full, never divide or lose its charge."""
    allocation = get(pid, allocation_id)
    ids = [r['task_id'] for r in bindings(pid, allocation_id)]
    if not ids:
        return Decimal(0)
    placeholders = ','.join('?' for _ in ids)
    runs = db.rows('SELECT g.*,m.group_id FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id '
                   'WHERE g.project_id=? AND g.task_id IN (' + placeholders + ") AND g.status NOT IN ('rejected','failed_before_send')", (pid, *ids))
    total = sum((Decimal(str(r['cost'] if r['cost'] is not None else r['reserved']))
                 for r in runs if not r['group_id']), Decimal(0))
    for identity in {r['group_id'] for r in runs if r['group_id']}:
        group = db.one('SELECT amount,currency FROM generation_billing_groups WHERE id=?', (identity,))
        if group['currency'] != allocation['currency']:
            raise BudgetExceeded('测试账单组币种不一致，停止继续计费')
        total += Decimal(str(group['amount']))
    return total


def guard(task, cfg, key, reservation):
    """Called inside the same IMMEDIATE transaction as request reservation."""
    if not task:
        return None
    pid = task['project_id']
    row = db.one("SELECT * FROM expense_evidence WHERE project_id=? AND kind='paid_testing_task' "
                 "AND json_extract(content,'$.task_id')=?", (pid, task['id']))
    if not row:
        return None
    binding = db.loads(row['content'], {})
    allocation_id = binding['allocation_id']; allocation = get(pid, allocation_id)
    stored = db.one('SELECT * FROM tasks WHERE id=? AND project_id=?', (task['id'], pid))
    if (not stored or stored['kind'] != binding['kind'] or stored['kind'] != task.get('kind')
            or digest(stored['payload']) != binding['payload_sha256']
            or digest(task.get('payload')) != binding['payload_sha256']):
        raise BudgetExceeded('测试任务参数或归属已变化，未发送收费请求')
    if closed(pid, allocation_id):
        raise BudgetExceeded('测试额度已关闭，未发送收费请求')
    if key_hash(key) != allocation['credential_hash'] or any(cfg.get(k) != v for k, v in allocation['provider'].items()):
        raise BudgetExceeded('测试使用的账号、模型、接口、单价或币种与授权快照不一致')
    if not finite(reservation):
        raise BudgetExceeded('测试请求缺少有效费用预留')
    used = charge(pid, allocation_id)
    if used + Decimal(str(reservation)) > Decimal(str(allocation['limit'])):
        raise BudgetExceeded('本轮测试支出已达到授权上限；完成结果与未知费用预留均已保存')
    historical = dict(allocation['historical_unresolved']); exemptions = [];inflight=[];elsewhere=[]
    resumed=resume_authorization(task)
    if resumed:historical.update(resumed['run_fingerprints'])
    parallel=db.loads(task.get('payload'),{}).get('knowledge_parallel_requests',1)
    if type(parallel) is not int or not 1<=parallel<=MAX_PARALLEL_REQUESTS:
        raise BudgetExceeded(f'知识编译并发范围须为1至{MAX_PARALLEL_REQUESTS}')
    owners={}
    def in_flight(run):
        """A pending/sent request is only unresolved if nobody is still watching
        it. The task that owns it keeps its lease fresh while it waits, so once
        that task stops beating the request really is unknown and must be
        authorized explicitly. Checking the current task alone made two paid
        tasks in one project block each other: task B saw task A's live
        requests and refused as if they were abandoned charges."""
        if run['status'] not in ('pending','sent'):return False
        owner=owners.get(run['task_id'])
        if owner is None:
            owner=db.one('SELECT status,heartbeat FROM tasks WHERE id=? AND project_id=?',(run['task_id'],pid))
            owners[run['task_id']]=owner
        return bool(owner and owner['status']=='running' and owner['heartbeat']
                    and __import__('time').time()-owner['heartbeat']<180)
    for run in unresolved(pid):
        if in_flight(run):
            if run['task_id']==task['id']:inflight.append(run['id'])
            else:elsewhere.append(run['id'])
            continue
        if run['id'] not in historical:
            raise BudgetExceeded('新增请求的结果或费用待核实，本轮测试已停止，未自动重复收费')
        if digest(run) != historical[run['id']]:
            raise BudgetExceeded('历史待核请求记录发生变化，请重新核查测试额度依据')
        exemptions.append(run['id'])
    if len(inflight)>=parallel:raise BudgetExceeded('本任务的已预留并发请求已达上限')
    if len(elsewhere)>=EXTERNAL_INFLIGHT_LIMIT:raise BudgetExceeded('项目中同时等待结果的收费任务过多，本次未发送收费请求')
    return {'allocation_id': allocation_id, 'limit': allocation['limit'], 'used_before_request': float(used),
            'currency': allocation['currency'], 'historical_exemption_ids': exemptions,
            'inflight_request_ids':inflight,'external_inflight_request_ids':elsewhere,
            'parallel_requests':parallel,'historical_holds_retained': True}


@db.atomic
def authorize_resume(pid, allocation_id, run_ids, authorization):
    """Record a human-directed continuation without renewing or clearing funds."""
    allocation=get(pid,allocation_id)
    if closed(pid,allocation_id):raise BudgetExceeded('测试额度已关闭')
    if not isinstance(authorization,str) or not authorization.strip():raise ValueError('须保存用户知悉待核请求后要求继续的明确指令')
    if not isinstance(run_ids,list) or not run_ids or any(not isinstance(r,str) for r in run_ids):raise ValueError('须明确本次允许保留并继续的请求ID')
    if db.one("SELECT id FROM tasks WHERE project_id=? AND status IN ('queued','running')",(pid,)):raise BudgetExceeded('已有任务运行，不能变更续编授权范围')
    selected={}
    for rid in set(run_ids):
        run=db.one('SELECT * FROM generation_runs WHERE id=? AND project_id=?',(rid,pid))
        meta=db.loads(run['manifest'],{}) if run else {}
        if (not run or run['status']!='outcome_unknown' or rid in allocation['historical_unresolved']
                or meta.get('paid_testing_allocation_id')!=allocation_id):
            raise BudgetExceeded('只能明确续接本轮额度中已经停止且结果不明的请求；不能重写旧21笔或运行中的请求')
        selected[rid]=digest(run)
    content={'allocation_id':allocation_id,'authorization':authorization.strip(),'authorized_at':db.now(),
             'run_fingerprints':selected,'original_limit':allocation['limit'],'reservations_retained':True,
             'max_replays_per_unknown_request':1,'new_unknown_requests_require_new_authorization':True}
    identity=expense.append(pid,'paid_testing_resume_authorization',content)
    db.audit(pid,'paid_testing_human_directed_resume',{'evidence_id':identity,**content})
    return {'id':identity,**content}


def resume_authorization(task):
    identity=db.loads(task.get('payload'),{}).get('paid_testing_resume_id')
    if not identity:return None
    row=db.one("SELECT * FROM expense_evidence WHERE id=? AND project_id=? AND kind='paid_testing_resume_authorization'",(identity,task['project_id']))
    if not row:raise BudgetExceeded('明确续编授权不存在或不属于本项目')
    result=db.loads(row['content'],{})
    binding=db.one("SELECT content FROM expense_evidence WHERE project_id=? AND kind='paid_testing_task' AND json_extract(content,'$.task_id')=?",(task['project_id'],task['id']))
    if not binding or db.loads(binding['content'],{}).get('allocation_id')!=result['allocation_id']:
        raise BudgetExceeded('续编任务未绑定原测试额度，不能使用保留待核请求的授权')
    return result


def replay_allowed(task,run):
    resumed=resume_authorization(task)
    if not resumed or resumed['run_fingerprints'].get(run['id'])!=digest(run):return False
    return not db.one("SELECT id FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.knowledge_authorized_replay_of_run_id')=? AND status NOT IN ('rejected','failed_before_send') LIMIT 1",(task['project_id'],run['id']))


def report(pid, allocation_id):
    allocation = get(pid, allocation_id)
    return {'allocation_id': allocation_id, **allocation, 'used': float(charge(pid, allocation_id)),
            'closed': closed(pid, allocation_id), 'tasks': bindings(pid, allocation_id),
            'note': '占用包括计价估算、账单保守上界和预留，不代表已核实的实际净扣款。'}


@db.atomic
def close(pid, allocation_id, reason):
    get(pid, allocation_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('请记录关闭原因')
    if not closed(pid, allocation_id):
        expense.append(pid, 'paid_testing_closed', {'allocation_id': allocation_id, 'reason': reason.strip()})
    return report(pid, allocation_id)

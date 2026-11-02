"""Traceable expense bases. Estimates and budget holds never become invoices.

Imported documents are user supplied, not authenticated by their content hash.
All monetary aggregation uses a single explicitly reported currency.
"""
import hashlib
import math
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from . import db, provider, security

router = APIRouter()
DEFAULT_POLICY = {'require_pricing_basis': True, 'block_on_unresolved': True}
EXCLUDED = {'rejected', 'failed_before_send'}
UNRESOLVED = {'pending', 'sent', 'outcome_unknown'}
LABELS = {
    'excluded': '未纳入预算占用', 'group_bill': '汇总账单核对（组内单笔未知）',
    'interval_bill': '账单区间核对上界', 'imported_receipt': '用户导入凭证（未独立验真）',
    'supported_estimate': '有依据的计价估算', 'unsupported_cost': '金额依据待补',
    'reservation': '预算预留，实际费用待核实',
    'unverified_closure': '用户确认无法核实后结转',
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def base(value):
    return str(value or '').rstrip('/').removesuffix('/v1')


def budget_currency(pid):
    # The provider ContextVar may currently contain a fallback or image route.
    return db.setting('expense_currency:' + pid) or db.setting('provider', {}).get('currency', provider.DEFAULT['currency'])


def policy(pid):
    security.require_project(pid)
    return {**DEFAULT_POLICY, **db.setting('expense_policy:' + pid, {}), 'currency': budget_currency(pid)}


def append(pid, kind, content, rid=None, document=None):
    identity = db.uid()
    db.execute('INSERT INTO expense_evidence VALUES(?,?,?,?,?,?,?)',
               (identity, pid, rid, kind, document, db.dumps(content), db.now()))
    return identity


def source_for(cfg, credential_hash):
    """Accept a scoped stored tariff or the existing reproducible account formula.

    A public CNY quote alone cannot justify an account's USD price.
    """
    evidence = cfg.get('expense_pricing_basis') or cfg.get('pricing_evidence') or {}
    if not isinstance(evidence, dict):
        return None
    if any((base(evidence.get('base_url')) != base(cfg.get('base_url')),
            evidence.get('model') != cfg.get('model'),
            evidence.get('credential_hash') != credential_hash,
            evidence.get('currency') != cfg.get('currency'))):
        return None
    fields = ('image_price',) if 'image_price' in cfg else ('input_price', 'output_price')
    if not all(finite(cfg.get(k)) and evidence.get(k) == cfg[k] for k in fields):
        return None
    if evidence.get('kind') == 'imported_tariff':
        record = db.one("SELECT * FROM expense_evidence WHERE id=? AND kind='tariff'", (evidence.get('evidence_id'),))
        if not record or record['document_sha256'] != evidence.get('document_sha256'):
            return None
        path=security.safe_object(record['document_sha256'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record['document_sha256']:
            return None
        stored = db.loads(record['content'], {})
        if any(stored.get(k) != evidence.get(k) for k in (*fields, 'base_url', 'model', 'credential_hash', 'currency')):
            return None
        if stored.get('valid_until') and datetime.fromisoformat(stored['valid_until']) <= datetime.now(timezone.utc):
            return None
        return evidence
    stat = evidence.get('account_model_statistics', {})
    if (evidence.get('verified') is True and evidence.get('source_url') and evidence.get('reference_quote_sha256')
            and evidence.get('captured_at') and evidence.get('account_unit') == cfg.get('currency')
            and stat.get('model') == cfg.get('model') and all(finite(stat.get(k)) for k in ('input_tokens', 'output_tokens', 'account_cost'))):
        amount = (stat['input_tokens'] * cfg['input_price'] + stat['output_tokens'] * cfg['output_price']) / 1e6
        if (math.isclose(amount, stat['account_cost'], rel_tol=1e-8, abs_tol=1e-9)
                or evidence.get('mode') == 'conservative_account_reference_estimate'
                and finite(evidence.get('reproduced_account_base_cost'))
                and math.isclose(evidence['reproduced_account_base_cost'], stat['account_cost'], abs_tol=1e-9)):
            return evidence
    return None


def resolve_config(pid, cfg, key):
    """Attach only this project's most recent matching imported tariff."""
    from .summary_billing import key_hash, priced_config
    result = priced_config(cfg, key)
    for row in db.rows("SELECT * FROM expense_evidence WHERE project_id=? AND kind='tariff' ORDER BY rowid DESC", (pid,)):
        evidence = db.loads(row['content'], {})
        if (base(evidence.get('base_url')) == base(cfg.get('base_url')) and evidence.get('model') == cfg.get('model')
                and evidence.get('credential_hash') == key_hash(key) and evidence.get('currency') == cfg.get('currency')):
            # Never silently replace a configured rate with a different tariff.
            fields = ('image_price',) if 'image_price' in cfg else ('input_price', 'output_price')
            if all(cfg.get(k) is None or cfg[k] == evidence.get(k) for k in fields):
                result.update({k: evidence.get(k) for k in fields})
                result['expense_pricing_basis'] = {**evidence, 'evidence_id': row['id'], 'document_sha256': row['document_sha256']}
            break
    return result


def preflight(pid, cfg, key, images=False, task=None, reservation=None):
    rules = policy(pid)
    from .paid_testing import guard
    testing = guard(task, cfg, key, reservation)
    if not rules['require_pricing_basis'] and not testing:
        return None
    from .summary_billing import BudgetExceeded, key_hash
    if not db.setting('expense_currency:'+pid):
        recorded={db.loads(r['manifest'],{}).get('provider',{}).get('currency') for r in db.rows("SELECT manifest FROM generation_runs WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))}
        recorded.discard(None)
        if recorded and recorded!={rules['currency']}:
            raise BudgetExceeded('历史调用存在不同币种，须先核实原始币种，禁止隐式合并')
        db.set_setting('expense_currency:'+pid,rules['currency'])
        append(pid,'ledger_currency_pinned',{'currency':rules['currency'],'basis':'primary_provider_currency_and_matching_historical_snapshots'})
    if cfg.get('currency') != rules['currency']:
        raise BudgetExceeded('费用币种与项目账本不一致；禁止直接相加或隐式换汇')
    proof = source_for(cfg, key_hash(key))
    if not proof:
        raise BudgetExceeded('缺少绑定当前服务商、账号、模型和币种的计价依据；请在费用依据账本导入服务商报价，未发送收费请求')
    if images and 'image_price' not in cfg:
        raise BudgetExceeded('图像输入的计价上界尚无依据，已阻止收费请求；请使用具备完整图像计价依据的接口')
    if rules['block_on_unresolved'] or testing:
        exemptions = set(testing['historical_exemption_ids'])|set(testing.get('inflight_request_ids',[])) if testing else set()
        pending = any(r['id'] not in exemptions for r in db.rows("SELECT g.id FROM generation_runs g LEFT JOIN generation_billing_group_runs m ON m.run_id=g.id WHERE g.project_id=? AND g.status NOT IN ('rejected','failed_before_send') AND m.run_id IS NULL AND (g.status IN ('pending','sent','outcome_unknown') OR g.cost IS NULL)", (pid,)))
        if pending:
            raise BudgetExceeded('项目仍有已发送但费用未核实的请求；请先补齐账单依据，已阻止新增收费请求')
        if unsupported_cost_runs(pid):
            raise BudgetExceeded('项目存在缺少计价或账单依据的已登记金额；请先补齐凭证，已阻止新增收费请求')
    return {'method': 'scoped_pricing_basis', 'basis': proof, 'currency': cfg['currency'], 'estimate_only': True,
            **({'paid_testing': testing} if testing else {})}


def estimate_basis(run, meta, usage):
    cfg = meta.get('provider', {})
    evidence = meta.get('cost_evidence', {})
    if run['protocol'] == 'images':
        price = cfg.get('image_price')
        return {'formula': '1 × image_price', 'image_price': price, 'source': cfg.get('expense_pricing_basis')} if finite(price) and price == run['cost'] and source_for(cfg, meta.get('credential_hash')) else None
    rates = {k: cfg.get(k) if cfg.get(k) is not None else evidence.get(k) for k in ('input_price', 'output_price')}
    if not all(finite(v) for v in rates.values()) or not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
        return None
    expected = (usage['input_tokens'] * rates['input_price'] + usage['output_tokens'] * rates['output_price']) / 1e6
    if not finite(run['cost']) or not math.isclose(expected, run['cost'], abs_tol=1e-9, rel_tol=1e-8):
        return None
    proof = source_for({**cfg, **rates}, meta.get('credential_hash'))
    if not proof and evidence.get('method')=='verified_account_reference_upper_estimate' and evidence.get('basis'):
        # A timestamp is only a pointer. Recover the original full scoped
        # observation and the explicit run membership from the audit trail.
        for record in db.rows("SELECT id,detail FROM audit WHERE project_id=? AND action='autopilot_scoped_reference_pricing' AND json_extract(detail,'$.evidence.captured_at')=?",(run['project_id'],evidence['basis'])):
            saved=db.loads(record['detail'],{})
            matched=any(item.get('id')==run['id'] and item.get('estimate')==run['cost'] for item in saved.get('updated_completed_run_estimates',[]))
            if matched:
                source=source_for({**cfg,**rates,'currency':evidence.get('currency'),'pricing_evidence':saved.get('evidence',{})},meta.get('credential_hash'))
                if source:proof={**source,'audit_record_id':record['id']};break
    return {'formula': '(input_tokens × input_price + output_tokens × output_price) / 1000000', **rates,
            'recomputed': expected, 'source': proof,
            'estimate_only': True} if proof else None


def _sum(values):
    return float(sum((Decimal(str(v)) for v in values), Decimal(0)))


_UNSUPPORTED_CACHE = {'project': None, 'at': 0.0, 'value': []}
_UNSUPPORTED_LOCK = threading.Lock()
# The answer is a data-integrity statement, not a running total: it only changes
# when a run is recorded with an amount that has no basis. Re-checking it on every
# request made sixteen in-flight requests serialise on a 300 ms scan, which cost
# more throughput than the check bought. A few seconds of staleness still lets the
# gate stop the request *after* the one that went unrecorded.
UNSUPPORTED_CACHE_SECONDS = 3.0


def unsupported_cost_runs(pid):
    """Runs whose recorded amount cannot be re-derived from usage and prices.

    Building the whole ledger parses every manifest in the project; once a
    project held a few thousand runs that cost about a second, and preflight
    runs it on *every* paid request, so concurrent requests serialised on it.
    An amount can only lack a basis when the run carries no scoped pricing
    basis or no usable usage figures, so only those rows are examined in full
    and everything else is settled from the row's own evidence markers.
    """
    cached = _UNSUPPORTED_CACHE
    if cached['project'] == pid and time.monotonic() - cached['at'] < UNSUPPORTED_CACHE_SECONDS:
        return list(cached['value'])
    # Stampede guard: concurrent callers that all miss the window would each run
    # the scan and serialise on the database write transaction anyway.
    with _UNSUPPORTED_LOCK:
        if cached['project'] == pid and time.monotonic() - cached['at'] < UNSUPPORTED_CACHE_SECONDS:
            return list(cached['value'])
        return _unsupported_scan(pid)


def _unsupported_scan(pid):
    rows = db.rows("""SELECT * FROM generation_budget_usage WHERE project_id=? AND cost IS NOT NULL
                      AND (json_extract(manifest,'$.expense_pricing_basis') IS NULL
                           OR json_extract(usage,'$.input_tokens') IS NULL
                           OR json_extract(usage,'$.output_tokens') IS NULL)""", (pid,))
    if not rows:
        _UNSUPPORTED_CACHE.update(project=pid, at=time.monotonic(), value=[])
        return []
    receipts = {r['run_id'] for r in db.rows("SELECT run_id FROM expense_evidence WHERE project_id=? AND kind='receipt'", (pid,))}
    members = {r['run_id'] for r in db.rows('SELECT m.run_id FROM generation_billing_group_runs m JOIN generation_billing_groups b ON b.id=m.group_id WHERE b.project_id=?', (pid,))}
    found = []
    for run in rows:
        if run['status'] in EXCLUDED or run['id'] in members or run['id'] in receipts:
            continue
        if closure_evidence(run):
            continue
        meta, usage = db.loads(run['manifest'], {}), db.loads(run['usage'], {})
        if meta.get('billing_interval_proof') and finite(run['cost']):
            continue
        if not estimate_basis(run, meta, usage):
            found.append(run['id'])
    _UNSUPPORTED_CACHE.update(project=pid, at=time.monotonic(), value=list(found))
    return found


def ledger(pid):
    security.require_project(pid)
    records = db.rows('SELECT * FROM expense_evidence WHERE project_id=? ORDER BY rowid', (pid,))
    receipts = {r['run_id']: r for r in records if r['kind'] == 'receipt'}
    groups = {r['id']: r for r in db.rows('SELECT * FROM generation_billing_groups WHERE project_id=?', (pid,))}
    members = {r['run_id']: r['group_id'] for r in db.rows('SELECT m.* FROM generation_billing_group_runs m JOIN generation_billing_groups b ON b.id=m.group_id WHERE b.project_id=?', (pid,))}
    items = []
    for run in db.rows('SELECT g.*,t.kind AS task_kind FROM generation_budget_usage g LEFT JOIN tasks t ON t.id=g.task_id WHERE g.project_id=? ORDER BY g.created_at DESC,g.id', (pid,)):
        meta, usage = db.loads(run['manifest'], {}), db.loads(run['usage'], {})
        cfg = meta.get('provider', {})
        currency = cfg.get('currency') or meta.get('cost_evidence', {}).get('currency')
        kind, amount, basis = 'reservation', None, {}
        gaps = []
        gid = members.get(run['id'])
        closure = closure_evidence(run)
        if run['status'] in EXCLUDED:
            kind = 'excluded'
            basis = meta.get('transport_failure') or meta.get('transport_failure_audit') or {'status': run['status'], 'error': run['error']}
        elif closure:
            saved = db.one('SELECT * FROM expense_evidence WHERE id=?', (closure,))
            saved = db.loads(saved['content'], {}) if saved else {}
            kind, amount = 'unverified_closure', run['cost']
            basis = {'evidence_id': closure, 'reason': saved.get('reason'),
                     'amount_basis': saved.get('amount_basis'), 'amount_basis_label': AMOUNT_BASIS.get(saved.get('amount_basis')),
                     'reserved_snapshot': saved.get('reserved_snapshot'), 'status_before': saved.get('status_before'),
                     'authenticity': saved.get('authenticity'), 'note': saved.get('note'),
                     'settled_by': 'local_authenticated_user'}
        elif gid:
            kind, currency = 'group_bill', groups[gid]['currency']
            basis = {'group_id': gid, 'per_request_cost_unknown': True, 'group_amount': groups[gid]['amount']}
        elif run['id'] in receipts:
            kind, amount = 'imported_receipt', run['cost']
            rec = receipts[run['id']]
            basis = {'evidence_id': rec['id'], 'document_sha256': rec['document_sha256'], **db.loads(rec['content'], {})}
        elif meta.get('billing_interval_proof') and finite(run['cost']):
            kind, amount, basis = 'interval_bill', run['cost'], meta['billing_interval_proof']
            currency = basis.get('currency') or currency
        elif run['cost'] is not None:
            amount = run['cost']
            basis = estimate_basis(run, meta, usage)
            kind = 'supported_estimate' if basis else 'unsupported_cost'
            if not basis:
                gaps.append('金额缺少可复算的用量、单价来源或可匹配的账单凭证')
        else:
            basis = {'reservation_method': meta.get('cost_evidence', {}).get('method', 'legacy_configured_ceiling'),
                     'reserved': run['reserved'], 'max_request_cost_snapshot': cfg.get('max_request_cost'),
                     'original_cost_evidence': meta.get('cost_evidence'), 'actual_charge_unknown': True}
            gaps.append('需要服务商逐请求账单或请求范围、币种、用量完全匹配的汇总账单')
        if not currency:
            gaps.append('原始快照未明确记录币种')
        if run['status'] in UNRESOLVED:
            gaps.append('远端是否完成或收费尚未证实，禁止把预留认作实际支出')
        items.append({'id': run['id'], 'task_id': run['task_id'], 'task_kind': run['task_kind'], 'model': run['model'],
                      'created_at': run['created_at'], 'status': run['status'], 'kind': kind, 'label': LABELS[kind],
                      'currency': currency, 'amount': amount, 'reserved': run['reserved'], 'budget_occupation': 0 if kind == 'excluded' else run['budget_charge'],
                      'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
                      'remote_id': run['remote_id'], 'client_request_id': meta.get('client_request_id'),
                      'provider_request_id': meta.get('response_evidence', {}).get('provider_request_id'),
                      'base_url': cfg.get('base_url'), 'group_id': gid, 'basis': basis, 'gaps': gaps,
                      'error': security.redact(run['error']) if run['error'] else None})
    by_currency = {}
    for currency in {r['currency'] or 'UNSPECIFIED' for r in items}:
        subset = [r for r in items if (r['currency'] or 'UNSPECIFIED') == currency]
        by_currency[currency] = {'budget_occupation': _sum(r['budget_occupation'] for r in subset),
                                'supported_estimate': _sum(r['amount'] for r in subset if r['kind'] == 'supported_estimate'),
                                'interval_bill_upper_bound': _sum(r['amount'] for r in subset if r['kind'] == 'interval_bill'),
                                'imported_receipts': _sum(r['amount'] for r in subset if r['kind'] == 'imported_receipt'),
                                'unsupported_cost': _sum(r['amount'] for r in subset if r['kind'] == 'unsupported_cost'),
                                'reservation': _sum(r['budget_occupation'] for r in subset if r['kind'] == 'reservation'),
                                'unverified_closure': _sum(r['amount'] for r in subset if r['kind'] == 'unverified_closure'),
                                'group_bill_upper_bound': _sum(g['amount'] for g in groups.values() if g['currency'] == currency)}
    group_rows = []
    for group in groups.values():
        evidence = db.loads(group['evidence'], {})
        group_rows.append({k: group[k] for k in ('id', 'amount', 'currency', 'model', 'updated_at')} | {
            'label': '账单汇总保守上界，不能按请求均摊', 'evidence': evidence,
            'per_request_cost_unknown': True, 'requests': sum(g == group['id'] for g in members.values())})
    return {'project_id': pid, 'generated_at': db.now(), 'policy': policy(pid), 'by_currency': by_currency,
            'counts': {kind: sum(r['kind'] == kind for r in items) for kind in LABELS},
            'gap_count': sum(bool(r['gaps']) for r in items), 'runs': items, 'groups': group_rows,
            'evidence_records': [{'id': r['id'], 'kind': r['kind'], 'run_id': r['run_id'], 'document_sha256': r['document_sha256'], 'created_at': r['created_at']} for r in records],
            'all_actual_costs_verified': not any(r['kind'] in ('supported_estimate', 'unsupported_cost', 'reservation', 'group_bill', 'interval_bill', 'imported_receipt') for r in items),
            'note': '计价估算、保守账单上界、用户凭证与预算预留分别统计；文件哈希证明留存一致性，不证明服务商签发真实性。'}


def load_document(raw):
    if len(raw) > 2_000_000:
        raise HTTPException(413, '费用凭证不能超过2MB')
    try:
        import json
        def unique(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError('重复字段')
                result[k] = v
            return result
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限数值')))
    except (ValueError, UnicodeError):
        raise HTTPException(422, '请导入UTF-8 JSON原始费用凭证；不接受重复字段或非有限数值')
    if not isinstance(value, dict):
        raise HTTPException(422, '费用凭证须为JSON对象')
    def check(item):
        if isinstance(item, dict):
            for k, v in item.items():
                if any(s in k.lower().replace('-', '_') for s in ('api_key', 'authorization', 'password', 'secret', 'access_token')):
                    raise HTTPException(422, '凭证包含凭据字段，请从服务商导出不含密钥的费用凭证')
                check(v)
        elif isinstance(item, list):
            for v in item:
                check(v)
    check(value)
    text = raw.decode('utf-8-sig')
    for profile in ('', 'material-summary', 'gpt-fallback', 'image-generation'):
        try:
            key = security.get_key(profile)
        except Exception:
            key = None
        if key and len(key) >= 8 and key in text:
            raise HTTPException(422, '凭证包含已保存的密钥，已拒绝留存')
    return value


def require_fields(doc, names):
    for name in names:
        if not isinstance(doc.get(name), str) or not doc[name].strip() or len(doc[name]) > 2000:
            raise HTTPException(422, '费用凭证缺少有效字段：' + name)


def date_field(doc, field):
    require_fields(doc, (field,))
    try:
        date = datetime.fromisoformat(doc[field])
        if date.tzinfo is None:
            raise ValueError()
        return date
    except ValueError:
        raise HTTPException(422, field + '须为带时区的ISO日期')


def document_base(doc):
    require_fields(doc, ('base_url', 'model', 'currency', 'source_url'))
    for key in ('base_url', 'source_url'):
        url = urlsplit(doc[key])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise HTTPException(422, key + '须为不含凭据或查询参数的HTTPS地址')
    if doc['currency'] not in ('USD', 'CNY'):
        raise HTTPException(422, '凭证币种须明确为USD或CNY')


@db.atomic
def settle(rid, raw):
    run = db.one('SELECT * FROM generation_runs WHERE id=?', (rid,))
    if not run:
        raise HTTPException(404, '调用记录不存在')
    security.require_project(run['project_id'])
    if run['status'] in EXCLUDED or run['status'] in ('pending', 'sent'):
        raise HTTPException(409, '该调用当前不能登记逐请求账单')
    if db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?', (rid,)):
        raise HTTPException(409, '该调用已属于汇总账单，不能重复登记逐笔金额')
    doc = load_document(raw)
    document_base(doc)
    require_fields(doc, ('receipt_id', 'request_id'))
    billed_at = date_field(doc, 'billed_at')
    if billed_at > datetime.now(timezone.utc):
        raise HTTPException(422, '账单日期不能在未来')
    if not finite(doc.get('amount')):
        raise HTTPException(422, '凭证金额须为非负有限数值')
    meta = db.loads(run['manifest'], {})
    cfg = meta.get('provider', {})
    ids = {v for v in (run['remote_id'], meta.get('response_evidence', {}).get('provider_request_id')) if v}
    exact_match = doc['request_id'] in ids or bool(meta.get('client_request_id') and doc.get('client_request_id') == meta['client_request_id'])
    if not exact_match or doc['model'] != run['model'] or base(doc['base_url']) != base(cfg.get('base_url')):
        raise HTTPException(422, '凭证请求标识、服务商或模型与本机记录不匹配；不能只按时间或金额推断归属')
    if doc['currency'] != cfg.get('currency') or doc['currency'] != budget_currency(run['project_id']):
        raise HTTPException(422, '凭证币种与原始快照或项目账本不一致')
    prior = db.one("SELECT * FROM expense_evidence WHERE run_id=? AND kind='receipt' ORDER BY rowid DESC LIMIT 1", (rid,))
    sha = hashlib.sha256(raw).hexdigest()
    if prior and prior['document_sha256'] == sha:
        return {'ok': True, 'evidence_id': prior['id'], 'idempotent': True}
    if prior:
        raise HTTPException(409, '该调用已有凭证；更正须保留原凭证并走单独审核，禁止直接覆盖')
    # One provider receipt/request pair may not be charged to two local runs.
    for old in db.rows("SELECT content FROM expense_evidence WHERE kind='receipt'"):
        saved = db.loads(old['content'], {})
        if base(saved.get('base_url')) == base(doc['base_url']) and (saved.get('receipt_id'), saved.get('request_id')) == (doc['receipt_id'], doc['request_id']):
            raise HTTPException(409, '该账单条目已登记，禁止重复计费')
    blob = security.put_blob(raw)
    content = {k: doc[k] for k in ('receipt_id', 'request_id', 'model', 'currency', 'amount', 'billed_at', 'base_url', 'source_url')}
    content.update(client_request_id=doc.get('client_request_id'), imported_by='local_authenticated_user',
                   authenticity='user_supplied_not_independently_authenticated', prior_cost=run['cost'], prior_status=run['status'])
    identity = append(run['project_id'], 'receipt', content, rid, blob)
    meta['expense_receipt_id'] = identity
    db.execute("UPDATE generation_runs SET cost=?,manifest=?,status=CASE WHEN status='outcome_unknown' THEN 'manually_settled' ELSE status END WHERE id=?", (doc['amount'], db.dumps(meta), rid))
    db.audit(run['project_id'], 'expense_receipt_imported', {'run_id': rid, 'evidence_id': identity, 'document_sha256': blob, 'amount': doc['amount'], 'currency': doc['currency'], 'prior_cost': run['cost'], 'prior_status': run['status']})
    return {'ok': True, 'evidence_id': identity, 'authenticity': content['authenticity']}


CLOSABLE = {'pending', 'sent', 'outcome_unknown', 'completed', 'completed_invalid'}
AMOUNT_BASIS = {
    'reserved': '按预留上界计入',
    'zero': '按 0 计入并释放预留',
}


def closure_evidence(run):
    """The marker a run carries after a human settled it as unverifiable."""
    return db.loads(run['manifest'], {}).get('expense_unverified_closure')


@db.atomic
def close_unverifiable(pid, run_ids, reason, amount_basis):
    """Book unresolved calls under an explicit human conclusion.

    A request that was sent but never returned usage, a response id or a
    provider bill cannot be priced by either the ledger or the reconciliation
    path. Leaving it open blocks every paid feature forever, so the user may
    settle it explicitly: the reason is stored, the amount follows a stated
    basis, and the reservation is neither silently released nor silently
    promoted to an invoice.
    """
    security.require_project(pid)
    if amount_basis not in AMOUNT_BASIS:
        raise HTTPException(422, '记账口径须明确为按预留上界计入或按 0 计入')
    reason = (reason or '').strip()
    if len(reason) < 8:
        raise HTTPException(422, '请写明核实结论，至少 8 个字；不用“无法核实”一句话代替原因')
    if not isinstance(run_ids, list) or not run_ids or len(run_ids) > 200:
        raise HTTPException(422, '请指定 1 至 200 笔需要结转的调用')
    if len(set(run_ids)) != len(run_ids):
        raise HTTPException(422, '调用编号重复')
    closed, amount_total, evidence_ids = [], 0.0, []
    for rid in run_ids:
        run = db.one('SELECT * FROM generation_runs WHERE id=? AND project_id=?', (rid, pid))
        if not run:
            raise HTTPException(404, '调用记录不存在：' + str(rid))
        if run['status'] not in CLOSABLE:
            raise HTTPException(409, '该调用当前状态不能结转：' + run['status'])
        if run['cost'] is not None:
            raise HTTPException(409, '该调用已有金额，不能按无法核实结转')
        if db.one('SELECT run_id FROM generation_billing_group_runs WHERE run_id=?', (rid,)):
            raise HTTPException(409, '该调用已归入汇总账单，请以账单为准')
        if closure_evidence(run):
            raise HTTPException(409, '该调用已登记过结转，禁止重复记账')
        reserved = float(run['reserved'] or 0)
        amount = reserved if amount_basis == 'reserved' else 0.0
        meta, usage = db.loads(run['manifest'], {}), db.loads(run['usage'], {})
        identity = append(pid, 'unverified_closure', {
            'run_id': rid, 'reason': reason, 'amount_basis': amount_basis,
            'amount': amount, 'reserved_snapshot': reserved,
            'status_before': run['status'], 'remote_id_present': bool(run['remote_id']),
            'usage_present': bool(usage.get('input_tokens') or usage.get('output_tokens')),
            'client_request_id': meta.get('client_request_id'),
            'authenticity': 'local_human_conclusion_not_provider_bill',
            'note': '用户确认无法取得服务商账单后结转；不代表服务商实际扣费，也不用于对外结算。',
        }, rid)
        meta['expense_unverified_closure'] = identity
        meta['expense_unverified_basis'] = amount_basis
        db.execute("UPDATE generation_runs SET cost=?,status='manually_settled',manifest=? WHERE id=?", (amount, db.dumps(meta), rid))
        closed.append(rid)
        amount_total += amount
        evidence_ids.append(identity)
    db.audit(pid, 'expense_unverified_closed', {
        'runs': closed, 'amount_basis': amount_basis, 'amount': amount_total,
        'reason': reason, 'evidence_ids': evidence_ids,
    })
    return {'ok': True, 'closed': len(closed), 'runs': closed, 'amount': amount_total,
            'amount_basis': amount_basis, 'basis_label': AMOUNT_BASIS[amount_basis],
            'evidence_ids': evidence_ids, 'budget': ledger_budget(pid)}


def ledger_budget(pid):
    from .summary_billing import budget
    return budget(pid)


@router.post('/projects/{pid}/expenses/close-unverifiable')
def close_unverifiable_api(pid: str, body: dict):
    return close_unverifiable(pid, body.get('run_ids'), body.get('reason'), body.get('amount_basis'))


@router.get('/projects/{pid}/expenses')
def expense_ledger(pid):
    return ledger(pid)


@router.get('/projects/{pid}/expenses/export')
def export_ledger(pid):
    data=ledger(pid)
    data['evidence_records']=[{**r,'content':db.loads(r['content'],{})} for r in db.rows('SELECT * FROM expense_evidence WHERE project_id=? ORDER BY rowid',(pid,))]
    return Response(db.dumps(data), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="expense-evidence.json"'})


@router.get('/projects/{pid}/expenses/evidence/{identity}')
def evidence_document(pid, identity):
    security.require_project(pid)
    row = db.one('SELECT * FROM expense_evidence WHERE id=? AND project_id=?', (identity, pid))
    if not row:
        raise HTTPException(404, '费用依据不存在')
    return {**row, 'content': db.loads(row['content'], {})}


@router.get('/projects/{pid}/expenses/evidence/{identity}/document')
def download_document(pid, identity):
    row = evidence_document(pid, identity)
    if not row['document_sha256']:
        raise HTTPException(404, '该依据没有导入文件')
    path = security.safe_object(row['document_sha256'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['document_sha256']:
        raise HTTPException(409, '原始凭证缺失或哈希校验失败')
    return Response(path.read_bytes(), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="expense-document.json"'})


@router.post('/projects/{pid}/expenses/account-observations')
def observe_accounts(pid):
    """Existing configured usage endpoints only. No model request or settlement."""
    security.require_project(pid)
    from .summary_billing import key_hash
    from . import material_summary,model_routing,image_generation
    profiles=[('primary',{**provider.DEFAULT,**db.setting('provider',{})},''),
              ('material-summary',material_summary.config(),'material-summary'),
              ('gpt-fallback',model_routing.config(),'gpt-fallback'),
              ('image-generation',image_generation.config(),'image-generation')]
    results=[]
    for profile,cfg,key_profile in profiles:
        try:
            key=security.get_key(key_profile)
            if not key or not cfg.get('model'):continue
            observation={'profile':profile,'base_url':cfg['base_url'],'model':cfg['model'],
                         'credential_hash':key_hash(key),'request_url':provider.usage_endpoint(cfg['base_url']),'fetched_at':db.now()}
            with provider.request_scope(cfg,key):
                with provider.client({**cfg,'timeout_seconds':20}) as client:
                    response=client.get(observation['request_url'],headers=provider.headers('openai'))
            observation.update(http_status=response.status_code,response_sha256=hashlib.sha256(response.content).hexdigest())
            if response.status_code!=200:
                observation['error']='账单接口HTTP '+str(response.status_code)
            else:
                raw=response.json();normalized=provider.normalize_account_usage(raw)
                payload=raw.get('data',raw) if normalized['response_scope']=='data' else raw
                observation['account']=normalized
                observation['model_stats']=[{k:v for k,v in stat.items() if k in ('model','requests','input_tokens','output_tokens','total_tokens','cache_creation_tokens','cache_read_tokens','cost','actual_cost','account_cost') and (k=='model' and isinstance(v,str) or finite(v))}
                                            for stat in payload.get('model_stats',[]) if isinstance(stat,dict)]
                observation['exact_currency_reported']=normalized['unit_source']=='response'
                observation['note']='账号汇总不等于项目实际支出；不同费用字段保留原值，未选作实际扣款'
            identity=append(pid,'account_observation',observation)
            results.append({'profile':profile,'evidence_id':identity,'http_status':observation['http_status'],'error':observation.get('error')})
        except Exception as error:
            # Only the error type is retained; remote bodies can contain secrets.
            observation={'profile':profile,'error_type':type(error).__name__,'fetched_at':db.now()}
            identity=append(pid,'account_observation',observation)
            results.append({'profile':profile,'evidence_id':identity,'error_type':type(error).__name__})
    return {'profiles':results,'note':'已保存'+str(len(results))+'个接口的免费账单查询结果；未修改任何单笔费用或预留'}


@router.post('/usage/{rid}/receipt')
async def upload_receipt(rid: str, file: UploadFile = File(...)):
    return settle(rid, await file.read(2_000_001))


@router.post('/projects/{pid}/expenses/tariffs/{profile}')
async def upload_tariff(pid: str, profile: str, file: UploadFile = File(...)):
    security.require_project(pid)
    raw = await file.read(2_000_001)
    doc = load_document(raw)
    document_base(doc)
    captured = date_field(doc, 'captured_at')
    if captured > datetime.now(timezone.utc):
        raise HTTPException(422, '报价采集时间不能在未来')
    if doc.get('valid_until') and date_field(doc, 'valid_until') <= datetime.now(timezone.utc):
        raise HTTPException(422, '报价依据已过期')
    if profile == 'primary':
        cfg, key = provider.config(), provider.get_key()
    elif profile == 'material-summary':
        from .material_summary import config
        cfg, key = config(), security.get_key(profile)
    elif profile == 'gpt-fallback':
        from .model_routing import snapshot
        cfg, key = snapshot()
    elif profile == 'image-generation':
        from .image_generation import snapshot
        cfg, key = snapshot()
    else:
        raise HTTPException(422, '接口类型无效')
    if not key:
        raise HTTPException(422, '请先保存该接口的密钥，报价依据须绑定当前账号')
    if base(doc['base_url']) != base(cfg['base_url']) or doc['model'] != cfg['model'] or doc['currency'] != cfg['currency']:
        raise HTTPException(422, '报价服务商、模型或币种与该接口配置不匹配')
    fields = ('image_price',) if profile == 'image-generation' else ('input_price', 'output_price')
    if doc.get('unit') != ('per_image' if profile == 'image-generation' else 'per_million_tokens') or not all(finite(doc.get(k)) for k in fields):
        raise HTTPException(422, '报价须明确计价单位及完整非负有限单价')
    from .summary_billing import key_hash
    content = {k: doc[k] for k in ('base_url', 'model', 'currency', 'source_url', 'captured_at', 'unit', *fields)}
    content.update(kind='imported_tariff', profile=profile, credential_hash=key_hash(key), valid_until=doc.get('valid_until'),
                   authenticity='user_supplied_not_independently_authenticated')
    with db.transaction():
        blob = security.put_blob(raw)
        identity = append(pid, 'tariff', content, document=blob)
        db.audit(pid, 'expense_tariff_imported', {'evidence_id': identity, 'profile': profile, 'document_sha256': blob})
    return {'ok': True, 'evidence_id': identity, 'note': '报价仅用于有依据的预算和估算，不代表实际账单；已配置单价不同须先统一配置'}

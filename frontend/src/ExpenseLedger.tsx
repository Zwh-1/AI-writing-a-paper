import { useEffect, useState } from 'react';
import { apiUrl, api, send } from './client';
import './expenseLedger.css';
type Row = Record<string, any>;
const labels: Record<string, string> = {
  reservation: '预算预留，实际费用待核实', unsupported_cost: '金额依据待补',
  supported_estimate: '有依据的计价估算', interval_bill: '账单区间核对上界',
  group_bill: '汇总账单成员', imported_receipt: '用户导入凭证', excluded: '未纳入预算占用',
  unverified_closure: '用户确认无法核实后结转',
};
const money = (value: number | null) => value === null ? '未知' : Number(value).toFixed(8).replace(/0+$/, '').replace(/\.$/, '');

export default function ExpenseLedger({ pid, tick, onChanged }: { pid: string; tick: number; onChanged: () => void }) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('gaps');
  const [page, setPage] = useState(0);
  const [profile, setProfile] = useState('primary');
  const [picked, setPicked] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const [basis, setBasis] = useState('reserved');
  useEffect(() => {
    let active = true;
    setData(null); setError(''); setPage(0);
    if (pid) api(`/projects/${pid}/expenses`).then(result => { if (active) setData(result); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [pid, tick]);
  const endpoint = `/projects/${pid}/expenses`;
  const reload = () => { if (pid) api(endpoint).then(setData).catch(e => setError(e.message)); };
  async function action(work: () => Promise<Row>, text: string) {
    if (busy) return;
    setBusy(true); setError(''); setMessage('');
    try { const result = await work(); setMessage(result.note || text); onChanged(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function upload(url: string, file: File) {
    const body = new FormData(); body.append('file', file);
    return api(url, { method: 'POST', body });
  }
  const runs = (data?.runs || []).filter((r: Row) => filter === 'all' || (filter === 'gaps' ? r.gaps.length > 0 : r.kind === filter));
  const visible = runs.slice(page * 40, (page + 1) * 40);
  const closable = (data?.runs || []).filter((r: Row) => r.kind === 'reservation');
  const chosen = closable.filter((r: Row) => picked.includes(r.id));
  const chosenReserved = chosen.reduce((sum: number, r: Row) => sum + Number(r.reserved || 0), 0);
  const reasonLength = reason.trim().length;
  const toggle = (id: string) => setPicked(list => list.includes(id) ? list.filter(item => item !== id) : [...list, id]);
  return <section className="panel mt expense-ledger" aria-label="费用依据账本">
    <div className="section-heading"><h2>费用依据账本</h2><span>{data ? `${data.runs.length} 次调用 · ${data.gap_count} 笔待补依据` : '读取中'}</span></div>
    <p className="muted mb">计价估算、账单上界和预算预留分别统计。预留不代表实际支出，汇总账单不按请求均摊。导入文件会留存原件及 SHA-256；用户导入凭证尚未独立验真。</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {message && <p role="status">{message}</p>}
    {data && <>
      <p>收费前检查：{data.policy.require_pricing_basis ? '要求完整计价依据' : '兼容模式'}；{data.policy.block_on_unresolved ? '存在待核实调用时阻止新增收费请求' : '继续保留未核实预留'}。项目币种 {data.policy.currency}。</p>
      <div className="table-wrap"><table className="data-table"><thead><tr><th>币种</th><th>预算占用</th><th>计价估算</th><th>汇总账单上界</th><th>区间账单上界</th><th>导入凭证</th><th>金额依据待补</th><th>预留</th><th>无法核实结转</th></tr></thead>
        <tbody>{Object.entries(data.by_currency).map(([currency, amount]) => { const a = amount as Row; return <tr key={currency}><td>{currency === 'UNSPECIFIED' ? '币种待核实' : currency}</td>{['budget_occupation', 'supported_estimate', 'group_bill_upper_bound', 'interval_bill_upper_bound', 'imported_receipts', 'unsupported_cost', 'reservation', 'unverified_closure'].map(k => <td key={k}>{money(a[k])}</td>)}</tr>; })}</tbody></table></div>
      <div className="actions mt mb">
        <a className="btn" href={apiUrl(`${endpoint}/export`)} download>导出全部费用与依据</a>
        <button className="btn" disabled={busy} onClick={() => void action(() => send(`${endpoint}/account-observations`), '已保存免费账单查询结果；未推断或修改单笔费用')}>查询服务商账单（免费）</button>
        <button className="btn" disabled={busy} onClick={() => void action(async () => {
          const result = await send(`/projects/${pid}/tasks/billing-reconcile`, {});
          return { note: (result.profiles || []).map((r: Row) => `${r.profile}：${r.reconciled ? '请求范围匹配，已保存账单上界' : r.reason}`).join('；') || result.message };
        }, '账单核对完成')}>核对请求范围与汇总账单</button>
      </div>
      {closable.length > 0 && <section className="expense-closure" aria-label="无法核实的调用结转">
        <div className="section-heading"><h3>无法核实的调用结转</h3><span>{closable.length} 笔 · 预留 {money(closable.reduce((sum: number, r: Row) => sum + Number(r.reserved || 0), 0))} {data.policy.currency}</span></div>
        <p className="muted mb">这些调用发出后没有用量、没有服务商响应标识，也没有可匹配的账单，账本无法复算金额，长期保留会一直阻断收费任务。可以在这里给出结论：结论与口径都写入审计，结转不等于服务商账单，也不用于对外结算。</p>
        <div className="expense-closure-list">
          {closable.map((r: Row) => <label key={r.id} className="expense-closure-row">
            <input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} />
            <span>{r.model}<small>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.status} · {r.task_kind}</small></span>
            <span className="expense-closure-amount">预留 {money(r.reserved)}<code>{r.id.slice(0, 8)}</code></span>
          </label>)}
        </div>
        <div className="actions mt">
          <button className="btn" disabled={busy} onClick={() => setPicked(closable.map((r: Row) => r.id))}>全选 {closable.length} 笔</button>
          <button className="btn" disabled={busy || !picked.length} onClick={() => setPicked([])}>清空选择</button>
          <span>已选 {chosen.length} 笔 · 预留合计 {money(chosenReserved)}</span>
        </div>
        <label className="expense-closure-reason">核实结论与原因
          <textarea aria-label="核实结论与原因" rows={2} value={reason} onChange={e => setReason(e.target.value)} placeholder="例如：10月29日批量知识维护中途中断，服务商不提供逐请求账单，用量与响应标识均无法取得" />
        </label>
        <p className={`expense-closure-hint${reasonLength < 8 ? ' warn' : ''}`} id="closure-reason-hint" role="status">
          {reasonLength < 8
            ? `核实结论至少 8 个字，当前 ${reasonLength} 字：写清是批处理中断、断网，还是服务商不提供逐请求账单，后续核对以这段结论为准。`
            : `结论已可登记，共 ${reasonLength} 字。`}
        </p>
        <div className="actions">
          <label>记账口径 <select aria-label="结转记账口径" value={basis} onChange={e => setBasis(e.target.value)}>
            <option value="reserved">按预留上界计入（预算占用不变）</option>
            <option value="zero">按 0 计入（释放预留，不视为已扣费）</option>
          </select></label>
          <button className="btn primary" disabled={busy || !chosen.length} aria-describedby="closure-reason-hint" title={reasonLength < 8 ? `核实结论至少 8 个字，当前 ${reasonLength} 字` : '登记后写入审计，可随时导出'} onClick={() => {
            if (!chosen.length) { setError('请先勾选需要结转的调用'); return; }
            if (reasonLength < 8) { setError(`核实结论至少 8 个字，当前 ${reasonLength} 字；这是一次会影响账面分类的结论，请写清原因`); return; }
            void action(async () => {
              const result = await send(`${endpoint}/close-unverifiable`, { run_ids: picked, reason: reason.trim(), amount_basis: basis });
              setPicked([]); setReason(''); reload();
              return { note: `已结转 ${result.closed} 笔，${result.basis_label}，合计 ${money(result.amount)} ${data.policy.currency}；已记入审计，服务商凭证未改动` };
            }, '结转完成');
          }}>登记结转（{chosen.length} 笔）</button>
        </div>
        {basis === 'zero' && <p className="muted">按 0 计入会把预留从预算占用中释放；日后拿到账单仍应导入逐请求凭证更正。</p>}
      </section>}
      <details className="mb"><summary>导入计价依据</summary>
        <p>报价 JSON 须包含 base_url、model、currency、source_url、captured_at（带时区）、unit 及完整单价。文字报价单位为 per_million_tokens，单价为 input_price 和 output_price；生图单位为 per_image，单价为 image_price。依据绑定导入时的账号、模型和接口。</p>
        <div className="actions"><label>报价所属接口 <select aria-label="报价所属接口" value={profile} onChange={e => setProfile(e.target.value)}><option value="primary">主模型</option><option value="material-summary">资料总结</option><option value="gpt-fallback">GPT 备选</option><option value="image-generation">生图</option></select></label>
          <label className="btn upload-label">导入服务商报价 JSON<input type="file" accept=".json,application/json" hidden disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void action(() => upload(`${endpoint}/tariffs/${profile}`, file), '计价依据已留存'); }} /></label></div>
      </details>
      <div className="actions mb"><label>显示范围 <select aria-label="费用记录筛选" value={filter} onChange={e => { setFilter(e.target.value); setPage(0); }}><option value="gaps">待补依据</option><option value="all">全部调用</option>{Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><span>{runs.length} 笔</span></div>
      {visible.length ? <div className="table-wrap"><table className="data-table expense-runs"><thead><tr><th>调用 / 模型</th><th>分类与状态</th><th>Token 输入 / 输出</th><th>金额与预算占用</th><th>依据 / 凭证</th></tr></thead><tbody>{visible.map((r: Row) => <tr key={r.id}>
        <td data-label="调用 / 模型"><strong>{r.model}</strong><small>{new Date(r.created_at).toLocaleString('zh-CN')}</small><small>{r.task_kind}</small><code>{r.id}</code></td>
        <td data-label="分类与状态">{r.label}<small>{r.status}</small></td><td data-label="Token 输入 / 输出">{r.input_tokens ?? '未知'} / {r.output_tokens ?? '未知'}</td>
        <td data-label="金额与预算占用">{r.group_id ? '单笔金额未知，已归汇总账单' : `${money(r.amount)} ${r.currency || '币种待核实'}`}<small>预算占用 {money(r.budget_occupation)}</small>{r.group_id && <small>汇总金额仅计入该组首笔，其余成员不重复计入</small>}</td>
        <td data-label="依据 / 凭证"><details><summary>查看依据{r.gaps.length ? '及缺口' : ''}</summary><p>{r.base_url}</p><p>远端响应 ID：{r.remote_id || '未返回'}</p><p>服务商请求 ID：{r.provider_request_id || '未返回'}</p>{r.gaps.map((g: string) => <p key={g}>{g}</p>)}{r.error && <p>{r.error}</p>}<pre className="path-text">{JSON.stringify(r.basis, null, 2)}</pre>{r.basis?.evidence_id && <a className="btn" href={apiUrl(`${endpoint}/evidence/${r.basis.evidence_id}/document`)} download>下载留存凭证</a>}</details>
          {!r.group_id && !['excluded', 'imported_receipt'].includes(r.kind) && !['pending', 'sent'].includes(r.status) && <label className="btn upload-label">导入请求账单<input type="file" accept=".json,application/json" hidden disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void action(() => upload(`/usage/${r.id}/receipt`, file), '已留存匹配凭证及原始金额，未独立验证签发真实性'); }} /></label>}
        </td></tr>)}</tbody></table></div> : <p className="muted">当前筛选没有费用记录。</p>}
      {runs.length > 40 && <div className="actions mt"><button className="btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>上一页</button><span>第 {page + 1} / {Math.ceil(runs.length / 40)} 页</span><button className="btn" disabled={(page + 1) * 40 >= runs.length} onClick={() => setPage(p => p + 1)}>下一页</button></div>}
      <details className="mt"><summary>汇总账单依据（{data.groups.length} 组）</summary>{data.groups.map((g: Row) => <details key={g.id}><summary>{g.model} · {g.requests} 次请求 · 上界 {money(g.amount)} {g.currency}</summary><p>{g.label}</p><pre className="path-text">{JSON.stringify(g.evidence, null, 2)}</pre></details>)}</details>
      <details className="mt"><summary>留存依据与免费查询记录（{data.evidence_records.length} 条）</summary>{data.evidence_records.map((r: Row) => <details key={r.id}><summary>{r.kind} · {new Date(r.created_at).toLocaleString('zh-CN')}</summary><EvidenceRecord pid={pid} record={r} /></details>)}</details>
      <details className="mt"><summary>逐请求账单的必填内容</summary><p>JSON 须包含 receipt_id、request_id、base_url、model、currency、amount、billed_at（带时区）、source_url。请求标识须与本机收到的响应或服务商请求标识精确匹配；仅有本机已留存 client_request_id 时，也可通过服务商原样回传的 client_request_id 匹配。无法匹配的历史未知请求继续列为缺口。</p></details>
    </>}
  </section>;
}

function EvidenceRecord({ pid, record }: { pid: string; record: Row }) {
  const [value, setValue] = useState<Row | null>(null);
  const [error, setError] = useState('');
  return <><button className="btn" onClick={() => { void api(`/projects/${pid}/expenses/evidence/${record.id}`).then(setValue).catch(e => setError(e.message)); }}>读取依据</button>{record.document_sha256 && <a className="btn" href={apiUrl(`/projects/${pid}/expenses/evidence/${record.id}/document`)} download>下载原始 JSON</a>}{error && <p role="alert">{error}</p>}{value && <pre className="path-text">{JSON.stringify(value, null, 2)}</pre>}</>;
}

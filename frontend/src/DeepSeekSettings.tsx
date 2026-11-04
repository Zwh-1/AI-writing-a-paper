import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, KeyRound, LoaderCircle, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api, send } from "./client";
import "./deepseekSettings.css";

type Row = Record<string, any>;
const officialEndpoint = (address: string) => {
  try {
    const host = new URL(address).hostname.toLowerCase().replace(/\.$/, "");
    return host === "deepseek.com" || host.endsWith(".deepseek.com");
  } catch { return false; }
};
const beijingTime = (value?: string | null) => {
  if (!value || Number.isNaN(new Date(value).getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
};
const blankProvider = () => ({
  base_url: "https://api.deepseek.com/v1", protocol: "openai", model: "deepseek-chat",
  max_output_tokens: 8192, timeout_seconds: 600, context_limit: 100000,
  max_request_cost: 2, input_price: null, output_price: null, currency: "USD",
  json_mode: false, supports_images: false, thinking_enabled: false, streaming: false, max_attempts: 5,
});

export default function DeepSeekSettings({ onChanged }: { onChanged: () => void }) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [editor, setEditor] = useState<Row | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<Row[]>([]);
  const [editorError, setEditorError] = useState("");
  const [editorMessage, setEditorMessage] = useState("");
  const [menu, setMenu] = useState("");
  const [strategy, setStrategy] = useState<Row | null>(null);
  const [officialPolicy, setOfficialPolicy] = useState<Row | null>(null);
  const [policyLoading, setPolicyLoading] = useState(true);
  const [policySaving, setPolicySaving] = useState(false);
  const [policyError, setPolicyError] = useState("");
  const [policyMessage, setPolicyMessage] = useState("");
  const policyEpoch = useRef(0);
  const policyPending = useRef(false);
  const pending = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const editorOpen = editor !== null;

  useEffect(() => {
    let alive = true;
    api("/deepseek/settings").then((result) => {
      if (!alive) return;
      setData(result); setStrategy(result.strategy); onChanged();
    }).catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      if (policyPending.current) return;
      const epoch = ++policyEpoch.current;
      try {
        const result = await api("/deepseek/official-api-policy");
        if (alive && epoch === policyEpoch.current) {
          setOfficialPolicy(result); setPolicyError("");
        }
      } catch (e) {
        if (alive && epoch === policyEpoch.current) setPolicyError((e as Error).message);
      } finally {
        if (alive && epoch === policyEpoch.current) setPolicyLoading(false);
      }
    };
    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 60000);
    return () => { alive = false; ++policyEpoch.current; window.clearInterval(interval); };
  }, []);

  useEffect(() => {
    if (editorOpen && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [editorOpen]);

  useEffect(() => {
    if (!menu) return;
    const dismiss = (event: MouseEvent) => {
      if (!(event.target as HTMLElement).closest(".ds-menu-wrap")) setMenu("");
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenu(""); };
    document.addEventListener("click", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("click", dismiss); document.removeEventListener("keydown", escape); };
  }, [menu]);

  const accept = (result: Row) => { setData(result); setStrategy(result.strategy); onChanged(); };
  async function reloadOfficialPolicy() {
    if (policyPending.current) return;
    const epoch = ++policyEpoch.current;
    setPolicyLoading(true); setPolicyError("");
    try {
      const result = await api("/deepseek/official-api-policy");
      if (epoch === policyEpoch.current) setOfficialPolicy(result);
    } catch (e) {
      if (epoch === policyEpoch.current) setPolicyError((e as Error).message);
    } finally {
      if (epoch === policyEpoch.current) setPolicyLoading(false);
    }
  }
  async function saveOfficialPolicy(field: "allow_official_api" | "allow_official_peak_hours", allow: boolean) {
    if (policyPending.current || !officialPolicy) return;
    policyPending.current = true; ++policyEpoch.current;
    setPolicySaving(true); setPolicyError(""); setPolicyMessage("");
    try {
      const result = await send("/deepseek/official-api-policy", { [field]: allow }, "PUT");
      setOfficialPolicy(result);
      setPolicyMessage(field === "allow_official_api"
        ? result.allow_official_api ? "已手动开启官方 API，仍受下方工作日高峰时段限制。" : "已禁用官方 API；再次使用需手动开启。"
        : result.allow_official_peak_hours ? "已手动允许官方 API 在高峰时段调用；使用前仍需开启总授权。" : "已恢复工作日高峰时段限制。");
      onChanged();
    } catch (e) {
      const failure = (e as Error).message;
      // The checkbox displays confirmed server state, including when a timed
      // out save actually reached the server before its response was lost.
      try { setOfficialPolicy(await api("/deepseek/official-api-policy")); }
      catch { setOfficialPolicy(null); }
      setPolicyError("保存失败：" + failure);
    } finally { policyPending.current = false; setPolicySaving(false); }
  }
  function edit(entry?: Row) {
    setMenu(""); setEditor(entry ? { ...entry } : { name: "", ...blankProvider() });
    setApiKey(""); setModels([]); setEditorError(""); setEditorMessage("");
  }
  function closeEditor() {
    if (pending.current) return;
    if ((apiKey || editor && (editor.id
      ? JSON.stringify(editor) !== JSON.stringify(data?.endpoints.find((ep: Row) => ep.id === editor.id))
      : editor.name)) && !window.confirm("放弃尚未保存的接口修改？")) return;
    dialog.current?.close(); setEditor(null); setApiKey("");
  }
  async function action(id: string, fn: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(id); setError(""); setMessage(""); setMenu("");
    try { await fn(); } catch (e) { setError((e as Error).message); }
    finally { pending.current = false; setBusy(""); }
  }
  async function activate(entry: Row) {
    await action(entry.id, async () => {
      requireOfficialPermission(entry);
      accept(await send(`/deepseek/endpoints/${entry.id}/activate`));
      setMessage(`已切换到「${entry.name}」，后续 DeepSeek 请求使用此接口。`);
    });
  }
  async function remove(entry: Row) {
    if (!window.confirm(`删除「${entry.name}」及其本机保存的密钥？`)) return;
    await action(entry.id, async () => {
      accept(await send(`/deepseek/endpoints/${entry.id}`, {}, "DELETE"));
      setMessage(`已删除「${entry.name}」。`);
    });
  }
  async function balance(entry: Row) {
    await action(entry.id, async () => {
      requireOfficialPermission(entry);
      const receipt = await send(`/deepseek/endpoints/${entry.id}/balance`);
      setData((current) => current && ({ ...current, endpoints: current.endpoints.map((ep: Row) => ep.id === entry.id ? { ...ep, account_usage: receipt } : ep) }));
      setMessage(`「${entry.name}」余额已更新。`);
    });
  }
  async function save(detect = false) {
    if (pending.current || !editor) return;
    pending.current = true; setBusy("editor"); setEditorError(""); setEditorMessage("");
    let persisted = false;
    try {
      if (detect) requireOfficialPermission(editor);
      if (apiKey && !apiKey.trim()) throw new Error("密钥不能只有空格；留空表示保留已有密钥。");
      const fields = Object.keys(blankProvider());
      const result = await send(editor.id ? `/deepseek/endpoints/${editor.id}` : "/deepseek/endpoints", {
        name: editor.name,
        ...(editor.priority != null ? { priority: editor.priority } : {}),
        provider: Object.fromEntries(fields.map((key) => [key, editor[key]])),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }, editor.id ? "PUT" : "POST");
      const saved = editor.id ? result.endpoints.find((ep: Row) => ep.id === editor.id) : result.endpoints[result.endpoints.length - 1];
      accept(result); setEditor(saved); setApiKey(""); persisted = true;
      if (detect) {
        setEditorMessage("接口已保存，正在查询 DeepSeek 模型…");
        const detected = await send(`/deepseek/endpoints/${saved.id}/models`);
        setModels(detected.models);
        setEditorMessage(detected.models.length ? `已找到 ${detected.models.length} 个 DeepSeek 模型；选择模型后请保存。` : "服务商未返回 DeepSeek 模型，请核对地址和权限，或填写实际模型 ID。");
      } else {
        setMessage(`「${saved.name}」已保存${saved.active ? "，当前正在使用" : "，可在列表中切换使用"}。`);
        dialog.current?.close(); setEditor(null);
      }
    } catch (e) {
      setEditorError((persisted ? "接口与密钥已保存；模型查询未完成：" : "保存失败：") + (e as Error).message);
    } finally { pending.current = false; setBusy(""); }
  }
  const update = (key: string, value: any) => setEditor((current) => current && ({ ...current, [key]: value }));
  const active = data?.endpoints.find((ep: Row) => ep.active);
  const officialAllowed = officialPolicy?.allow_official_api === true && officialPolicy.blocked === false && !policyError && !policyLoading && !policySaving;
  const officialBlocked = (entry: Row) => officialEndpoint(entry.base_url) && !officialAllowed;
  function requireOfficialPermission(entry: Row) {
    if (officialBlocked(entry)) throw new Error("官方 API 当前已禁用或授权状态尚未确认，请检查上方总授权及高峰时段限制。");
  }

  return <section className="panel mb ds-settings" aria-label="DeepSeek 接口管理">
    <div className="section-heading ds-heading">
      <div><h2><KeyRound size={18} />DeepSeek 接口</h2><p className="muted">论文写作、资料总结、规则整理与知识库共用当前接口。</p></div>
      <button className="btn primary" disabled={!!busy || !data || data.endpoints.length >= data.max_endpoints} onClick={() => edit()}><Plus size={16} />添加接口</button>
    </div>
    {error && <p className="settings-feedback error" role="alert">{error}</p>}
    {message && <p className="settings-feedback success" role="status">{message}</p>}
    {!data && !error && <p className="muted"><LoaderCircle size={16} className="spin" />正在整理 DeepSeek 接口…</p>}
    {!data && error && <button className="btn" onClick={() => action("reload", async () => accept(await api("/deepseek/settings")))}>重新加载</button>}
    <section className="ds-official-policy" aria-labelledby="ds-official-policy-title" aria-busy={policyLoading || policySaving}>
      <div className="ds-official-policy-heading">
        <h3 id="ds-official-policy-title">官方 API 使用授权</h3>
        <span className={`ds-official-policy-status ${!officialAllowed ? "is-blocked" : ""}`} role="status">
          {policyLoading && !officialPolicy ? "正在读取设置…" : !officialPolicy || policyError ? "授权未确认，已禁用调用" : !officialPolicy.allow_official_api ? "官方 API 已禁用" : officialPolicy.blocked ? "高峰时段已禁用" : "已手动授权，可调用"}
        </span>
      </div>
      <p>DeepSeek 官方 API 默认禁用，只有手动开启下方总授权后才允许使用；开启后仍受工作日高峰时段限制。</p>
      <p id="ds-official-api-warning" className="ds-official-policy-warning">{officialPolicy?.warning || "当前 DeepSeek 官方 API 花费高，缓存命中率非常低，开启后可能产生较高费用。"}</p>
      <label className="checkbox ds-official-policy-toggle">
        <input type="checkbox" checked={officialPolicy?.allow_official_api === true} disabled={!officialPolicy || policyLoading || policySaving || !!busy} aria-describedby="ds-official-api-warning" onChange={(e) => { void saveOfficialPolicy("allow_official_api", e.target.checked); }} />
        <span>允许使用 DeepSeek 官方 API</span>
        {policySaving && <LoaderCircle size={15} className="spin" aria-label="正在保存官方 API 授权设置" />}
      </label>
      <p>北京时间，工作日（周一至周五）09:00–12:00、14:00–18:00 默认禁止使用 DeepSeek 官方 API。总授权开启后，其他时段自动解除时段限制。</p>
      <label className="checkbox ds-official-policy-toggle">
        <input type="checkbox" checked={officialPolicy?.allow_official_peak_hours === true} disabled={!officialPolicy || policyLoading || policySaving || !!busy} onChange={(e) => { void saveOfficialPolicy("allow_official_peak_hours", e.target.checked); }} />
        <span>手动允许官方 API 在上述高峰时段调用（仍需开启总授权）</span>
      </label>
      {officialPolicy?.allow_official_api && officialPolicy.blocked && beijingTime(officialPolicy.next_allowed_at) && <p className="ds-official-policy-next">时段限制解除：{beijingTime(officialPolicy.next_allowed_at)}（北京时间，需保持总授权开启）</p>}
      {policyMessage && <p className="settings-feedback success" role="status">{policyMessage}</p>}
      {policyError && <div className="ds-official-policy-error" role="alert"><span>{policyError}</span><button className="btn" type="button" disabled={policyLoading || policySaving} onClick={() => { void reloadOfficialPolicy(); }}>重新读取</button></div>}
    </section>
    <div className="ds-provider-list">
      {data?.endpoints.map((entry: Row) => <div key={entry.id} className={`ds-provider-row ${entry.active ? "is-active" : ""}`} data-testid="deepseek-endpoint" data-active={entry.active ? "true" : "false"}>
        <div className="ds-provider-avatar" aria-hidden="true">{entry.name.slice(0, 1).toUpperCase()}</div>
        <div className="ds-provider-info"><div className="ds-provider-title"><strong>{entry.name}</strong>{officialBlocked(entry) && <span className="ds-key-missing">已禁用</span>}{!entry.credential.configured && <span className="ds-key-missing">待填写密钥</span>}</div><span className="ds-provider-url">{entry.base_url}</span><span className="ds-provider-model">{entry.model || "待选择模型"}</span></div>
        {entry.account_usage && <div className="ds-balance" title={`查询时间：${entry.account_usage.fetched_at ?? "未知"}`}><span>余额 </span><strong>{entry.account_usage.balance_known ? entry.account_usage.remaining_exact : "未知"}{entry.account_usage.unit_source === "response" ? ` ${entry.account_usage.unit}` : ""}</strong></div>}
        <div className="ds-row-actions">
          {entry.active ? <span className="ds-active-badge"><span />{officialBlocked(entry) ? "已选择（已禁用）" : "使用中"}</span> : <button className="icon-button ds-switch" aria-label={`使用 ${entry.name}`} title={officialBlocked(entry) ? "请先开启官方 API 总授权并检查高峰时段限制" : "切换到此接口"} disabled={!!busy || !entry.credential.configured || officialBlocked(entry)} onClick={() => activate(entry)}>{busy === entry.id ? <LoaderCircle size={19} className="spin" /> : <Play size={19} />}</button>}
          <button className="icon-button" aria-label={`编辑 ${entry.name}`} title="编辑接口" disabled={!!busy} onClick={() => edit(entry)}><Pencil size={18} /></button>
          <div className="ds-menu-wrap"><button className="icon-button" aria-label={`${entry.name} 更多操作`} aria-expanded={menu === entry.id} aria-haspopup="menu" disabled={!!busy} onClick={() => setMenu(menu === entry.id ? "" : entry.id)}><MoreHorizontal size={20} /></button>
            {menu === entry.id && <div className="ds-menu" role="menu"><button role="menuitem" disabled={!entry.credential.configured || officialBlocked(entry)} onClick={() => balance(entry)}><RefreshCw size={15} />查询余额</button><button role="menuitem" className="ds-danger" disabled={entry.active} onClick={() => remove(entry)}><Trash2 size={15} />删除接口{entry.active ? "（使用中）" : ""}</button></div>}
          </div>
        </div>
      </div>)}
    </div>
    {active && <p className="ds-scope-note"><Check size={14} />{officialBlocked(active) ? `当前选择「${active.name}」，官方 API 调用已禁用；请检查上方总授权及高峰时段限制。` : data?.strategy.mode === "manual" ? `当前使用「${active.name}」。切换仅影响后续请求，已提交的请求继续使用原接口。` : `已启用自动切换，「${active.name}」为首选接口。手动切换到其他接口会恢复手动模式。`}</p>}
    {strategy && <details className="ds-strategy"><summary>高级切换设置<ChevronDown size={14} /></summary><p className="muted">默认手动选择 DeepSeek 接口。启用接口自动切换后，明确失败的请求可尝试列表中的其他接口。DeepSeek 单条请求链最多尝试 5 次（含首次），单接口重试与切换接口均计入此上限；连续 5 次明确且可安全重试的失败后，自动切换到已启用并配置就绪的 GPT 备选。图片能力不足或请求被明确拒绝时可提前切换。GPT 独立最多尝试 5 次，成功接替后当前任务的后续调用沿用 GPT。超时、503 或已有输出但结果未知时保留待核实状态，不自动重发；官方 API 的禁用及高峰时段限制仍然生效。</p><fieldset disabled={!!busy} className="settings-fieldset"><div className="form-grid">
      <label className="field">切换方式<select value={strategy.mode} onChange={(e) => setStrategy({ ...strategy, mode: e.target.value })}><option value="manual">手动切换（仅使用当前接口）</option><option value="priority">失败后按优先级切换</option><option value="round_robin">轮询可用接口</option></select></label>
      <label className="field">同一接口重试次数<input type="number" min="0" max="10" value={strategy.retry_same_endpoint} onChange={(e) => setStrategy({ ...strategy, retry_same_endpoint: Number(e.target.value) })} /></label>
      <label className="field">连续失败阈值<input type="number" min="1" max="20" value={strategy.circuit_breaker.failure_threshold} onChange={(e) => setStrategy({ ...strategy, circuit_breaker: { ...strategy.circuit_breaker, failure_threshold: Number(e.target.value) } })} /></label>
      <label className="field">失败后冷却秒数<input type="number" min="1" max="86400" value={strategy.circuit_breaker.cooldown_seconds} onChange={(e) => setStrategy({ ...strategy, circuit_breaker: { ...strategy.circuit_breaker, cooldown_seconds: Number(e.target.value) } })} /></label>
    </div><label className="checkbox"><input type="checkbox" checked={strategy.circuit_breaker.enabled} onChange={(e) => setStrategy({ ...strategy, circuit_breaker: { ...strategy.circuit_breaker, enabled: e.target.checked } })} />连续失败后暂时跳过该接口</label><button className="btn" onClick={() => action("strategy", async () => { accept(await send("/deepseek/strategy", strategy, "PUT")); setMessage("切换设置已保存。"); })}>保存切换设置</button></fieldset></details>}
    {editor && <dialog ref={dialog} className="ds-editor" aria-labelledby="ds-editor-title" onCancel={(e) => { e.preventDefault(); closeEditor(); }} onClick={(e) => { if (e.target === e.currentTarget) { const bounds = e.currentTarget.getBoundingClientRect(); if (e.clientX < bounds.left || e.clientX > bounds.right || e.clientY < bounds.top || e.clientY > bounds.bottom) closeEditor(); } }}>
      <div className="ds-editor-header"><div><h2 id="ds-editor-title">{editor.id ? "编辑 DeepSeek 接口" : "添加 DeepSeek 接口"}</h2><p className="muted">每个接口独立保存密钥、模型和请求设置。</p></div><button className="icon-button" type="button" aria-label="关闭接口编辑" disabled={!!busy} onClick={closeEditor}><X size={19} /></button></div>
      <form noValidate onSubmit={(e) => { e.preventDefault(); save(); }}><fieldset disabled={!!busy} className="settings-fieldset">
        <label className="field">接口名称<input autoFocus value={editor.name} maxLength={100} onChange={(e) => update("name", e.target.value)} placeholder="例如：SwiftAPI / DeepSeek 官方" /></label>
        <label className="field">API 地址<input type="url" value={editor.base_url} onChange={(e) => update("base_url", e.target.value)} placeholder="https://api.example.com/v1" /></label>
        {officialBlocked(editor) && <p className="ds-key-hint muted">官方 API 当前已禁用。可以保存接口配置，检测模型和远程调用需先开启总授权并满足时段限制。</p>}
        <label className="field">API key<input type="password" autoComplete="new-password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={editor.credential?.configured ? `已保存 ${editor.credential.masked}，留空保留` : "填写此接口的 API key"} /></label>
        <p className="ds-key-hint muted">密钥通过 Windows DPAPI 加密保存在本机，保存后仅显示末四位。</p>
        <div className="form-grid"><label className="field">DeepSeek 模型 ID<input list="deepseek-profile-models" value={editor.model} onChange={(e) => update("model", e.target.value)} /><datalist id="deepseek-profile-models">{models.map((model) => <option key={model.id} value={model.id} />)}</datalist></label><label className="field">接口协议<select value={editor.protocol} onChange={(e) => update("protocol", e.target.value)}><option value="openai">Chat Completions</option><option value="responses">Responses</option></select></label></div>
        <details className="ds-request-settings"><summary>请求、预算与兼容性设置</summary><div className="form-grid">
          {[["max_output_tokens", "单次输出上限 Token"], ["context_limit", "上下文上限 Token"], ["timeout_seconds", "请求超时秒数"], ["max_attempts", "最多尝试次数（含首次）"], ["max_request_cost", "单次未知费用预留"], ["input_price", "每百万输入 Token 单价"], ["output_price", "每百万输出 Token 单价"]].map(([key, label]) => <label className="field" key={key}>{label}<input type="number" step={key === "max_attempts" ? "1" : "any"} min={key === "max_attempts" ? 1 : undefined} max={key === "max_attempts" ? 5 : undefined} value={editor[key] ?? ""} onChange={(e) => update(key, e.target.value === "" ? null : Number(e.target.value))} /></label>)}
          <label className="field">计价币种<input value={editor.currency} onChange={(e) => update("currency", e.target.value)} /></label><label className="field">自动切换优先级<input type="number" min="1" value={editor.priority ?? 1} onChange={(e) => update("priority", Number(e.target.value))} /></label>
        </div>{[["json_mode", "启用 JSON 模式"], ["streaming", "使用流式输出"], ["thinking_enabled", "启用推理模式"], ["supports_images", "此接口支持图片输入"]].map(([key, label]) => <label className="checkbox" key={key}><input type="checkbox" checked={!!editor[key]} onChange={(e) => update(key, e.target.checked)} />{label}</label>)}</details>
      </fieldset>
      {editorError && <p className="settings-feedback error" role="alert">{editorError}</p>}{editorMessage && <p className="settings-feedback success" role="status">{editorMessage}</p>}
      <div className="ds-editor-footer"><button className="btn" type="button" disabled={!!busy || officialBlocked(editor)} onClick={() => save(true)}><RefreshCw size={15} />保存并检测模型</button><div className="actions"><button className="btn" type="button" disabled={!!busy} onClick={closeEditor}>取消</button><button className="btn primary" type="submit" disabled={!!busy}>{busy === "editor" && <LoaderCircle size={15} className="spin" />}{busy === "editor" ? "保存中…" : "保存接口"}</button></div></div>
      </form>
    </dialog>}
  </section>;
}

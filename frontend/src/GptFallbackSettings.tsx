import { useEffect, useRef, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;

export default function GptFallbackSettings({ pid }: { pid: string }) {
  const [saved, setSaved] = useState<Row | null>(null);
  const [cfg, setCfg] = useState<Row | null>(null);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const pending = useRef(false);
  useEffect(() => {
    let alive = true;
    api("/gpt-fallback/settings").then((data) => {
      if (!alive) return;
      setSaved(data); setCfg(data.provider); setModels(data.capabilities?.models ?? []);
    }).catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, []);
  async function save(detect = false) {
    if (pending.current || !cfg) return;
    pending.current = true; setBusy(true); setMessage(""); setError("");
    try {
      if (key && !key.trim()) throw new Error("密钥不能只有空格");
      const data = await send("/gpt-fallback/settings", { provider: cfg, ...(key.trim() ? { api_key: key.trim() } : {}) }, "PUT");
      setSaved(data); setCfg(data.provider); setKey("");
      setMessage(data.ready ? "GPT 备选已保存，可以在需要时接替 DeepSeek。" : "设置已保存；请检测并选择有权限的 GPT 模型。");
      if (detect) {
        const result = await send("/gpt-fallback/models"); setModels(result.models);
        setMessage(result.message + (result.models.length ? "，选择模型后保存。" : ""));
      }
    } catch (e) { setError((e as Error).message); }
    finally { pending.current = false; setBusy(false); }
  }
  const dirty = !!key || JSON.stringify(cfg) !== JSON.stringify(saved?.provider);
  return <section className="panel mb" aria-label="GPT 备选设置">
    <div className="section-heading"><h2>GPT 备选</h2><span>{saved?.ready ? "已就绪" : "待完成配置"}</span></div>
    <p className="muted mb">文字任务优先使用 DeepSeek；图片能力不足或请求被明确拒绝时，使用 GPT。超时、503 和已有输出的请求保留待核实状态。</p>
    {cfg && <form noValidate onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <fieldset disabled={busy} className="settings-fieldset">
        <div className="form-grid">
          <label className="field">GPT API 地址<input aria-label="GPT API 地址" value={cfg.base_url} onChange={(e) => setCfg({ ...cfg, base_url: e.target.value })} /></label>
          <label className="field">GPT API key<input aria-label="GPT API key" type="password" autoComplete="new-password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={saved?.credential.configured ? "已保存 " + saved.credential.masked + "，留空保留" : "填写 EcoAPI 密钥"} /></label>
          <label className="field">GPT 模型 ID<input aria-label="GPT 模型 ID" list="gpt-fallback-models" value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })} placeholder="从检测结果选择 GPT 模型" /></label>
          <label className="field">GPT 接口协议<select aria-label="GPT 接口协议" value={cfg.protocol} onChange={(e) => setCfg({ ...cfg, protocol: e.target.value })}><option value="openai">Chat Completions</option><option value="responses">Responses</option></select></label>
        </div>
        <datalist id="gpt-fallback-models">{models.map((m) => <option key={m.id} value={m.id} />)}</datalist>
        <label className="checkbox"><input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />启用 GPT 备选</label>
        <label className="checkbox"><input type="checkbox" checked={cfg.supports_images} onChange={(e) => setCfg({ ...cfg, supports_images: e.target.checked })} />所选 GPT 模型支持图片输入</label>
        <details className="mb"><summary>GPT 输出与预算</summary><div className="form-grid">{[
          ["max_output_tokens", "GPT 输出上限"], ["timeout_seconds", "GPT 超时秒数"], ["max_request_cost", "GPT 单次费用上限"], ["input_price", "GPT 每百万输入 Token 单价"], ["output_price", "GPT 每百万输出 Token 单价"],
        ].map(([name, label]) => <label className="field" key={name}>{label}<input aria-label={label} type="number" step="any" min="0" value={cfg[name] ?? ""} onChange={(e) => setCfg({ ...cfg, [name]: e.target.value === "" ? null : Number(e.target.value) })} /></label>)}</div></details>
        <div className="actions"><button type="submit" className="btn primary">{busy ? "处理中…" : "保存 GPT 备选"}</button><button type="button" className="btn" onClick={() => void save(true)}>检测 GPT 模型</button><button type="button" className="btn" disabled={!pid || !saved?.ready || dirty} onClick={async () => {
          if (pending.current) return;
          pending.current = true; setBusy(true); setError("");
          try { await send("/gpt-fallback/probe", { project_id: pid }); setMessage("GPT 生成验证已排队，按实际用量计费，结果见任务中心。"); }
          catch (e) { setError((e as Error).message); }
          finally { pending.current = false; setBusy(false); }
        }}>测试 GPT 生成（计费）</button><button type="button" className="btn" disabled={!pid || !saved?.ready || dirty} onClick={async () => {
          if (pending.current) return;
          pending.current = true; setBusy(true); setError("");
          try { await send("/provider/probe-image", { project_id: pid, provider_profile: "gpt" }); setMessage("GPT 图片验证已排队，按实际用量计费，结果见任务中心。"); }
          catch (e) { setError((e as Error).message); }
          finally { pending.current = false; setBusy(false); }
        }}>测试 GPT 图片（计费）</button></div>
      </fieldset>
    </form>}
    {message && <p role="status" className="settings-feedback success">{message}</p>}
    {error && <p role="alert" className="settings-feedback error">{error}</p>}
  </section>;
}

import { useEffect, useState } from "react";
import { api, send } from "../client";
import { version as appVersion } from "../../package.json";
import "./runtimeDiagnostics.css";

type Row = Record<string, any>;
declare const __RUNTIME_FRONTEND_BUILD__: Row | null;
const frontendBuild = typeof __RUNTIME_FRONTEND_BUILD__ === "undefined" ? null : __RUNTIME_FRONTEND_BUILD__;
const short = (value: unknown) => typeof value === "string" && value ? value.slice(0, 12) : "未记录";
const count = (value: unknown) => typeof value === "number" ? value.toLocaleString("zh-CN") : "未知";
const issues: Record<string, string> = {
  source_changed_since_start: "源码已有更新，当前运行实例尚未加载。",
  worker_unavailable: "未收到有效执行器心跳，队列可能暂时无法执行。",
  server_worker_build_mismatch: "服务与执行器加载的源码不同。",
};

export default function RuntimeDiagnostics({ pid }: { pid: string }) {
  const [runtime, setRuntime] = useState<Row | null>(null);
  const [policy, setPolicy] = useState<Row | null>(null);
  const [metrics, setMetrics] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => {
    let alive = true;
    let polling = false;
    const controller = new AbortController();
    async function load() {
      if (polling || !alive) return;
      polling = true;
      try {
        const result = await api("/runtime/status", { timeout: 10000, signal: controller.signal });
        if (alive) { setRuntime(result); setError(""); }
      } catch (failure) {
        if (alive) { setRuntime(null); setError((failure as Error).message + "；运行服务可能尚未加载诊断能力。"); }
      }
      try { const result = await api("/ai-cache-policy", { timeout: 10000, signal: controller.signal }); if (alive) setPolicy(result); }
      catch { if (alive) setPolicy(null); }
      if (pid) {
        try { const result = await api(`/projects/${pid}/runtime-metrics`, { timeout: 10000, signal: controller.signal }); if (alive) setMetrics(result); }
        catch { if (alive) setMetrics(null); }
      } else if (alive) setMetrics(null);
      polling = false;
    }
    void load();
    const timer = window.setInterval(() => { void load(); }, 30000);
    return () => { alive = false; controller.abort(); window.clearInterval(timer); };
  }, [pid, epoch]);
  async function change(path: string, body: Row, text: string) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await send(path, body, "PUT"); setMessage(text); setEpoch(value => value + 1); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  const mismatch = frontendBuild?.source_fingerprint && runtime?.server?.source_fingerprint
    && frontendBuild.source_fingerprint !== runtime.server.source_fingerprint;
  return <section className="panel mb runtime-diagnostics" aria-label="运行状态与缓存">
    <div className="section-heading"><h2>运行状态与缓存</h2><button type="button" className="btn" disabled={busy} onClick={() => setEpoch(value => value + 1)}>刷新状态</button></div>
    <p className="muted">这里显示实际加载的构建身份、执行器心跳及本地记录。新任务设置不会改变正在运行任务的输入布局。</p>
    {error && <p role="alert" className="settings-feedback error">{error}</p>}
    {message && <p role="status" className="settings-feedback success">{message}</p>}
    <div className="table-wrap"><table className="data-table"><thead><tr><th>组成</th><th>版本</th><th>源码指纹</th><th>状态</th></tr></thead><tbody>
      <tr><td>当前页面</td><td>{frontendBuild?.version || appVersion}</td><td>{short(frontendBuild?.source_fingerprint)}</td><td>{frontendBuild ? "构建时嵌入" : "构建身份未记录"}</td></tr>
      <tr><td>本机服务</td><td>{runtime?.server?.version || "未知"}</td><td>{short(runtime?.server?.source_fingerprint)}</td><td>{runtime ? "已连接" : "诊断不可用"}</td></tr>
      {(runtime?.workers || []).map((worker: Row) => <tr key={worker.pid}><td>执行器 {worker.pid}</td><td>{worker.version}</td><td>{short(worker.source_fingerprint)}</td><td>{worker.status === "healthy" ? `心跳 ${worker.age_seconds} 秒前` : "心跳已过期"}</td></tr>)}
    </tbody></table></div>
    {mismatch && <p className="runtime-warning" role="status">当前页面与服务的源码指纹不同。请先核对更新状态；正在执行的任务会保留。</p>}
    {(runtime?.readiness?.issues || []).map((issue: string) => <p className="runtime-warning" key={issue}>{issues[issue] || issue}</p>)}
    {runtime && <details><summary>构建与能力详情</summary><p>服务启动：{runtime.server.loaded_at} · 提交 {short(runtime.server.source_commit)} · {runtime.server.source_dirty === true ? "含未提交改动" : runtime.server.source_dirty === false ? "已提交构建" : "提交状态未知"}</p><p>构建 {short(runtime.server.build_id)} · 数据库版本 {runtime.server.database_schema_version ?? "未知"}</p><p>{runtime.server.capabilities?.join(" · ") || "能力未记录"}</p></details>}
    {policy ? <fieldset className="settings-fieldset runtime-cache-controls" disabled={busy}>
      <label className="field">新写作任务的输入布局<select aria-label="新写作任务的输入布局" value={policy.new_task_prompt_layout} onChange={event => void change("/ai-cache-policy", { new_task_prompt_layout: event.target.value }, "已更新新任务布局；现有任务保持原版本。") }><option value="cache_v2">稳定材料优先</option><option value="legacy">兼容布局</option></select></label>
      {policy.new_task_verification_scope && <label className="field">新任务的独立核验范围<select aria-label="新任务的独立核验范围" value={policy.new_task_verification_scope} onChange={event => void change("/ai-cache-policy", { new_task_verification_scope: event.target.value }, "已更新新任务核验协议；现有任务保持原版本。") }><option value="claim_window_v2">主张窗口与明确依赖（全文衔接另待审）</option><option value="legacy_full">完整候选与章节上下文</option></select></label>}
      {policy.new_task_revision_protocol && <label className="field">新任务的修订方式<select aria-label="新任务的修订方式" value={policy.new_task_revision_protocol} onChange={event => void change("/ai-cache-policy", { new_task_revision_protocol: event.target.value }, "已更新新任务修订协议；现有任务保持原版本。") }><option value="restricted_patch_v1">受限段落补丁，保留未授权内容</option><option value="full_candidate">兼容完整候选修订</option></select></label>}
      {[["stable_block_lru_enabled", "复用已验证材料的组装结果"], ["task_recovery_v2_enabled", "复用同任务已完成响应"], ["unit_matrix_cache_enabled", "复用本地检索矩阵"]].map(([key, label]) => <label className="checkbox" key={key}><input type="checkbox" checked={policy[key] === true} onChange={event => void change("/ai-cache-policy", { [key]: event.target.checked }, "缓存设置已保存。") } />{label}</label>)}
      <p className="muted">请求单飞保护：{policy.writing_singleflight_enabled ? "开启" : "关闭"}。服务商缓存参数保持关闭；未知远端请求不会自动重发。</p>
    </fieldset> : <p className="muted">当前服务未提供缓存策略入口，请核对服务是否已经更新。</p>}
    {metrics && <details className="runtime-metrics"><summary>当前项目的阶段缓存记录</summary><div className="table-wrap"><table className="data-table"><thead><tr><th>阶段</th><th>请求</th><th>命中输入</th><th>未命中输入</th><th>加权命中率</th><th>未知记录</th><th>本地复用</th></tr></thead><tbody>{metrics.stages.map((row: Row) => <tr key={row.stage}><td>{row.stage}</td><td>{count(row.requests)}</td><td>{count(row.cache_hit_tokens)}</td><td>{count(row.cache_miss_tokens)}</td><td>{typeof row.weighted_hit_rate === "number" ? (row.weighted_hit_rate * 100).toFixed(2) + "%" : "未知"}</td><td>{count(row.cache_unknown_runs)}</td><td>{count(row.local_response_reuses)}</td></tr>)}</tbody></table></div><p className="muted">{metrics.scope}</p></details>}
    {runtime && <details className="runtime-drain"><summary>维护与安全排空</summary><p>运行中 {runtime.drain.active_tasks} 项 · 排队 {runtime.drain.queued_tasks} 项 · 远端待核实 {runtime.drain.unresolved_requests} 笔。</p><p>排空会暂停接收新任务和领取队列；在途任务继续完成。这里只切换排空状态，不重启服务。</p><p role="status">{runtime.drain.enabled ? runtime.drain.can_restart ? "排空完成，可以安排维护。" : "正在排空；请等待运行任务完成并核实未决请求。" : "正常接收任务。"}</p><button type="button" className="btn" disabled={busy} onClick={() => void change("/runtime/drain", { enabled: !runtime.drain.enabled }, runtime.drain.enabled ? "已恢复接收与领取任务。" : "已开始排空，现有任务继续完成。")}>{runtime.drain.enabled ? "恢复接收任务" : "开始安全排空"}</button></details>}
  </section>;
}

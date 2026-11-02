import { LoaderCircle, Sparkles } from "lucide-react";
import { useAIAutopilot, taskStatusLabels } from "./useAIAutopilot";

export default function AutopilotControls() {
  const c = useAIAutopilot(),
    { data, active, pending } = c;
  if (!data) return null;
  const latest = data.tasks[0],
    disabled = !!pending || !!active;
  const currency = data.budget.currency ?? data.pricing?.currency ?? "USD";
  return (
    <section className="autopilot-settings" aria-label="AI托管设置">
      <div className="workspace-surface-intro">
        <div>
          <h3>托管设置</h3>
          <p>资料与规则共用本轮预算，已有有效结果自动复用。</p>
        </div>
        <span className="autopilot-budget-pill">
          项目可用 {c.available.toFixed(2)} {currency}
        </span>
      </div>
      <div className="autopilot-controls">
        <label className="field">
          <span>本轮总预算上限（{currency}）</span>
          <input
            aria-label="AI托管预算上限"
            type="number"
            min={0.01}
            step={0.01}
            max={c.available}
            value={c.budget}
            disabled={disabled}
            onChange={(event) => c.setBudget(Number(event.target.value))}
          />
        </label>
        <div className="autopilot-options">
          <label>
            <input
              type="checkbox"
              checked={c.autoPublish}
              disabled={disabled}
              onChange={(event) => c.setAutoPublish(event.target.checked)}
            />
            满足发布条件后自动更新规则包
          </label>
          <label>
            <input
              type="checkbox"
              checked={c.followImports}
              disabled={disabled}
              onChange={(event) => c.setFollowImports(event.target.checked)}
            />
            新增资料自动接续托管
          </label>
        </div>
        <button
          className="btn primary"
          disabled={!c.canStart}
          onClick={c.start}
        >
          {pending === "start" ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <Sparkles size={15} />
          )}
          {pending === "start"
            ? "正在提交…"
            : active
              ? "AI 正在托管处理"
              : "AI 接管资料与规则（计费）"}
        </button>
      </div>
      {c.billingGate.reason && (
        <p className="autopilot-budget" role="status">
          {c.billingGate.reason}。本地解析与已有结果仍可查看。
        </p>
      )}
      {!active && c.budget > c.available && (
        <p className="error-text">
          本轮预算不能超过当前可用的 {c.available.toFixed(2)} {currency}。
          <button
            className="text-button"
            disabled={disabled}
            onClick={c.useAvailableBudget}
          >
            使用当前可用预算 {c.available.toFixed(2)} {currency}
          </button>
        </p>
      )}
      {!data.model_ready && (
        <p className="autopilot-budget">
          请在模型设置中配置接口与密钥后开始托管。
        </p>
      )}
      {data.budget.can_call === false && (
        <p className="alert">
          可用额度不足以覆盖费用预留，请先核实费率或账单。
        </p>
      )}
      <div className="autopilot-budget-actions">
        {data.exception_count > 0 && (
          <button
            className="btn"
            disabled={!c.canStart}
            onClick={c.repairExceptions}
          >
            {pending === "repair"
              ? "正在提交修复…"
              : "修复当前 " + data.exception_count + " 项例外（计费）"}
          </button>
        )}
        <button
          className="btn"
          disabled={disabled}
          onClick={c.reconcileBilling}
        >
          {pending === "billing" ? "正在核对账单…" : "核对账单并刷新预算"}
        </button>
        {(data.budget.unknown_reserved ?? 0) > 0 && (
          <span>
            未核实预留 {Number(data.budget.unknown_reserved).toFixed(2)}{" "}
            {currency}。 核对失败会保留预留，不会增加项目预算。
          </span>
        )}
      </div>
      {c.message && (
        <p className="autopilot-budget" role="status">
          {c.message}
        </p>
      )}
      {latest && (
        <div className="autopilot-job" role="status">
          <strong>{taskStatusLabels[latest.status] ?? latest.status}</strong>
          <span>
            {active
              ? active.phase
              : latest.result?.pause_reason ||
                latest.error ||
                `自动处理记录已保存，${data.exception_count} 项例外可继续核对。`}
          </span>
          {active && (
            <>
              <progress
                aria-label="本轮处理进度"
                max={100}
                value={active.progress}
              />
              <button className="btn" disabled={!!pending} onClick={c.cancel}>
                {pending === "cancel" ? "正在停止…" : "停止后续处理"}
              </button>
            </>
          )}
        </div>
      )}
      {data.policy.enabled && !active && (
        <div className="autopilot-follow">
          <span>新资料解析完成后，将按现有预算继续托管。</span>
          <button
            className="text-button"
            disabled={!!pending}
            onClick={c.stopFollowing}
          >
            {pending === "follow" ? "正在更新…" : "关闭自动接续"}
          </button>
        </div>
      )}
      <details className="autopilot-billing">
        <summary>费用与统计说明</summary>
        <p>
          项目总预算 {data.budget.limit.toFixed(2)} {currency}。
          {data.pricing?.verified_reference_estimate
            ? "本轮使用已核实的接口基准公式保守估算，用量估算与账单核对分别保留。"
            : "费用按现有模型设置预留与登记。"}
          未核实费用继续保留预留。
        </p>
        <p>{data.note}</p>
      </details>
    </section>
  );
}

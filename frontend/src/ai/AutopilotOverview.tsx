import {
  ArrowUpRight,
  CheckCircle2,
  LoaderCircle,
  Sparkles,
} from "lucide-react";
import { useAIAutopilot, type AIDomain } from "./useAIAutopilot";
import { WorkspaceNotice } from "../workspace/WorkspaceChrome";
import "../aiAutopilot.css";

export default function AutopilotOverview({
  domain,
  onManage,
}: {
  domain: AIDomain;
  onManage: () => void;
}) {
  const { data, error, active, load } = useAIAutopilot();
  const rate = data?.[domain],
    label = domain === "materials" ? "资料" : "规则";
  const exceptions =
    data?.exceptions.filter(
      (item) => item.kind === (domain === "materials" ? "material" : "rule"),
    ).length ?? 0;
  return (
    <section className="ai-overview" aria-label={`${label}AI处理概览`}>
      <div className="ai-overview-intro">
        <span className="ai-overview-icon">
          <Sparkles size={21} />
        </span>
        <div>
          <div className="ai-overview-kicker">
            AI 自动处理{data?.policy.enabled && <span>自动接续已开启</span>}
          </div>
          <h2>
            {active
              ? "AI 正在整理你的资料与规则"
              : domain === "materials"
                ? "从原件到可用资料，由 AI 持续整理"
                : "AI 判断用途，保留每一条规则的依据"}
          </h2>
          <p>
            {active
              ? active.phase
              : exceptions
                ? `${exceptions} 条例外已单独保留，可以随时查看原因与原文。`
                : "导入资料后继续处理，人工集中关注必要例外。"}
          </p>
        </div>
      </div>
      {rate ? (
        <div className="ai-overview-numbers">
          <div>
            <strong>
              {rate.ai_percent.toFixed(2)}
              <small>%</small>
            </strong>
            <span>AI 参与率</span>
          </div>
          <div>
            <strong>
              {rate.automatic_percent.toFixed(2)}
              <small>%</small>
            </strong>
            <span>自动完成率</span>
          </div>
        </div>
      ) : (
        <div className="ai-overview-wait" role="status">
          <LoaderCircle className="spin" size={17} />
          读取处理进度
        </div>
      )}
      <button className="btn ai-overview-action" onClick={onManage}>
        查看 AI 处理
        <ArrowUpRight size={14} />
      </button>
      {rate && (
        <div className="ai-overview-footer">
          <span>
            <CheckCircle2 size={13} />
            已自动完成 {rate.automatic_completed}/{rate.total} 项
          </span>
          <span>持续提升覆盖率，尽可能减少人工操作</span>
        </div>
      )}
      {error && <WorkspaceNotice error={error} retry={load} />}
    </section>
  );
}

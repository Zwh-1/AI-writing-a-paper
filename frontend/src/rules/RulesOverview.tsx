import { ArrowUpRight, ShieldCheck } from "lucide-react";
import type { Row } from "../workspace/types";
import type { RulesView } from "./useRulesWorkspace";
import { summarizeRuleRecords } from "./ruleRecordSummary";

export default function RulesOverview({
  data,
  navigate,
  onRecords,
  onSnapshot,
}: {
  data: Row | null;
  navigate: (view: RulesView) => void;
  onRecords: (filter: string) => void;
  onSnapshot: () => void;
}) {
  const ready = data?.readiness;
  const summary = summarizeRuleRecords(data?.rules ?? []);
  const metrics = [
    {
      key: "all",
      label: "全部提取记录",
      count: summary.total,
      hint: "包含参考与归并记录",
    },
    {
      key: "publishable",
      label: "可纳入规则包",
      count: summary.publishable,
      hint: `${summary.mandatory} 条强制 · ${summary.recommended} 条建议`,
    },
    {
      key: "reference_only",
      label: "仅供参考",
      count: summary.reference_only,
      hint: "示例、反例与条件参考",
    },
    {
      key: "rejected",
      label: "已归并 / 排除",
      count: summary.rejected,
      hint: "保留记录与出处",
    },
    {
      key: "candidate",
      label: "待确认 / 分类",
      count: summary.candidate,
      hint: "查看仍需核验的记录",
    },
  ];
  return (
    <section className="rules-overview" aria-label="规则概况与发布状态">
      <div className="rules-version-summary">
        <span className="rules-version-icon">
          <ShieldCheck size={22} />
        </span>
        <div>
          <span className="rules-overline">当前写作使用</span>
          <strong>
            {data?.package
              ? `规则包 V${data.package.version}`
              : data
                ? "尚未发布规则包"
                : "正在读取规则包…"}
          </strong>
          <p>
            {data?.package
              ? `${ready.package_rule_count} 条生效要求${ready.dirty ? " · 有未发布修改" : " · 已保存版本快照"}`
              : "AI 整理并检查适用要求后，自动更新规则包。"}
          </p>
        </div>
        <div className="rules-version-actions">
          {ready && (
            <button
              className={`rules-status-pill ${ready.can_publish ? "is-ready" : "is-pending"}`}
              onClick={() => navigate("publication")}
            >
              {ready.can_publish
                ? ready.dirty
                  ? "可发布更新"
                  : "发布条件就绪"
                : `${ready.blockers.length} 项发布待办`}
              <ArrowUpRight size={12} />
            </button>
          )}
          {data?.package && (
            <button className="text-button" onClick={onSnapshot}>
              查看生效快照
              <ArrowUpRight size={12} />
            </button>
          )}
        </div>
      </div>
      <div className="rules-metrics" aria-label="规则概况">
        {metrics.map((metric) => (
          <button
            key={metric.key}
            className={
              metric.key === "publishable" ? "is-publishable" : undefined
            }
            disabled={!data}
            onClick={() => onRecords(metric.key)}
          >
            <span>{metric.label}</span>
            <strong>{data ? metric.count : "—"}</strong>
            <small>
              {data ? metric.hint : "正在读取…"}
              <ArrowUpRight size={12} />
            </small>
          </button>
        ))}
      </div>
      <div className="rules-count-explanation">
        <p>全部记录由以上四类组成。仅适用的强制要求与建议进入规则包。</p>
        <button
          className="text-button"
          disabled={!data}
          onClick={() => navigate("issues")}
        >
          重复与冲突{" "}
          {data ? data.duplicates.length + data.conflicts.length : "—"} 组
          <small>按组统计</small>
          <ArrowUpRight size={12} />
        </button>
      </div>
    </section>
  );
}

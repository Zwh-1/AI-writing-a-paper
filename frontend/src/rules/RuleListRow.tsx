import { ArrowUpRight, FileText } from "lucide-react";
import { scopeLabels, stateLabels } from "../RuleEditor";
import { send } from "../client";
import type { Row } from "../workspace/types";

type Props = {
  rule: Row;
  selected: boolean;
  busy: boolean;
  sourceBusy: string;
  onSelect: (checked: boolean) => void;
  onEdit: (rule: Row) => void;
  onSource: (rule: Row) => void;
  onAutomation?: () => void;
  run: (key: string, work: () => Promise<any>, message: string) => Promise<any>;
};
export default function RuleListRow({
  rule,
  selected,
  busy,
  sourceBusy,
  onSelect,
  onEdit,
  onSource,
  onAutomation,
  run,
}: Props) {
  const purpose =
    rule.state === "reference_only" &&
    rule.machine_review?.outcome === "knowledge"
      ? "知识参考"
      : rule.state === "reference_only" &&
          rule.machine_review?.outcome === "conditional_reference"
        ? "条件参考"
        : (stateLabels[rule.modality] ?? rule.modality);
  const category =
    (
      {
        content: "内容要求",
        format: "格式要求",
        reference: "引用与文献",
        structure: "结构要求",
      } as Record<string, string>
    )[rule.category] ?? "论文要求";
  return (
    <article className="rule-row" data-rule-id={rule.id}>
      <input
        type="checkbox"
        aria-label={`选择规则：${rule.statement}`}
        checked={selected}
        disabled={busy}
        onChange={(event) => onSelect(event.target.checked)}
      />
      <div className="grow">
        <div className="rules-row-meta">
          <span
            className={`badge ${rule.modality === "mandatory" ? "success" : ""}`}
          >
            {purpose}
          </span>
          <span className="badge">{stateLabels[rule.state] ?? rule.state}</span>
          <small>
            {scopeLabels[rule.scope] ?? rule.scope} · {category}
          </small>
        </div>
        <button
          type="button"
          className="rules-row-statement"
          disabled={busy}
          onClick={() => onEdit(rule)}
        >
          {rule.statement}
        </button>
        {rule.source_id ? (
          <button
            className="text-button rule-source-link"
            disabled={sourceBusy === rule.id}
            onClick={() => onSource(rule)}
          >
            <FileText size={12} />
            {sourceBusy === rule.id
              ? "正在读取原文…"
              : `${rule.source_file ?? "来源已移除"} · 第${rule.page_number ?? "?"}页`}
            <ArrowUpRight size={12} />
          </button>
        ) : (
          <small>用户新增规则</small>
        )}
        {rule.merged_sources?.length > 0 && (
          <details className="rule-merged-sources">
            <summary>已保留 {rule.merged_sources.length} 条归并来源</summary>
            {rule.merged_sources.map((member: Row) => (
              <div key={member.id}>
                {member.source_id ? (
                  <button
                    className="text-button rule-source-link"
                    disabled={sourceBusy === member.id}
                    onClick={() => onSource(member)}
                  >
                    {member.source_file ?? "来源文件"} · 第
                    {member.page_number ?? "?"}页<ArrowUpRight size={12} />
                  </button>
                ) : (
                  <small>用户新增记录 · 原始条款已保留</small>
                )}
              </div>
            ))}
          </details>
        )}
        {rule.decision_note && (
          <details className="rules-decision-details">
            <summary>采用 / 排除依据</summary>
            <p className="rule-decision-note">{rule.decision_note}</p>
          </details>
        )}
        {rule.machine_review?.reasons?.length > 0 && (
          <details className="rules-decision-details">
            <summary>自动判断依据</summary>
            {rule.machine_review.reasons.map(
              (reason: string, index: number) => (
                <p className="rule-decision-note" key={index}>
                  {reason}
                </p>
              ),
            )}
          </details>
        )}
        {rule.state === "confirmed" && rule.modality === "pending" && (
          <div className="rule-purpose-next">
            <span>已记录核对状态，AI 仍需判断此条用途。</span>
            {onAutomation && (
              <button className="text-button" onClick={onAutomation}>
                查看 AI 处理
                <ArrowUpRight size={12} />
              </button>
            )}
          </div>
        )}
        {rule.state === "confirmed" &&
          ["example", "counterexample"].includes(rule.modality) && (
            <small className="rule-reference-note">
              此条作为示例参考保留，可用于理解写作方式。
            </small>
          )}
      </div>
      <div className="rule-row-actions">
        <button className="btn" disabled={busy} onClick={() => onEdit(rule)}>
          核对与编辑
        </button>
        <button
          className="text-button"
          disabled={busy}
          onClick={() =>
            run(
              rule.id,
              () =>
                send(
                  `/rules/${rule.id}`,
                  {
                    state: rule.state === "rejected" ? "candidate" : "rejected",
                  },
                  "PATCH",
                ),
              rule.state === "rejected"
                ? "条款已恢复为待确认"
                : "条款已排除，可在已排除列表恢复",
            )
          }
        >
          {rule.state === "rejected" ? "恢复待确认" : "排除"}
        </button>
      </div>
    </article>
  );
}

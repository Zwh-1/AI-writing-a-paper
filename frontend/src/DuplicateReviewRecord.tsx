import { useState } from "react";
import { api } from "./client";
import { stateLabels } from "./RuleEditor";
import type { Row } from "./workspace/types";
import { adoptionStateLabel, adoptionPurposeLabel } from "./duplicateWorkflowLabels";

export default function DuplicateReviewRecord({ pid, reviewId }: { pid: string; reviewId: string }) {
  const [record, setRecord] = useState<Row | null>(null), [error, setError] = useState("");
  return <details onToggle={event => {
    if (event.currentTarget.open && !record) void api(`/projects/${pid}/rules/duplicates/reviews/${reviewId}`).then(setRecord).catch(e => setError(e.message));
  }}>
    <summary>查看本次来源、审核与采用依据</summary>
    <p>这里保存的是当时的复核快照。再次采用会重新检查当前版本。</p>
    {error && <p role="alert">{error}</p>}
    {record && <div>
      <p>程序复核 · {({ ready: "待采用", blocked: "未通过", applied: "已采用", restored: "已恢复" } as Record<string, string>)[record.status] || record.status} · {new Date(record.created_at).toLocaleString()}</p>
      <h4>来源</h4>
      {record.sources.map((source: Row, index: number) => <details key={`${source.rule_id}:${index}`}><summary>{source.file_name || "用户新增记录"} · {source.page ? `第${source.page}页` : "无固定页码"}{source.historical_merged_origin ? " · 历史归并来源" : ""}</summary><pre className="rule-source-text">{source.text}</pre></details>)}
      <h4>审核</h4>
      {record.checks.map((check: Row) => <p key={check.key}>{check.passed ? "通过：" : "待核对："}{check.message}</p>)}
      <h4>采用</h4>
      <p>主记录：{adoptionStateLabel(record.adoption_preview.canonical_state_after)} · {adoptionPurposeLabel(record)}；保留 {record.adoption_preview.retained_rule_occurrences} 条原始记录、{record.adoption_preview.distinct_source_occurrences} 个来源位置。</p>
    </div>}
  </details>;
}

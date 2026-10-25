type Row = Record<string, any>;
export const reviewBuckets: Record<string, string> = {
  machine_verified: "accepted",
  knowledge: "reference",
  example: "reference",
  conditional_reference: "reference",
  needs_resolution: "uncertain",
};
const reasons: Record<string, string> = {
  machine_verified: "语义复核与原文校验通过",
  knowledge: "作为知识参考保留",
  example: "作为示例保留",
  conditional_reference: "保留适用条件，作为条件规范参考",
  needs_resolution: "复核仍不足以确定条款，原候选保留",
  running: "该条正在审核，尚无有效结论",
  unknown: "远端结果尚未核实，原候选保留",
  stale: "审核期间条款或来源发生变化，未覆盖当前内容",
  budget_wait: "预算不足，尚未完成审核",
};

export function ruleReviewDetails(projectId: string, rules: Row[], events: Row[]) {
  const scope = new Set<string>();
  const decisions = new Map<string, Row>();
  let scopeSaved = false;
  for (const event of events) {
    if (Array.isArray(event.rule_ids)) {
      scopeSaved = true;
      for (const id of event.rule_ids) scope.add(id);
    } else if (typeof event.rule_id === "string" && typeof event.state === "string") {
      decisions.set(event.rule_id, event);
    }
  }
  const byId = new Map(rules.filter(rule => rule.project_id === projectId).map(rule => [rule.id, rule]));
  const counts: Record<string, number> = { accepted: 0, reference: 0, uncertain: 0, unreviewed: 0 };
  const items = [...scope].map(id => {
    const event = decisions.get(id);
    const state = event?.state ?? "unreviewed";
    const bucket = reviewBuckets[state] ?? "unreviewed";
    counts[bucket]++;
    return {
      rule_id: id,
      rule: byId.get(id),
      state,
      bucket,
      reason: event?.error || reasons[state] || "该条没有有效审核结论，原候选保留",
    };
  });
  return {
    scope_saved: scopeSaved,
    counts,
    items,
    note: scopeSaved
      ? "审核状态和失败原因取自本次任务记录；条款及原文展示当前保存内容。"
      : "此旧任务未保存逐条审核范围，无法还原条目；任务汇总和历史记录仍保留。",
  };
}

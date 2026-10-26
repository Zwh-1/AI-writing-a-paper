import type { Row } from "../workspace/types";

export function isPublishableRule(rule: Row) {
  return (
    ["confirmed", "machine_verified"].includes(rule.state) &&
    ["mandatory", "recommended"].includes(rule.modality)
  );
}

function recordGroup(rule: Row) {
  if (rule.state === "rejected") return "rejected";
  if (isPublishableRule(rule)) return "publishable";
  if (
    rule.state === "reference_only" ||
    (["confirmed", "machine_verified"].includes(rule.state) &&
      ["example", "counterexample"].includes(rule.modality))
  )
    return "reference_only";
  return "candidate";
}

export function matchesRuleFilter(rule: Row, filter: string) {
  if (filter === "all") return true;
  if (
    ["publishable", "reference_only", "rejected", "candidate"].includes(filter)
  )
    return recordGroup(rule) === filter;
  return rule.state === filter;
}

export function summarizeRuleRecords(rules: Row[]) {
  const summary = {
    total: rules.length,
    publishable: 0,
    reference_only: 0,
    rejected: 0,
    candidate: 0,
    mandatory: 0,
    recommended: 0,
  };
  for (const rule of rules) {
    const group = recordGroup(rule);
    summary[group]++;
    if (group === "publishable") {
      if (rule.modality === "mandatory") summary.mandatory++;
      else summary.recommended++;
    }
  }
  return summary;
}

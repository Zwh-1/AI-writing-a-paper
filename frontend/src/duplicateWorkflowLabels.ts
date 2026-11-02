import { stateLabels } from "./RuleEditor";
import type { Row } from "./workspace/types";

export const adoptionStateLabel = (state: string) =>
  ({ reference_only: "仅供参考", candidate: "待完整核验" } as Record<string, string>)[state] || stateLabels[state] || state;

export const reviewPurposeLabel = (purpose: string | undefined, modality: string) =>
  ({ conditional: "条件参考", knowledge: "知识参考", heading: "标题", noise: "界面或装饰信息", example: "教学示例", counterexample: "教学反例", uncertain: "待核验" } as Record<string, string>)[purpose || ""] || stateLabels[modality] || modality;

export function adoptionPurposeLabel(review: Row) {
  const preview = review.adoption_preview;
  const purpose = preview.canonical_purpose || review.snapshots?.find((member: Row) => member.rule.id === preview.canonical_id)?.comparison?.purpose;
  return reviewPurposeLabel(purpose, preview.canonical_modality_after);
}

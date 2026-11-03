import { api, send } from "./client";
import { pathSegment } from "./paths";
import { projectPath } from "./projects";
import type { SourceRef } from "../workspace/WorkspaceContext";

export type WritingChannel = "literature" | "guidance" | "project" | "products" | "rules";
export type WritingMaterial = {
  id: string;
  title: string;
  source_ref: SourceRef;
  kind: string;
  channel?: WritingChannel;
  text?: string;
  preview_text?: string;
  locator?: Record<string, unknown>;
  read_scope: string;
  conditions?: string[];
  reference_ids?: string[];
  material_use?: { source_purpose?: string; source_kind?: string };
};
export type WritingRetrievalRequest = {
  section_id: string;
  query: string;
  limit?: number;
  channels?: WritingChannel[];
  source_refs?: SourceRef[];
  operation?: string;
  max_chars?: number;
};
export type WritingMaterialSearch = {
  schema: string;
  query: string;
  items: WritingMaterial[];
  total_candidates: number;
  gaps: { reason: string }[];
  read_scope: string;
};
export type WritingEvidenceBundle = {
  schema: string;
  bundle_id: string;
  project_id: string;
  section_id: string;
  query: string;
  constraints: WritingMaterial[];
  primary_evidence: WritingMaterial[];
  guidance: WritingMaterial[];
  derived_context: WritingMaterial[];
  gaps: { reason: string; source_ref?: SourceRef; file_id?: string }[];
  retrieval_trace: Record<string, unknown>[];
  coverage: Record<string, unknown>;
  source_manifest: Record<string, unknown>;
  complete: boolean;
  hash: string;
};
export type WritingFlowSettings = {
  enabled: boolean;
  semantic_review: boolean;
  originality_check: boolean;
  max_revision_rounds: number;
  max_tool_calls: number;
  max_research_rounds: number;
  unit_target_words: number;
  unit_threshold_words: number;
  context_mode: "full_manuscript" | "argument_scoped";
  max_material_chars: number;
};
export type WritingArgumentCard = {
  id: string;
  point_ids: string[];
  question: string;
  role: string;
  claim_ids: string[];
  analysis_goal?: string;
  expected_information_gain?: string;
  relation_to_previous?: { from: string; relation: string } | null;
  gaps?: string[];
};
export type WritingArgumentPlan = {
  schema: string;
  section_id: string;
  hash: string;
  research_question?: string;
  section_goal?: string;
  author_note?: string;
  paragraph_cards: WritingArgumentCard[];
};
export type WritingQuality = {
  schema: string;
  status: string;
  findings: { message: string; kind?: string; paragraph_index?: number }[];
  semantic: { status: string; coverage_complete?: boolean; summary?: string; claims?: Record<string, unknown>[] };
  mapping: { status: string; method?: string };
  repair_rounds: { iteration: number; accepted: boolean; before_findings: number; after_findings: number }[];
  coverage_complete: boolean;
};
export type WritingQualityResponse = {
  task_id: string;
  status: string;
  sections: { section_id?: string; writing_quality?: WritingQuality; evidence_bundle?: WritingEvidenceBundle; argument_plan?: WritingArgumentPlan; context_coverage?: Record<string, unknown>; stale_reasons: string[] }[];
  scope: string;
};

export const writingApi = (projectId: string) => {
  const base = projectPath(projectId) + "/writing";
  const project = projectPath(projectId);
  return {
    sessions: () => api(base + "/sessions"),
    session: (id: string) => api(base + "/sessions/" + pathSegment(id)),
    start: (command: unknown) => send(base + "/start", command),
    preflight: (sectionId: string) => api(base + "/preflight?section_id=" + encodeURIComponent(sectionId)),
    settings: (): Promise<WritingFlowSettings> => api(project + "/writing-flow/settings"),
    saveSettings: (values: Partial<WritingFlowSettings>): Promise<WritingFlowSettings> =>
      send(project + "/writing-flow/settings", values, "PUT"),
    plan: (sectionId: string): Promise<WritingArgumentPlan> =>
      api(project + "/writing-plan?section_id=" + encodeURIComponent(sectionId)),
    savePlan: (sectionId: string, body: { base_hash: string; paragraph_cards: WritingArgumentCard[]; author_note?: string }): Promise<WritingArgumentPlan> =>
      send(project + "/writing-plan?section_id=" + encodeURIComponent(sectionId), body, "PUT"),
    searchMaterials: (body: WritingRetrievalRequest): Promise<WritingMaterialSearch> =>
      send(project + "/writing-retrieval/search", body),
    prepareMaterials: (body: WritingRetrievalRequest): Promise<WritingEvidenceBundle> =>
      send(project + "/writing-retrieval/prepare", body),
    readMaterials: (body: WritingRetrievalRequest & { candidates: (WritingMaterial | SourceRef)[] }): Promise<WritingEvidenceBundle> =>
      send(project + "/writing-retrieval/read", body),
    quality: (taskId: string, sectionId?: string): Promise<WritingQualityResponse> =>
      api(project + "/writing-quality/" + pathSegment(taskId) + (sectionId ? "?section_id=" + encodeURIComponent(sectionId) : "")),
  };
};

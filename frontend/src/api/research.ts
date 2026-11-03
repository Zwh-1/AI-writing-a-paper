import { api, send } from "./client";
import { projectPath } from "./projects";
import { pathSegment } from "./paths";
import type { SourceRef } from "../workspace/WorkspaceContext";
export type ResearchKind =
  | "note"
  | "agent"
  | "conversation"
  | "message"
  | "translation"
  | "annotation"
  | "comment"
  | "canvas"
  | "webpage"
  | "parse_result"
  | "search"
  | "deletion"
  | "proposal"
  | "guide";
export type ResearchRecord<T extends object = Record<string, unknown>> = {
  resource_type: "research_record";
  id: string;
  project_id: string;
  kind: ResearchKind;
  owner_id: string;
  revision: number;
  schema_version: number;
  data: T;
  source_refs: SourceRef[];
  source_state: "current" | "unknown" | "stale" | "deleted";
};
export type NoteContent = {
  title: string;
  text?: string;
  document?: object;
  tags?: string[];
  section_id?: string | null;
  source_refs?: SourceRef[];
};
export type Asset = {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  sha256: string;
  asset_version_id: string;
  revision: number;
  editor_type: string;
  metadata: Record<string, unknown>;
};
export const researchApi = (pid: string) => {
  const base = projectPath(pid);
  return {
    notes: () =>
      api(base + "/research-records?kind=note") as Promise<
        ResearchRecord<NoteContent>[]
      >,
    saveNote: (data: NoteContent, record?: ResearchRecord<NoteContent>) =>
      send(
        base +
          "/research-records" +
          (record ? "/" + pathSegment(record.id) : ""),
        record ? { data, revision: record.revision } : { kind: "note", data },
        record ? "PUT" : "POST",
      ) as Promise<ResearchRecord<NoteContent>>,
    assets: () => api(base + "/asset-library") as Promise<Asset[]>,
    asset: (id: string) =>
      api(
        base + "/asset-library/" + pathSegment(id) + "/metadata",
      ) as Promise<Asset>,
    resolve: (ref: SourceRef) =>
      send(base + "/resource-refs/resolve", ref) as Promise<{
        ref: SourceRef;
        current: boolean;
        location: {
          file_id?: string;
          unit_id?: string;
          parse_id?: string;
          section_id?: string;
        };
      }>,
    capabilities: () =>
      api(base + "/capabilities") as Promise<{
        project_id: string;
        schema_version: number;
        exports: { formats: string[] };
        sync: { research_incremental: boolean };
      }>,
  };
};

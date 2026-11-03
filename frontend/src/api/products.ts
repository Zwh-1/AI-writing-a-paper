import { api, send } from "./client";
import type {
  SourceRef,
  WorkspaceLocation,
} from "../workspace/WorkspaceContext";
import { projectPath } from "./projects";
export type Product = {
  id: string;
  kind: string;
  title: string;
  label: string;
  stamp: string;
  section_id: string;
  source_ref: SourceRef | null;
  state: "current" | "stale";
  reason: string;
  actions: string[];
  open_location?: WorkspaceLocation;
};
export type ProductCatalog = {
  items: Product[];
  total: number;
  limit: number;
  offset: number;
  kinds: { id: string; label: string }[];
};
export type ProductPreview = {
  title: string;
  text: string;
  source_ref: SourceRef;
  kind: string;
  visual: boolean;
};
export type ProductRelations = {
  source: SourceRef;
  selected_version_current: boolean;
  truncated: boolean;
  items: {
    direction: "upstream" | "downstream";
    relation: string;
    type: string;
    id: string;
    title: string;
    state: string;
    reason?: string;
    open_location?: WorkspaceLocation;
  }[];
};
export const productKey = (value: Pick<Product, "kind" | "id">) =>
  value.kind + ":" + value.id;
export const productsPath = (pid: string) => projectPath(pid) + "/products";
export const productApi = (pid: string) => ({
  relations: (source: SourceRef) =>
    api(productsPath(pid) + "/relations", {
      method: "POST",
      body: JSON.stringify({ source, limit: 60 }),
    }) as Promise<ProductRelations>,
  item: (kind: string, id: string) =>
    api(
      productsPath(pid) +
        "/" +
        encodeURIComponent(kind) +
        "/" +
        encodeURIComponent(id),
    ) as Promise<Product>,
  preview: (source: SourceRef) =>
    api(productsPath(pid) + "/preview", {
      method: "POST",
      body: JSON.stringify(source),
    }) as Promise<ProductPreview>,
  reuse: (body: {
    sources: SourceRef[];
    target: string;
    section_id?: string;
    revision_id?: string | null;
  }) => send(productsPath(pid) + "/reuse", body),
});

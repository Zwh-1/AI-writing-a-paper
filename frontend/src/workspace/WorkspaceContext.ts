import { useCallback, useEffect, useState } from "react";
export type SourceRef = {
  project_id?: string;
  type: string;
  id: string;
  version: string;
  file_id?: string;
  parse_id?: string;
  locator?: Record<string, unknown>;
};
export type WorkspaceLocation = {
  page: string;
  view?: string;
  fileId?: string;
  unitId?: string;
  parseId?: string;
  sectionId?: string;
  revisionId?: string;
  conversationId?: string;
  productType?: string;
  productId?: string;
  productRefs?: SourceRef[];
  productSelection?: {
    kind: string;
    id: string;
    title: string;
    label: string;
    source_ref: SourceRef | null;
    actions?: string[];
  }[];
  proposalId?: string;
  canvasId?: string;
  assetId?: string;
  noteId?: string;
  selection?: { start: number; end: number; quote: string };
  returnTo?: WorkspaceLocation;
};
const eventName = "paper-workspace-navigation";
const locationChanged = "paper-workspace-location-changed";
const key = (pid: string, page: string) =>
  "paper-workspace-location-v1:" + pid + ":" + page;
export function readLocation(pid: string, page: string): WorkspaceLocation {
  try {
    return (
      JSON.parse(sessionStorage.getItem(key(pid, page)) || "null") || { page }
    );
  } catch {
    return { page };
  }
}
export function navigateWorkspace(pid: string, location: WorkspaceLocation) {
  location={...readLocation(pid,location.page),...location};
  sessionStorage.setItem(key(pid, location.page), JSON.stringify(location));
  window.dispatchEvent(
    new CustomEvent(eventName, { detail: { pid, location } }),
  );
}
export function useWorkspaceLocation(pid: string, page: string) {
  const [location, setLocation] = useState(() => readLocation(pid, page));
  useEffect(() => {
    setLocation(readLocation(pid, page));
    const changed = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.pid === pid && detail.location?.page === page)
        setLocation(detail.location);
    };
    window.addEventListener(eventName, changed);
    window.addEventListener(locationChanged, changed);
    return () => {
      window.removeEventListener(eventName, changed);
      window.removeEventListener(locationChanged, changed);
    };
  }, [pid, page]);
  const update = useCallback(
    (patch: Partial<WorkspaceLocation>) => {
      const next = { ...readLocation(pid, page), ...patch, page };
      sessionStorage.setItem(key(pid, page), JSON.stringify(next));
      setLocation(next);
      window.dispatchEvent(
        new CustomEvent(locationChanged, { detail: { pid, location: next } }),
      );
    },
    [pid, page],
  );
  return { location, update };
}
export function listenWorkspace(
  fn: (pid: string, location: WorkspaceLocation) => void,
) {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent).detail;
    if (detail) fn(detail.pid, detail.location);
  };
  window.addEventListener(eventName, handler);
  return () => window.removeEventListener(eventName, handler);
}

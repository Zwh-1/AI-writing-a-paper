import { useMemo, useRef, useState } from "react";
import { send } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row, WorkspaceAction } from "../workspace/types";
import { useSourceLocation } from "../workspace/sourceNavigation";

export type MaterialsView =
  "library" | "flow" | "automation" | "originals" | "advanced";
export function useMaterialsWorkspace(
  pid: string,
  tick: number,
  act: WorkspaceAction,
) {
  const resource = useWorkspaceResource<Row[]>(`/projects/${pid}/files`, tick);
  const route = useSourceLocation(pid, "materials", "library");
  const view: MaterialsView = [
    "library",
    "flow",
    "automation",
    "originals",
    "advanced",
  ].includes(route.location.view || "")
    ? (route.location.view as MaterialsView)
    : "library";
  const setView = (view: MaterialsView) => route.update({ view });
  const [upload, setUpload] = useState(false),
    [selected, setSelected] = useState<Row | null>(null);
  const [busy, setBusy] = useState("");
  const actionLock = useRef(false);
  const files = resource.data ?? [];
  const activeFiles = useMemo(
    () => files.filter((file) => file.active),
    [resource.data],
  );
  const summary = useMemo(
    () => ({
      active: activeFiles.length,
      pages: activeFiles.reduce((n, file) => n + (file.pages ?? 0), 0),
      completed: activeFiles.filter(
        (file) => file.summary_state === "completed",
      ).length,
      needsAttention: activeFiles.filter(
        (file) =>
          ["failed", "partial"].includes(file.parse_quality?.state) ||
          file.status === "failed" ||
          file.summary_state === "failed",
      ).length,
      ocrPending: activeFiles.reduce(
        (n, file) => n + (file.parse_quality?.ocr_pending_pages ?? 0),
        0,
      ),
      ocrFailed: activeFiles.reduce(
        (n, file) => n + (file.parse_quality?.ocr_failed_pages ?? 0),
        0,
      ),
      unreviewed: activeFiles.reduce(
        (n, file) => n + (file.parse_quality?.unreviewed_pages ?? 0),
        0,
      ),
    }),
    [activeFiles],
  );
  const run = async (
    key: string,
    work: () => Promise<any>,
    message: string,
  ) => {
    if (actionLock.current) return null;
    actionLock.current = true;
    setBusy(key);
    try {
      const result = await act(work, message);
      if (result) await resource.load();
      return result;
    } finally {
      actionLock.current = false;
      setBusy("");
    }
  };
  return {
    ...resource,
    files,
    route,
    summary,
    activeFiles,
    view,
    setView,
    upload,
    setUpload,
    selected,
    setSelected,
    busy,
    run,
    patch: (file: Row, field: string, value: string) =>
      run(
        `${file.id}:${field}`,
        () => send(`/files/${file.id}`, { [field]: value }, "PATCH"),
        field === "role" ? "资料用途已更新" : "资料使用权限已更新",
      ),
  };
}

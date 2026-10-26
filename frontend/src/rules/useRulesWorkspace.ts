import { useEffect, useRef, useState } from "react";
import { api } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row, WorkspaceAction } from "../workspace/types";

export type RulesView = "catalog" | "automation" | "issues" | "publication";
export function useRulesWorkspace(
  pid: string,
  tick: number,
  act: WorkspaceAction,
) {
  const resource = useWorkspaceResource<Row>(`/projects/${pid}/rules`, tick);
  const [view, setView] = useState<RulesView>("catalog"),
    [filter, setFilter] = useState("all");
  const [catalogKey, setCatalogKey] = useState(0);
  const [editing, setEditing] = useState<Row | null>(null),
    [source, setSource] = useState<Row | null>(null),
    [original, setOriginal] = useState<Row | null>(null);
  const [busy, setBusy] = useState(""),
    [sourceBusy, setSourceBusy] = useState(""),
    [sourceError, setSourceError] = useState("");
  const lock = useRef(false),
    sourceVersion = useRef(0);
  useEffect(
    () => () => {
      sourceVersion.current++;
    },
    [],
  );
  const run = async (
    key: string,
    work: () => Promise<any>,
    message: string,
  ) => {
    if (lock.current) return null;
    lock.current = true;
    setBusy(key);
    try {
      const result = await act(work, message);
      if (result) await resource.load();
      return result;
    } finally {
      lock.current = false;
      setBusy("");
    }
  };
  const openSource = async (rule: Row) => {
    const version = ++sourceVersion.current;
    setSourceBusy(rule.id);
    setSourceError("");
    try {
      const value = await api(`/rules/${rule.id}/source`);
      if (version === sourceVersion.current) setSource({ ...value, rule });
    } catch (failure) {
      if (version === sourceVersion.current)
        setSourceError((failure as Error).message);
    } finally {
      if (version === sourceVersion.current) setSourceBusy("");
    }
  };
  const showRecords = (nextFilter: string) => {
    setFilter(nextFilter);
    setView("catalog");
    setCatalogKey((key) => key + 1);
  };
  const showCandidates = () => showRecords("candidate");
  const onBlocker = (blocker: Row) => {
    if (
      ["RULE_CONFLICT", "RULE_AUTOMATIC_CONFLICTS", "RULE_DUPLICATES"].includes(
        blocker.code,
      )
    )
      setView("issues");
    else if (blocker.code === "RULE_CONFIRMATION_REQUIRED") showCandidates();
    else if (blocker.code === "RULE_MACHINE_REVIEW_STALE")
      setView("automation");
    else if (blocker.code === "RULE_SOURCE_INACTIVE") {
      const rule = resource.data?.rules.find((item: Row) =>
        blocker.rule_ids.includes(item.id),
      );
      if (rule) setEditing(rule);
    }
  };
  return {
    ...resource,
    view,
    setView,
    filter,
    setFilter,
    catalogKey,
    showRecords,
    editing,
    setEditing,
    source,
    setSource,
    original,
    setOriginal,
    busy,
    sourceBusy,
    sourceError,
    openSource,
    run,
    showCandidates,
    onBlocker,
  };
}

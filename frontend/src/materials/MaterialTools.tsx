import { useState } from "react";
import {
  MaterialSummarySettings,
  MaterialSummaryBatch,
} from "../MaterialSummary";
import { MaterialAutomation, RetrievalEvaluation } from "../Completion";
import type { WorkspaceAction } from "../workspace/types";

export function MaterialSummaryTools({
  pid,
  tick,
  refresh,
  onOpen,
}: {
  pid: string;
  tick: number;
  refresh: () => void;
  onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="workspace-details autopilot-manual-tools"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <strong>资料总结设置与独立整理工具</strong><span>按需使用</span>
      </summary>
      {open && (
        <>
          <MaterialSummarySettings onSaved={refresh} />
          <MaterialSummaryBatch
            pid={pid}
            tick={tick}
            onUpdated={refresh}
            onOpen={onOpen}
          />
        </>
      )}
    </details>
  );
}
export function MaterialAdvancedTools({
  pid,
  tick,
  act,
}: {
  pid: string;
  tick: number;
  act: WorkspaceAction;
}) {
  return (
    <div className="material-advanced-tools">
      <div className="workspace-surface-intro">
        <div>
          <h2>高级资料工具</h2>
          <p>解析、OCR 与检索检查，按需要独立运行。</p>
        </div>
      </div>
      <MaterialAutomation pid={pid} tick={tick} act={act} />
      <RetrievalEvaluation pid={pid} tick={tick} act={act} />
    </div>
  );
}

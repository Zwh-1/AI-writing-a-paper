import { useState } from "react";
import { FileText } from "lucide-react";
import AIAutopilot from "../AIAutopilot";
import AutomaticRuleReview from "../AutomaticRuleReview";
import { AIRuleExtraction } from "../Completion";
import { send } from "../client";
import type { Row, WorkspaceAction } from "../workspace/types";

type Props = {
  pid: string;
  tick: number;
  data: Row | null;
  busy: string;
  refresh: () => void;
  act: WorkspaceAction;
  run: (key: string, work: () => Promise<any>, message: string) => Promise<any>;
  onRule: (id: string) => void;
  onSource: (rule: Row) => Promise<void>;
  onMaterials: () => void;
};
export default function RuleAutomationPanel(p: Props) {
  const [advanced, setAdvanced] = useState(false);
  return (
    <>
      <AIAutopilot
        pid={p.pid}
        tick={p.tick}
        onUpdated={p.refresh}
        scope="rules"
        onRule={p.data ? p.onRule : undefined}
        onOtherDomain={p.onMaterials}
      />
      <details
        className="workspace-details autopilot-manual-tools"
        onToggle={(event) => setAdvanced(event.currentTarget.open)}
      >
        <summary>
          <strong>高级整理与独立审核工具</strong><span>保留原有独立流程</span>
        </summary>
        {advanced && (
          <>
            <div className="rules-section-intro">
              <div>
                <h2>从材料到可用规则</h2>
                <p>需要单独整理或复核时，在这里选择范围与处理方式。</p>
              </div>
              <button
                className="btn"
                disabled={!!p.busy}
                onClick={() =>
                  p.run(
                    "extract",
                    () => send(`/projects/${p.pid}/rules/extract`, {}),
                    "本地候选整理已排队，结果显示在整理任务中",
                  )
                }
              >
                <FileText size={15} />
                {p.busy === "extract" ? "正在提交…" : "从课件整理候选"}
              </button>
            </div>
            <div className="rules-automation-grid">
              <AIRuleExtraction
                pid={p.pid}
                tick={p.tick}
                act={p.act}
                refresh={p.refresh}
                onRuleSource={p.onSource}
              />
              <AutomaticRuleReview
                pid={p.pid}
                data={p.data}
                busy={!!p.busy}
                run={p.run}
              />
            </div>
          </>
        )}
      </details>
    </>
  );
}

import { Sparkles } from "lucide-react";
import {
  AIAutopilotProvider,
  useAIAutopilot,
  useOptionalAIAutopilot,
  type AIDomain,
} from "./useAIAutopilot";
import AutopilotCoverage from "./AutopilotCoverage";
import AutopilotControls from "./AutopilotControls";
import AutopilotExceptions from "./AutopilotExceptions";
import { WorkspaceNotice } from "../workspace/WorkspaceChrome";
import "../aiAutopilot.css";

type Props = {
  pid: string;
  tick: number;
  onUpdated: () => void;
  onRule?: (id: string) => void;
  onRuleSource?: (id: string) => void;
  onMaterial?: (id: string) => void;
  scope?: AIDomain;
  onOtherDomain?: () => void;
};
function Panel(props: Props) {
  const { data, error, load } = useAIAutopilot();
  return (
    <section className="panel ai-autopilot" aria-label="资料与规则AI托管">
      <header className="autopilot-heading">
        <div className="autopilot-title">
          <span>
            <Sparkles size={21} />
          </span>
          <div>
            <h2>资料与规则 · AI 托管</h2>
            <p>
              从解析、总结到用途判断与规则发布，持续提高可自动完成的覆盖率。
            </p>
          </div>
        </div>
        <span className="autopilot-target">AI 优先处理 · 人工聚焦例外</span>
      </header>
      <div className="workspace-workflow" aria-label="AI托管流程">
        {["解析原件", "总结资料", "判断用途", "整理规则", "检查并发布"].map(
          (step, index) => (
            <span key={step}>
              <i>{index + 1}</i>
              {step}
              {index < 4 && <span aria-hidden="true">›</span>}
            </span>
          ),
        )}
      </div>
      <WorkspaceNotice
        error={error}
        loading={!data && !error}
        retry={load}
        label="正在读取自动处理覆盖情况…"
      />
      {data && (
        <>
          <AutopilotCoverage />
          <AutopilotControls />
          <AutopilotExceptions
            scope={props.scope}
            onRule={props.onRule}
            onRuleSource={props.onRuleSource}
            onMaterial={props.onMaterial}
            onOtherDomain={props.onOtherDomain}
          />
        </>
      )}
    </section>
  );
}
export default function AIAutopilot(props: Props) {
  const shared = useOptionalAIAutopilot();
  return shared ? (
    <Panel {...props} />
  ) : (
    <AIAutopilotProvider
      key={props.pid}
      pid={props.pid}
      tick={props.tick}
      onUpdated={props.onUpdated}
    >
      <Panel {...props} />
    </AIAutopilotProvider>
  );
}

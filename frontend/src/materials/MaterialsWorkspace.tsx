import {
  Archive,
  ArrowUpRight,
  BookOpen,
  FolderOpen,
  Plus,
  ScanText,
  Settings2,
  Sparkles,
} from "lucide-react";
import { send } from "../client";
import AIAutopilot from "../AIAutopilot";
import { AIAutopilotProvider } from "../ai/useAIAutopilot";
import AutopilotOverview from "../ai/AutopilotOverview";
import ProjectOriginalReview from "../ProjectOriginalReview";
import {
  WorkspaceHeader,
  WorkspaceTabs,
  WorkspaceNotice,
  showWorkspacePanel,
} from "../workspace/WorkspaceChrome";
import type { SourceRenderer, WorkspaceAction } from "../workspace/types";
import {
  useMaterialsWorkspace,
  type MaterialsView,
} from "./useMaterialsWorkspace";
import MaterialLibrary from "./MaterialLibrary";
import MaterialUploadDialog from "./MaterialUploadDialog";
import { MaterialSummaryTools, MaterialAdvancedTools } from "./MaterialTools";
import "./materialsWorkspace.css";

type Props = {
  pid: string;
  tick: number;
  act: WorkspaceAction;
  refresh: () => void;
  notify: (message: string, error?: boolean) => void;
  onRules: () => void;
  renderSource: SourceRenderer;
};
const tabs = [
  { key: "library", label: "资料库", icon: BookOpen },
  { key: "automation", label: "AI 处理", icon: Sparkles },
  { key: "originals", label: "原文核对", icon: ScanText },
  { key: "advanced", label: "高级工具", icon: Settings2 },
] as const;
function Workspace({
  pid,
  tick,
  act,
  refresh,
  notify,
  onRules,
  renderSource,
}: Props) {
  const c = useMaterialsWorkspace(pid, tick, act);
  const open = (id: string, page?: number) => {
    const file = c.files.find((item) => item.id === id);
    if (file)
      c.setSelected({ ...file, ...(page ? { initialPage: page } : {}) });
    else notify("资料尚未加载或已变化，请刷新后重试。", true);
  };
  const s = c.summary;
  return (
    <div className="materials-workspace">
      <WorkspaceHeader
        section="MATERIALS / 资料"
        title="资料中心"
        description="原件、AI 总结与研究依据集中管理，让资料自动进入写作流程。"
        actions={
          <>
            <button
              className="btn"
              disabled={!!c.busy}
              onClick={() =>
                c.run(
                  "import",
                  () => send(`/projects/${pid}/imports/local`, {}),
                  "已导入“格式”目录，解析在后台进行",
                )
              }
            >
              <FolderOpen size={15} />
              导入现有格式目录
            </button>
            <button className="btn primary" onClick={() => c.setUpload(true)}>
              <Plus size={16} />
              添加资料
            </button>
          </>
        }
      />
      <AutopilotOverview
        domain="materials"
        onManage={() => {
          c.setView("automation");
          showWorkspacePanel("materials-panel-automation");
        }}
      />
      <div className="material-workspace-stats" aria-label="资料概况">
        <button onClick={() => c.setView("library")}>
          <span>在用资料</span>
          <strong>
            {c.data ? s.active : "—"}
            <small>份</small>
          </strong>
          <p>保留原件与出处</p>
        </button>
        <button onClick={() => c.setView("library")}>
          <span>已有 AI 总结</span>
          <strong>
            {c.data ? s.completed : "—"}
            <small>份</small>
          </strong>
          <p>概括、分点与主题</p>
        </button>
        <button onClick={() => c.setView("originals")}>
          <span>原文范围</span>
          <strong>
            {c.data ? s.pages.toLocaleString() : "—"}
            <small>页 / 片段</small>
          </strong>
          <p>随时回到原件核对</p>
        </button>
        <button onClick={() => c.setView("automation")}>
          <span>解析或总结异常</span>
          <strong>
            {c.data ? s.needsAttention : "—"}
            <small>份</small>
          </strong>
          <p>查看处理原因</p>
        </button>
      </div>
      <WorkspaceTabs<MaterialsView>
        id="materials"
        label="资料工作区"
        tabs={tabs}
        value={c.view}
        onChange={c.setView}
      />
      <WorkspaceNotice
        error={c.error}
        loading={!c.data && !c.error}
        retry={c.load}
        label="正在加载项目资料…"
      />
      <section
        id={`materials-panel-${c.view}`}
        role="tabpanel"
        aria-labelledby={`materials-tab-${c.view}`}
      >
        {c.view === "library" && c.data && (
          <MaterialLibrary
            pid={pid}
            files={c.files}
            busy={c.busy}
            onOpen={open}
            onPatch={c.patch}
            onUpload={() => c.setUpload(true)}
          />
        )}
        {c.view === "automation" && (
          <>
            <AIAutopilot
              pid={pid}
              tick={tick}
              onUpdated={refresh}
              scope="materials"
              onMaterial={c.data ? open : undefined}
              onOtherDomain={onRules}
            />
            <MaterialSummaryTools
              pid={pid}
              tick={tick}
              refresh={refresh}
              onOpen={open}
            />
          </>
        )}
        {c.view === "originals" && (
          <section
            className="panel material-health"
            data-testid="material-health"
          >
            <div className="workspace-surface-intro">
              <div>
                <h2>原文与解析覆盖</h2>
                <p>AI 自动处理与人工原文核对分别记录，保留每一次检查的依据。</p>
              </div>
              <button
                className="btn"
                disabled={!!c.busy}
                onClick={() =>
                  c.run(
                    "repair",
                    () => send(`/projects/${pid}/materials/repair`, {}),
                    "本地补解析已排队，进度可在任务中心查看；不调用模型 API",
                  )
                }
              >
                {c.busy === "repair" ? "正在提交…" : "修复解析缺项"}
              </button>
            </div>
            <div className="material-original-stats">
              <span>在用资料 {s.active} 份</span>
              <span>待补图片识别 {s.ocrPending} 页</span>
              <span>识别缺项 {s.ocrFailed} 页</span>
              <span>待核对原文 {s.unreviewed} 页</span>
            </div>
            <ProjectOriginalReview
              key={pid}
              pid={pid}
              tick={tick}
              act={act}
              onRefresh={refresh}
            />
          </section>
        )}
        {c.view === "advanced" && (
          <MaterialAdvancedTools pid={pid} tick={tick} act={act} />
        )}
      </section>
      <footer className="material-workspace-footer">
        <span>
          <Archive size={13} />
          原件保留在本机，资料用途与使用范围可随时调整。
        </span>
        <button className="text-button" onClick={onRules}>
          前往论文规则
          <ArrowUpRight size={13} />
        </button>
      </footer>
      {c.upload && (
        <MaterialUploadDialog
          pid={pid}
          act={act}
          onClose={() => c.setUpload(false)}
        />
      )}
      {c.selected && renderSource(c.selected, () => c.setSelected(null))}
    </div>
  );
}
export default function MaterialsWorkspace(props: Props) {
  return (
    <AIAutopilotProvider
      key={props.pid}
      pid={props.pid}
      tick={props.tick}
      onUpdated={props.refresh}
    >
      <Workspace {...props} />
    </AIAutopilotProvider>
  );
}

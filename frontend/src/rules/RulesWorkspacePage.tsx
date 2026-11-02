import {
  ArrowUpRight,
  CopyCheck,
  ListChecks,
  Plus,
  ShieldCheck,
  Sparkles,
  FileText,
  BookOpen,
} from "lucide-react";
import { lazy, Suspense } from "react";
import { send } from "../client";
import { RuleExecutionPanel } from "../Completion";
import { AIAutopilotProvider } from "../ai/useAIAutopilot";
import AutopilotOverview from "../ai/AutopilotOverview";
import RuleCatalog from "../RuleCatalog";
import RuleIssues from "../RuleIssues";
import RulePublication from "../RulePublication";
import RuleEditor from "../RuleEditor";
import { confirmSelectedRules } from "../ruleBatchConfirmation";
import {
  WorkspaceHeader,
  WorkspaceTabs,
  WorkspaceNotice,
  showWorkspacePanel,
} from "../workspace/WorkspaceChrome";
import type { Row, SourceRenderer, WorkspaceAction } from "../workspace/types";
import { useRulesWorkspace, type RulesView } from "./useRulesWorkspace";
import RulesOverview from "./RulesOverview";
import RuleAutomationPanel from "./RuleAutomationPanel";
import RuleSourceDialog from "./RuleSourceDialog";
import RuleRebuildPanel from "./RuleRebuildPanel";
import "../rulesWorkspace.css";
import {
  SourceWorkspaceNavigation,
  SourceFocus,
  SourceFlowPanel,
  SourceWorkspaceData,
} from "../workspace/SourceWorkspace";
import { navigateSources } from "../workspace/sourceNavigation";
const GuidanceKnowledge = lazy(
  () => import("../references/GraphWikiWorkspace"),
);

type Props = {
  pid: string;
  tick: number;
  act: WorkspaceAction;
  refresh: () => void;
  onMaterials: () => void;
  onTasks: () => void;
  renderSource: SourceRenderer;
};
const tabs = [
  { key: "catalog", label: "条款管理", icon: ListChecks },
  { key: "sources", label: "来源材料", icon: FileText },
  { key: "automation", label: "整理与审核", icon: Sparkles },
  { key: "issues", label: "重复与冲突", icon: CopyCheck },
  { key: "publication", label: "发布与设置", icon: ShieldCheck },
  { key: "knowledge", label: "指导与示例知识", icon: BookOpen },
] as const;
function Workspace(p: Props) {
  const c = useRulesWorkspace(p.pid, p.tick, p.act),
    all: Row[] = c.data?.rules ?? [];
  const onRule = (id: string) => {
    const rule = all.find((item) => item.id === id);
    if (rule) c.setEditing(rule);
  };
  const blocker = (item: Row) => {
    if (
      [
        "RULE_CONFLICT",
        "RULE_AUTOMATIC_CONFLICTS",
        "RULE_DUPLICATES",
        "RULE_CONFIRMATION_REQUIRED",
        "RULE_MACHINE_REVIEW_STALE",
        "RULE_SOURCE_INACTIVE",
      ].includes(item.code)
    )
      c.onBlocker(item);
    else p.onMaterials();
  };
  return (
    <div className="rules-workspace" aria-busy={!c.data && !c.error}>
      <WorkspaceHeader
        section="RULES / 规则"
        title="论文规则工作台"
        description="AI 整理适用要求，原文与判断记录随时可查，生效规则自动用于写作。"
        actions={
          <>
            <button className="btn" onClick={() => c.setView("automation")}>
              <Sparkles size={15} />
              整理材料
            </button>
            <button className="btn" onClick={() => c.setEditing({})}>
              <Plus size={15} />
              手工添加
            </button>
            <button
              className="btn primary"
              disabled={!c.data || !!c.busy}
              onClick={() => c.setView("publication")}
            >
              <ShieldCheck size={15} />
              检查并发布
            </button>
          </>
        }
      />
      <SourceWorkspaceNavigation pid={p.pid} tick={p.tick} current="rules" />
      <SourceFocus
        pid={p.pid}
        tick={p.tick}
        fileId={c.route.location.fileId}
        onClear={() => c.route.update({ fileId: undefined })}
      >
        <button
          className="btn"
          onClick={() =>
            navigateSources(p.pid, "materials", {
              view: "library",
              fileId: c.route.location.fileId,
            })
          }
        >
          管理原件与用途
        </button>
      </SourceFocus>
      {c.view === "catalog" && (
        <details className="source-compact-overview">
          <summary>规则覆盖、审核统计与来源说明</summary>
          <RulesOverview
            data={c.data}
            navigate={c.setView}
            onRecords={c.showRecords}
            onSnapshot={() => c.setSource({ package: c.data?.package })}
          />
        </details>
      )}
      <WorkspaceTabs<RulesView>
        id="rules"
        label="规则工作区"
        tabs={tabs.map((tab) => ({
          ...tab,
          count:
            tab.key === "issues" && c.data
              ? c.data.duplicates.length + c.data.conflicts.length
              : undefined,
        }))}
        value={c.view}
        onChange={c.setView}
      />
      <WorkspaceNotice
        error={c.error || c.sourceError}
        loading={!c.data && !c.error}
        retry={c.load}
        label="正在加载规则与发布条件…"
      />
      <section
        id="rules-panel-catalog"
        role="tabpanel"
        aria-labelledby="rules-tab-catalog"
        hidden={c.view !== "catalog"}
      >
        <RuleCatalog
          key={`${p.pid}:${c.catalogKey}`}
          rules={all}
          loaded={!!c.data}
          filter={c.filter}
          onFilter={c.setFilter}
          busy={!!c.busy}
          sourceBusy={c.sourceBusy}
          initialFileId={
            c.route.location.fileId || c.route.location.sourceFilter
          }
          onFileChange={(fileId) =>
            c.route.update({
              fileId: fileId && fileId !== "manual" ? fileId : undefined,
              sourceFilter: fileId === "manual" ? "manual" : undefined,
            })
          }
          onEdit={c.setEditing}
          onSource={c.openSource}
          onAutomation={() => c.setView("automation")}
          run={c.run}
          onBatch={(ids, state) =>
            state === "confirmed"
              ? c.run(
                  "batch-confirm",
                  () =>
                    confirmSelectedRules(ids, (id) =>
                      send(`/rules/${id}`, { state: "confirmed" }, "PATCH"),
                    ),
                  "",
                )
              : c.run(
                  "batch",
                  () =>
                    send(`/projects/${p.pid}/rules/batch-review`, {
                      rule_ids: ids,
                      state,
                    }),
                  state === "rejected"
                    ? "所选规则已排除，发布后更新生效内容"
                    : "所选规则已恢复待确认",
                )
          }
        />
      </section>
      {c.view === "sources" && (
        <section
          id="rules-panel-sources"
          role="tabpanel"
          aria-labelledby="rules-tab-sources"
        >
          <SourceFlowPanel
            pid={p.pid}
            tick={p.tick}
            scope="rules"
            fileId={c.route.location.fileId}
            onOpen={(fileId, pageNumber) =>
              navigateSources(p.pid, "materials", {
                view: "library",
                fileId,
                pageNumber: pageNumber || 1,
              })
            }
            onChoose={(fileId) => c.route.update({ view: "catalog", fileId })}
          />
        </section>
      )}
      {c.view === "knowledge" && (
        <section
          id="rules-panel-knowledge"
          role="tabpanel"
          aria-labelledby="rules-tab-knowledge"
        >
          <p className="workspace-notice">
            指导与示例知识用于理解要求和语境；纳入写作的约束仍以已发布规则包为准。
          </p>
          <Suspense fallback={<p role="status">正在读取指导知识…</p>}>
            <GuidanceKnowledge
              pid={p.pid}
              tick={p.tick}
              initialNamespace="guidance"
              namespaceOptions={["guidance"]}
              initialQuery={c.route.location.query || ""}
              focusFileId={c.route.location.fileId}
              onSource={(fileId, pageNumber) =>
                navigateSources(p.pid, "materials", {
                  view: "library",
                  fileId,
                  pageNumber: pageNumber || 1,
                })
              }
            />
          </Suspense>
        </section>
      )}
      {c.view === "automation" && (
        <section
          id="rules-panel-automation"
          role="tabpanel"
          aria-labelledby="rules-tab-automation"
        >
          <AIAutopilotProvider pid={p.pid} tick={p.tick} onUpdated={p.refresh}>
            <AutopilotOverview
              domain="rules"
              onManage={() => showWorkspacePanel("rules-panel-automation")}
            />
            <RuleRebuildPanel
              pid={p.pid}
              tick={p.tick}
              onCompleted={p.refresh}
              onSnapshot={(value) => c.setSource({ package: value })}
            />
            <RuleAutomationPanel
              pid={p.pid}
              tick={p.tick}
              data={c.data}
              busy={c.busy}
              refresh={p.refresh}
              act={p.act}
              run={c.run}
              onRule={onRule}
              onSource={c.openSource}
              onMaterials={p.onMaterials}
            />
          </AIAutopilotProvider>
        </section>
      )}
      {c.view === "issues" && (
        <section
          id="rules-panel-issues"
          role="tabpanel"
          aria-labelledby="rules-tab-issues"
        >
          {c.data && (
            <RuleIssues
              pid={p.pid}
              data={c.data}
              busy={c.busy}
              run={c.run}
              onEdit={c.setEditing}
              onSource={c.openSource}
              onScan={c.load}
            />
          )}
        </section>
      )}
      {c.view === "publication" && (
        <section
          id="rules-panel-publication"
          role="tabpanel"
          aria-labelledby="rules-tab-publication"
        >
          {c.data && (
            <RulePublication
              data={c.data}
              busy={!!c.busy}
              onPublish={() =>
                c.run(
                  "publish",
                  () => send(`/projects/${p.pid}/rule-packages`, {}),
                  "规则包已发布，后续写作自动加载",
                )
              }
              onSnapshot={() => c.setSource({ package: c.data?.package })}
              onBlocker={blocker}
              onRecords={c.showRecords}
              onPage={(page) =>
                c.setOriginal({
                  id: page.file_id,
                  name: page.name,
                  project_id: p.pid,
                  extension: page.extension,
                  role: page.role,
                  initialPage: page.page_number,
                })
              }
            />
          )}
          <RuleExecutionPanel pid={p.pid} tick={p.tick} act={p.act} />
        </section>
      )}
      {c.editing && (
        <RuleEditor
          key={c.editing.id ?? "new"}
          rule={c.editing}
          pid={p.pid}
          act={p.act}
          onSaved={c.load}
          onClose={() => c.setEditing(null)}
        />
      )}
      {c.source && (
        <RuleSourceDialog
          source={c.source}
          onClose={() => c.setSource(null)}
          onOriginal={(file) => {
            c.setOriginal(file);
            c.setSource(null);
          }}
        />
      )}
      {c.original &&
        p.renderSource(c.original, () => {
          c.setOriginal(null);
          p.refresh();
          void c.load();
        })}
      <footer className="rule-footer">
        <button className="text-button" onClick={p.onMaterials}>
          资料中心
          <ArrowUpRight size={14} />
        </button>
        <button className="text-button" onClick={p.onTasks}>
          全部任务
          <ArrowUpRight size={14} />
        </button>
      </footer>
    </div>
  );
}
export default function RulesWorkspacePage(p: Props) {
  return (
    <SourceWorkspaceData pid={p.pid} tick={p.tick}>
      <Workspace key={p.pid} {...p} />
    </SourceWorkspaceData>
  );
}

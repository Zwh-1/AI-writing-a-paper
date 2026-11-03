import { apiUrl } from "../client";
import { useEffect, useState } from "react";
import {
  BookOpen,
  FileText,
  Languages,
  Plus,
  X,
  GitBranch,
  ScanText,
  Settings2,
} from "lucide-react";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import ProductLink from '../products/ProductLink';
import type { Row, SourceRenderer, WorkspaceAction } from "../workspace/types";
import {
  ArchiveImportDialog,
  BibliographyImportDialog,
  ReferenceEditor,
  ReferenceLookupDialog,
  TermEditor,
} from "./ReferenceDialogs";
import "./referencesWorkspace.css";
import "./referenceWorkbench.css";
import ReferenceLibrary from "./ReferenceLibrary";
import ConnectedLiterature from "./ConnectedLiterature";
import Reader from "../reading/Reader";
import { StudioChat, StudioNotes } from "../assistant/ChatNotes";
import { useWorkspaceLocation } from "../workspace/WorkspaceContext";
import TermsWorkbench from "./TermsWorkbench";
import { ReferenceTitles } from "./ReferenceWorkbenchParts";
import { KnowledgeBasePanel, KnowledgeDocumentDialog } from "./KnowledgeBase";
import EvidenceKnowledgePanel from "./EvidenceKnowledge";
import DocumentReadingPanel from "./DocumentReading";
import LiteratureRefreshPanel from "./LiteratureRefreshPanel";
import GraphWikiWorkspace from "./GraphWikiWorkspace";
import { MaterialSummary } from "../MaterialSummary";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { WorkspaceHeader, WorkspaceTabs } from "../workspace/WorkspaceChrome";
import {
  SourceWorkspaceNavigation,
  SourceFocus,
  SourceWorkspaceData,
  useSourceMetadata,
  useSourceWorkspaceResource,
} from "../workspace/SourceWorkspace";
import {
  navigateSources,
  useSourceLocation,
} from "../workspace/sourceNavigation";

type ReferenceView =
  | "library"
  | "reading"
  | "evidence"
  | "knowledge"
  | "terms"
  | "manage"
  | "parallel"
  | "intake"
  | "discover"
  | "assistant"
  | "notes";
const referenceTabs = [
  { key: "library", label: "文献书目", icon: BookOpen },
  { key: "reading", label: "问题阅读", icon: FileText },
  { key: "parallel", label: "对照阅读", icon: BookOpen },
  { key: "intake", label: "文献接入", icon: Plus },
  { key: "discover", label: "文献发现", icon: BookOpen },
  { key: "assistant", label: "学术对话", icon: FileText },
  { key: "notes", label: "思维随记", icon: FileText },
  { key: "evidence", label: "原文与证据", icon: ScanText },
  { key: "knowledge", label: "图谱与Wiki", icon: GitBranch },
  { key: "terms", label: "统一术语", icon: Languages },
  { key: "manage", label: "处理与维护", icon: Settings2 },
] as const;

function ReferencesWorkspaceContent({
  pid,
  tick,
  act,
  renderSource,
}: {
  pid: string;
  tick: number;
  act: WorkspaceAction;
  renderSource: SourceRenderer;
}) {
  const route = useSourceLocation(pid, "references", "library");
  const workspace = useWorkspaceLocation(pid, "references");
  useEffect(() => {
    if (workspace.location.view)
      route.update({
        view: workspace.location.view,
        fileId: workspace.location.fileId,
      });
  }, [workspace.location]);
  const sourceMetadata = useSourceMetadata();
  const sourceResource = useSourceWorkspaceResource();
  const files = {
    data: sourceMetadata?.files as Row[] | undefined,
    error: sourceResource?.error || "",
    loading: sourceResource?.loading,
    load: sourceResource?.load || (async () => {}),
  };
  const view: ReferenceView = referenceTabs.some(
    (t) => t.key === route.location.view,
  )
    ? (route.location.view as ReferenceView)
    : "library";
  const [evidenceView, setEvidenceView] = useState<"units" | "search">("units");
  const references = useWorkspaceResource<Row[]>(
      `/projects/${pid}/references`,
      tick,
    ),
    terms = useWorkspaceResource<Row[]>(
      view === "terms" ? `/projects/${pid}/terms` : "",
      tick,
    ),
    knowledge = useWorkspaceResource<Row>(
      view === "evidence" && evidenceView === "search"
        ? `/projects/${pid}/knowledge-base`
        : "",
      tick,
    ),
    archives = useWorkspaceResource<Row[]>(
      view === "library" ? `/projects/${pid}/references/archives` : "",
      tick,
    );
  const [editing, setEditing] = useState<Row | null>(null),
    [knowledgeDocument, setKnowledgeDocument] = useState<Row | null>(null),
    [literatureSummary, setLiteratureSummary] = useState<Row | null>(null),
    [term, setTerm] = useState<Row | null>(null),
    [source, setSource] = useState<Row | null>(null),
    [dialog, setDialog] = useState(""),
    [notice, setNotice] = useState("");
  const latest = archives.data?.[0];
  const error =
    references.error || files.error || terms.error || archives.error;
  return (
    <ReferenceTitles
      references={references.data ?? []}
      files={files.data ?? []}
    >
      <section
        className="references-workspace"
        aria-label="文献与术语"
        data-view={view}
      >
        <WorkspaceHeader
          section="RESEARCH / 文献与知识"
          title="文献与知识工作台"
          description="管理文献，围绕问题阅读，让每一条认识都能回到原文。"
          actions={
            <>
              <ProductLink pid={pid}/>
              <button
                className="btn"
                onClick={() => {
                  route.update({ view: "terms" });
                  setTerm({});
                }}
              >
                <Languages size={17} />
                新增术语
              </button>
              <button
                className="btn primary"
                onClick={() => {
                  route.update({ view: "library" });
                  setEditing({});
                }}
              >
                <Plus size={17} />
                登记文献
              </button>
            </>
          }
        />
        <details className="reference-project-context" open>
          <summary>
            <span>项目资料与关联</span>
            <span>
              {sourceMetadata?.counts.references ?? "—"} 篇文献 ·{" "}
              {sourceMetadata?.counts.active_files ?? "—"} 份原件
              {sourceMetadata?.billing?.paid_blocked
                ? " · 本地功能可用，收费操作暂停"
                : ""}
            </span>
          </summary>
          <SourceWorkspaceNavigation
            pid={pid}
            tick={tick}
            current="references"
          />
        </details>
        <WorkspaceTabs<ReferenceView>
          id="references"
          label="文献知识工作区"
          tabs={referenceTabs}
          value={view}
          onChange={(next) => {
            route.update({ view: next });
          }}
        />
        <SourceFocus
          pid={pid}
          tick={tick}
          fileId={
            ["library", "reading", "evidence", "knowledge"].includes(view)
              ? route.location.fileId
              : undefined
          }
          onClear={() =>
            route.update({ fileId: undefined, pageNumber: undefined })
          }
        >
          <button
            className="btn"
            onClick={() =>
              navigateSources(pid, "materials", {
                view: "library",
                fileId: route.location.fileId,
              })
            }
          >
            回到资料管理
          </button>
        </SourceFocus>
        {notice && (
          <p className="reference-notice" role="status">
            {notice}
            <button
              className="icon-button"
              aria-label="关闭导入结果"
              onClick={() => setNotice("")}
            >
              <X size={16} />
            </button>
          </p>
        )}
        {error && (
          <div className="panel reference-error" role="alert">
            <p>{error}</p>
            <button
              className="btn"
              onClick={() => {
                void references.load();
                void files.load();
                void terms.load();
                void archives.load();
              }}
            >
              重新加载
            </button>
          </div>
        )}
        {view === "intake" && <ConnectedLiterature pid={pid} tick={tick} />}
        {view === "discover" && (
          <ConnectedLiterature pid={pid} tick={tick} discover />
        )}
        {view === "parallel" && (
          <Reader
            pid={pid}
            files={files.data || []}
            initialFile={route.location.fileId}
            initialUnit={workspace.location.unitId}
          />
        )}
        {view === "assistant" && (
          <StudioChat
            pid={pid}
            files={files.data || []}
            assets={[]}
            initialFile={route.location.fileId}
          />
        )}
        {view === "notes" && <StudioNotes pid={pid} tick={tick} />}
        {(view === "knowledge" || view === "manage") && (
          <section
            role="tabpanel"
            id={`references-panel-${view}`}
            aria-labelledby={`references-tab-${view}`}
          >
            <GraphWikiWorkspace
              key={view + (route.location.namespace || "literature")}
              pid={pid}
              tick={tick}
              initialNamespace={route.location.namespace || "literature"}
              initialTab={view === "manage" ? "operations" : "wiki"}
              visibleTabs={
                view === "manage"
                  ? ["build", "maintenance", "evaluation", "operations"]
                  : ["wiki", "graph", "query"]
              }
              initialQuery={route.location.query}
              focusFileId={
                view === "knowledge" ? route.location.fileId : undefined
              }
              onSource={(fileId, page) => {
                const original = files.data?.find((item) => item.id === fileId);
                if (original) setSource({ ...original, initialPage: page });
                else
                  navigateSources(pid, "materials", {
                    view: "library",
                    fileId,
                    pageNumber: page || 1,
                  });
              }}
            />
          </section>
        )}
        {view === "manage" && (
          <LiteratureRefreshPanel
            pid={pid}
            tick={tick}
            onSummary={(file) =>
              setLiteratureSummary(
                files.data?.find((item) => item.id === file.id) || file,
              )
            }
            onCompleted={() => {
              void files.load();
            }}
          />
        )}
        {view === "reading" && (
          <section
            role="tabpanel"
            id="references-panel-reading"
            aria-labelledby="references-tab-reading"
          >
            <DocumentReadingPanel
              pid={pid}
              tick={tick}
              initialFileId={route.location.fileId}
              onSource={(file, page) =>
                setSource({ ...file, initialPage: page })
              }
              onChanged={() => {
                void knowledge.load();
              }}
            />
          </section>
        )}
        {view === "evidence" && (
          <section
            role="tabpanel"
            id="references-panel-evidence"
            aria-labelledby="references-tab-evidence"
          >
            <div
              className="reference-evidence-switch"
              role="group"
              aria-label="原文证据工具"
            >
              <button
                className="btn"
                aria-pressed={evidenceView === "units"}
                onClick={() => setEvidenceView("units")}
              >
                原文单元与支持性
              </button>
              <button
                className="btn"
                aria-pressed={evidenceView === "search"}
                onClick={() => setEvidenceView("search")}
              >
                全文与向量检索
              </button>
            </div>
            {evidenceView === "units" && (
              <EvidenceKnowledgePanel
                pid={pid}
                tick={tick}
                initialFileId={route.location.fileId}
                onFileChange={(id) => route.update({ fileId: id || undefined })}
                onSource={(file, page) =>
                  setSource({ ...file, initialPage: page })
                }
                onImported={() => void act(async () => ({}), "研究文献已导入")}
              />
            )}
            {evidenceView === "search" && (
              <KnowledgeBasePanel
                pid={pid}
                fileId={route.location.fileId}
                knowledge={knowledge}
                files={files.data ?? []}
                act={act}
                onSource={(file, page) =>
                  setSource({ ...file, initialPage: page })
                }
              />
            )}
          </section>
        )}
        {view === "library" && (
          <section
            role="tabpanel"
            id="references-panel-library"
            aria-labelledby="references-tab-library"
          >
            <ReferenceLibrary
              references={references.data ?? []}
              files={files.data ?? []}
              tick={tick}
              fileId={route.location.fileId}
              loading={references.loading}
              filesLoading={!!files.loading}
              onEdit={setEditing}
              onImport={setDialog}
              onSource={(file, page) =>
                setSource({ ...file, initialPage: page || 1 })
              }
              onReading={(file) =>
                route.update({ view: "reading", fileId: file.id })
              }
              onChunks={setKnowledgeDocument}
              onPermissions={(file) =>
                navigateSources(pid, "materials", {
                  view: "library",
                  fileId: file.id,
                })
              }
            />
            {latest && (
              <details className="reference-archive-history research-advanced">
                <summary>
                  文献包导入记录 · {latest.pdf_count} 篇 / {latest.pages} 页
                </summary>
                <p>{latest.filename}</p>
                <p>{latest.warnings?.join(" ")}</p>
                <a
                  className="btn"
                  href={apiUrl(
                    `/projects/${pid}/references/archives/${latest.sha256}/download`,
                  )}
                >
                  下载原始文献包
                </a>
              </details>
            )}
          </section>
        )}
        {view === "terms" && (
          <section
            role="tabpanel"
            id="references-panel-terms"
            aria-labelledby="references-tab-terms"
          >
            <TermsWorkbench
              pid={pid}
              terms={terms.data ?? []}
              loading={terms.loading}
              act={act}
            />
          </section>
        )}
        {editing && (
          <ReferenceEditor
            pid={pid}
            reference={editing}
            files={files.data ?? []}
            act={act}
            onClose={() => setEditing(null)}
          />
        )}
        {term && (
          <TermEditor
            pid={pid}
            term={term}
            act={act}
            onClose={() => setTerm(null)}
          />
        )}
        {dialog === "archive" && (
          <ArchiveImportDialog
            pid={pid}
            act={act}
            onClose={() => setDialog("")}
            onImported={(result) =>
              setNotice(
                `识别 ${result.pdf_count} 篇，新增 ${result.imported} 篇，补充原文关联 ${result.linked} 篇，重复跳过 ${result.unchanged} 篇。原文在本地解析，书目保留待核对。`,
              )
            }
          />
        )}
        {dialog === "bib" && (
          <BibliographyImportDialog
            pid={pid}
            act={act}
            onClose={() => setDialog("")}
          />
        )}
        {dialog === "lookup" && (
          <ReferenceLookupDialog
            pid={pid}
            act={act}
            onClose={() => setDialog("")}
          />
        )}
        {source && renderSource(source, () => setSource(null))}
        {literatureSummary && (
          <WorkspaceDialog
            title="文献内容总结"
            onClose={() => setLiteratureSummary(null)}
            wide
          >
            <p>{literatureSummary.name}</p>
            <MaterialSummary
              file={literatureSummary}
              tick={tick}
              onPage={(page) => {
                setSource({ ...literatureSummary, initialPage: page });
                setLiteratureSummary(null);
              }}
            />
          </WorkspaceDialog>
        )}
        {knowledgeDocument && (
          <KnowledgeDocumentDialog
            pid={pid}
            file={knowledgeDocument}
            tick={tick}
            files={files.data ?? []}
            onClose={() => setKnowledgeDocument(null)}
            onSource={(file, page) => {
              setKnowledgeDocument(null);
              setSource({ ...file, initialPage: page });
            }}
          />
        )}
      </section>
    </ReferenceTitles>
  );
}

export default function ReferencesWorkspace(props: {
  pid: string;
  tick: number;
  act: WorkspaceAction;
  renderSource: SourceRenderer;
}) {
  return (
    <SourceWorkspaceData pid={props.pid} tick={props.tick}>
      <ReferencesWorkspaceContent key={props.pid} {...props} />
    </SourceWorkspaceData>
  );
}

import SentenceLocator from "./SentenceLocator";
import { lazy, Suspense } from "react";
import WorkspaceDialog from "./workspace/WorkspaceDialog";
import OutlineWorkspace from "./outline/OutlineWorkspace";
const RulesWorkspace = lazy(() => import("./RulesWorkspace"));
const MaterialsWorkspace = lazy(() => import("./materials/MaterialsWorkspace"));
const AgentWriting = lazy(() => import("./AgentWriting"));
const ReferencesWorkspace = lazy(() => import("./references/ReferencesWorkspace"));
import GptFallbackSettings from "./GptFallbackSettings";
import { MaterialSummary, MaterialSummarySettings } from "./MaterialSummary";
import AdvancedPage, { StreamPreview } from "./Advanced";
import katex from "katex";
import { version as appVersion } from "../package.json";
import {
  FormatPlans,
  ManualRuleReview,
  CandidateMerge,
  PageSourceTools,
  ReadinessPanel,
  ImpactPanel,
} from "./Completion";
import {
  useState,
  useEffect,
  useRef,
  useCallback,
  createContext,
  useContext,
  type ReactNode,
  type FormEvent,
} from "react";
import { createRoot } from "react-dom/client";
import { useEditor, EditorContent } from "@tiptap/react";
import { Node, Extension, mergeAttributes } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { Table } from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableHeader from "@tiptap/extension-table-header";
import TableCell from "@tiptap/extension-table-cell";
import {
  BookOpen,
  LayoutDashboard,
  FolderOpen,
  ShieldCheck,
  ListTree,
  PenLine,
  FlaskConical,
  Library,
  ClipboardCheck,
  Download,
  Settings,
  Plus,
  ArrowUpRight,
  ArrowRight,
  ChevronRight,
  ChevronLeft,
  X,
  Check,
  LoaderCircle,
  Search,
  Upload,
  FileText,
  Image,
  FileUp,
  RefreshCw,
  LockKeyhole,
  History,
  Sparkles,
  AlertTriangle,
  CheckCircle2,
  Circle,
  Save,
  Bold,
  Italic,
  Heading2,
  List,
  Quote,
  Link,
  Table2,
  Activity,
  KeyRound,
  ExternalLink,
  Square,
  Terminal,
  MessageSquare,
  Languages,
  ArchiveRestore,
  PanelRightClose,
} from "lucide-react";
import "./styles.css";
import { LockedNodes } from "./editorLocks";
type Row = Record<string, any>;
import { api, send, setCsrf } from "./client";
const parse = (x: any, f: any = {}) => {
  try {
    return typeof x === "string" ? JSON.parse(x) : (x ?? f);
  } catch {
    return f;
  }
};
const date = (x: string) =>
  x
    ? new Date(x).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
const kinds: Row = {
  software_system: "系统设计与实现",
  experimental: "实验研究",
  survey: "调研研究",
  review: "文献综述",
};
const roles: Row = {
  rules: "论文规则",
  example: "写作示例",
  research: "研究文献",
  data: "研究数据",
  draft: "已有草稿",
  supervisor: "导师意见",
};
function sourceLocation(source: Row) {
  const ext =
    source.extension ?? source.name?.match(/\.[^.]+$/)?.[0]?.toLowerCase();
  return ext === ".docx"
    ? "段落与表格（非固定页码）"
    : [".txt", ".md", ".csv", ".xlsx"].includes(ext)
      ? "文本片段/工作表 " + source.page_number
      : "第 " + source.page_number + " 页";
}
const stateLabel: Row = {
  queued: "排队中",
  budget_blocked: "预算待核对",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  outcome_unknown: "远端状态未知",
  incomplete: "未完成待恢复",
  ready: "文字已提取",
  parsing: "解析中",
  unreviewed: "待核对",
  reviewed: "已核对",
  not_applicable: "已排除",
  confirmed: "已确认",
  candidate: "待确认",
  rejected: "已排除",
  mandatory: "强制要求",
  recommended: "建议",
  pending: "待判断",
  counterexample: "反例",
  example: "正例",
  planned: "计划中",
  implemented: "已有实现",
  measured: "已有测量",
  unverified: "待核实",
  human_confirmed: "人工确认",
  pass: "通过",
  fail: "未通过",
  unknown: "待判断",
};
type AppState = {
  pid: string;
  project: Row;
  tick: number;
  refresh: () => void;
  act: (fn: () => Promise<any>, success?: string) => Promise<any>;
  notify: (message: string, error?: boolean) => void;
  setPage: (page: string) => void;
  taskOpen: (task: Row) => Promise<void>;
  archiveProject: () => Promise<void>;
};
const AppContext = createContext<AppState | undefined>(undefined);
function useApp() {
  const value = useContext(AppContext);
  if (!value) throw new Error("工作台上下文未就绪");
  return value;
}
function Button({
  children,
  onClick,
  variant = "",
  disabled = false,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: string;
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      className={`btn ${variant} ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Badge({ value, tone }: { value: string; tone?: string }) {
  return (
    <span
      className={`badge ${tone ?? (["succeeded", "ready", "reviewed", "confirmed", "pass", "human_confirmed"].includes(value) ? "green" : ["failed", "fail", "outcome_unknown"].includes(value) ? "red" : ["running", "queued", "candidate", "unreviewed", "unknown", "pending"].includes(value) ? "amber" : "")}`}
    >
      {stateLabel[value] ?? value}
    </span>
  );
}
function Empty({
  title,
  detail,
  children,
}: {
  title: string;
  detail?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <BookOpen size={32} />
      <h3>{title}</h3>
      {detail && <p>{detail}</p>}
      {children}
    </div>
  );
}
function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section className={`modal ${wide ? "wide" : ""}`}>
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose}>
            <X size={19} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function JsonDetail({ value }: { value: unknown }) {
  return <pre className="technical">{JSON.stringify(value, null, 2)}</pre>;
}
function useLoad(path: string | null, refresh = 0) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  // A direct update is newer than any reload that was already in flight.
  const set = useCallback((next: any) => {
    requestVersion.current += 1;
    setData(next);
  }, []);
  useEffect(() => {
    let alive = true;
    const version = ++requestVersion.current;
    setError("");
    if (!path) {
      setData(null);
      return;
    }
    api(path)
      .then((d) => {
        if (alive && version === requestVersion.current) setData(d);
      })
      .catch((e) => {
        if (alive && version === requestVersion.current) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [path, refresh]);
  return { data, error, set };
}
function PageTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <div className="eyebrow">{eyebrow ?? "PAPER WORKBENCH"}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{actions}</div>
    </div>
  );
}
function formValues(e: FormEvent<HTMLFormElement>) {
  e.preventDefault();
  return Object.fromEntries(new FormData(e.currentTarget).entries()) as Record<
    string,
    string
  >;
}
const nav = [
  ["overview", "项目概览", LayoutDashboard],
  ["materials", "资料中心", FolderOpen],
  ["rules", "论文规则", ShieldCheck],
  ["outline", "提纲与计划", ListTree],
  ["editor", "写作工作台", PenLine],
  ["agent-writing", "有据写作", BookOpen],
  ["research", "研究与成果", FlaskConical],
  ["references", "文献与术语", Library],
  ["review", "修改与检查", ClipboardCheck],
  ["exports", "排版与交付", Download],
  ["advanced", "高级研究与协作", Activity],
] as const;
function App() {
  const [ready, setReady] = useState(false),
    [authError, setAuthError] = useState(""),
    [page, setPage] = useState("overview"),
    [pid, setPid] = useState(localStorage.getItem("paper_project") ?? ""),
    [tick, setTick] = useState(0),
    [toast, setToast] = useState(""),
    [toastError, setToastError] = useState(false),
    [newProject, setNewProject] = useState(false),
    [showTasks, setShowTasks] = useState(false),
    [taskDetail, setTaskDetail] = useState<Row | null>(null),
    [allTasks, setAllTasks] = useState<Row[]>([]);
  const lastTaskSig = useRef("");
  const { data: projects, set: setProjects } = useLoad(
    ready ? "/projects" : null,
    tick,
  );
  const { data: project } = useLoad(
    ready && pid ? "/projects/" + pid : null,
    tick,
  );
  const notify = (msg: string, error = false) => {
    setToast(msg);
    setToastError(error);
    setTimeout(() => setToast(""), 6000);
  };
  const act = async (fn: () => Promise<any>, success = "已完成") => {
    try {
      const d = await fn();
      if (success) notify(success);
      setTick((t) => t + 1);
      return d;
    } catch (e) {
      notify((e as Error).message, true);
      return null;
    }
  };
  const refresh = () => setTick((t) => t + 1);
  useEffect(() => {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    send("/session", { token })
      .then((d) => {
        setCsrf(d.csrf);
        history.replaceState(null, "", location.pathname);
        setReady(true);
      })
      .catch((e) => setAuthError(e.message));
  }, []);
  useEffect(() => {
    if (projects?.length && !projects.some((p: Row) => p.id === pid))
      setPid(projects[0].id);
  }, [projects, pid]);
  useEffect(() => {
    localStorage.setItem("paper_project", pid);
  }, [pid]);
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    let polling = false;
    const controller = new AbortController();
    const poll = () => {
      if (polling || !alive) return;
      polling = true;
      api("/tasks?summary=true" + (pid ? "&project_id=" + pid : ""), { signal: controller.signal })
        .then((d) => {
          if (!alive) return;
          setAllTasks(d);
          const sig = d.map((t: Row) => t.id + t.status).join();
          if (lastTaskSig.current !== sig) refresh();
          lastTaskSig.current = sig;
        })
        .catch(() => {})
        .finally(() => { polling = false; });
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => {
      alive = false;
      clearInterval(id);
      controller.abort();
    };
  }, [ready, pid]);
  const taskOpen = async (t: Row) => {
    try {
      setTaskDetail(await api("/tasks/" + t.id));
    } catch (e) {
      notify((e as Error).message, true);
    }
  };
  if (!ready)
    return (
      <div className="welcome-screen">
        <div className="brand-mark">砚</div>
        <h1>砚台 · 论文工作台</h1>
        {authError ? (
          <>
            <p>{authError}</p>
            <p className="muted">
              请运行项目中的「启动论文工作台.ps1」，通过它打开本机页面。
            </p>
            <Button onClick={() => location.reload()}>重新连接</Button>
          </>
        ) : (
          <>
            <LoaderCircle className="spin" />
            <p>正在连接本机项目库…</p>
          </>
        )}
      </div>
    );
  const archiveProject = async () => {
    if (!pid) return;
    if (
      !confirm(
        "归档会从当前列表隐藏项目，资料与版本保留；可在设置页恢复。确认归档？",
      )
    )
      return;
    const d = await act(
      () => send("/projects/" + pid, { archived: 1 }, "PATCH"),
      "项目已归档，可在设置页恢复",
    );
    if (d) {
      setProjects((items: Row[] | null) =>
        (items ?? []).filter((p: Row) => p.id !== pid),
      );
      setPid("");
      setPage("overview");
    }
  };
  const ctx: AppState = {
    pid,
    project: project ?? {},
    tick,
    refresh,
    act,
    notify,
    setPage,
    taskOpen,
    archiveProject,
  };
  return (
    <AppContext.Provider value={ctx}>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark">砚</div>
            <div>
              <strong>砚台</strong>
              <small>论文工作台</small>
            </div>
            <span className="local-tag">LOCAL</span>
          </div>
          <div className="project-switch">
            <span>当前论文</span>
            <select
              aria-label="切换论文项目"
              value={pid}
              onChange={(e) => setPid(e.target.value)}
            >
              <option value="">选择项目</option>
              {projects?.map((p: Row) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
            <button onClick={() => setNewProject(true)}>
              <Plus size={14} />
              新建论文项目
            </button>
          </div>
          <div className="nav-label">创作空间</div>
          <nav>
            {nav.map(([id, label, Icon]) => (
              <button
                key={id}
                className={page === id ? "active" : ""}
                onClick={() => setPage(id)}
              >
                <Icon size={18} />
                {label}
                {page === id && <span className="nav-dot" />}
              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="storage-note">
              <span className="status-dot" />
              <div>
                资料保存在本机<small>模型调用按设置发送资料</small>
              </div>
            </div>
            <button
              className={page === "settings" ? "active" : ""}
              onClick={() => setPage("settings")}
            >
              <Settings size={18} />
              接口与设置
            </button>
          </div>
        </aside>
        <div className="main-shell">
          <header className="topbar">
            <div className="breadcrumb">
              我的研究空间
              <ChevronRight size={14} />
              <span>
                {page === "settings"
                  ? "接口与设置"
                  : nav.find((x) => x[0] === page)?.[1]}
              </span>
            </div>
            <div className="topbar-right">
              <button
                className="task-trigger"
                onClick={() => setShowTasks(true)}
              >
                <Activity size={16} />
                任务中心
                {allTasks.some((t) =>
                  ["running", "queued"].includes(t.status),
                ) && <i />}
              </button>
              <span className="avatar">研</span>
            </div>
          </header>
          <main className={page === "outline" ? "outline-main" : page === "references" ? "references-main" : undefined}>
            {page === "settings" ? (
              <SettingsPage />
            ) : !project || project.id !== pid ? (
              <Empty
                title="从一个论文项目开始"
                detail="建立研究范围，导入规则与参考资料，再逐节完成写作。"
              >
                <Button variant="primary" onClick={() => setNewProject(true)}>
                  <Plus size={16} />
                  新建论文项目
                </Button>
              </Empty>
            ) : (
              <>
                {page === "overview" && <Overview />}
                {page === "materials" && <Materials />}
                {page === "rules" && <Rules />}
                {page === "outline" && <Outline />}
                {page === "editor" && <Writing />}
                {page === "agent-writing" && <Suspense fallback={<p>正在打开有据写作…</p>}><AgentWriting pid={pid} tick={tick} onOpenEditor={(sectionId)=>{localStorage.setItem("paper_section",sectionId);setPage("editor");}} onOpenResearch={()=>setPage("research")} /></Suspense>}
                {page === "research" && <Research />}
                {page === "references" && <References />}
                {page === "review" && <Review />}
                {page === "exports" && <Exports />}
                {page === "advanced" && <AdvancedPage pid={pid} tick={tick} onProjectImported={(id)=>{setPid(id);refresh();}} />}
              </>
            )}
          </main>
          <footer className="app-footer">
            <span>
              <span className="status-dot" />
              本机服务已连接
            </span>
            <span>内容有依据 · 修改可回退 · 规则可核对</span>
            <span>砚台 v{appVersion}</span>
          </footer>
        </div>
      </div>
      {toast && (
        <div role="status" className={`toast ${toastError ? "error" : ""}`}>
          {toastError ? (
            <AlertTriangle size={18} />
          ) : (
            <CheckCircle2 size={18} />
          )}
          <span>{toast}</span>
          <button onClick={() => setToast("")}>
            <X size={16} />
          </button>
        </div>
      )}
      {newProject && (
        <Modal title="建立新的论文项目" onClose={() => setNewProject(false)}>
          <form
            onSubmit={async (e) => {
              const f = formValues(e);
              const d = await act(
                () =>
                  send("/projects", {
                    ...f,
                    target_words: Number(f.target_words),
                    budget: Number(f.budget),
                  }),
                "论文项目已建立",
              );
              if (d) {
                setProjects((existing: Row[] | null) => [
                  d,
                  ...(existing ?? []).filter((p: Row) => p.id !== d.id),
                ]);
                setPid(d.id);
                setPage("overview");
                setNewProject(false);
              }
            }}
          >
            <Field label="论文题目或项目名称">
              <input
                autoFocus
                name="title"
                required
                placeholder="例如：社区服务管理系统的设计与实现"
              />
            </Field>
            <Field label="论文类型">
              <select name="paper_type">
                {Object.entries(kinds).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <div className="form-grid">
              <Field label="目标篇幅">
                <input
                  name="target_words"
                  type="number"
                  min="500"
                  defaultValue="15000"
                />
              </Field>
              <Field label="API 项目预算（服务计价币种）">
                <input
                  name="budget"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue="20"
                />
              </Field>
            </div>
            <Button type="submit" variant="primary">
              建立项目
              <ArrowRight size={16} />
            </Button>
          </form>
        </Modal>
      )}
      {showTasks && (
        <Modal title="任务中心" wide onClose={() => setShowTasks(false)}>
          {allTasks.length ? (
            allTasks.map((t) => (
              <div className="task-row" key={t.id}>
                <div className="task-icon">
                  {t.status === "running" ? (
                    <LoaderCircle className="spin" size={18} />
                  ) : (
                    <FileText size={18} />
                  )}
                </div>
                <div className="grow">
                  <strong>
                    {(
                      {
                        advanced_index:"建立语义索引",advanced_benchmark:"独立检索评测",advanced_statistics:"统计分析",advanced_office:"原版式渲染",advanced_screenshot:"实际系统截图",advanced_execute:"隔离执行研究代码",advanced_tables:"续表候选整理",advanced_objects:"复杂对象提取",advanced_model_download:"安装本地模型",advanced_http_test:"实际HTTP检查",advanced_sandbox_install:"准备代码执行镜像",
                        parse: "解析资料",
                        extract_rules: "整理规则",
                        write: "论文写作",
                        review: "论文检查",
                        export: "导出论文",
                        backup: "创建备份",
                        ocr: "API图片识别",
                        ocr_local: "本地页面OCR",
                        ocr_local_batch: "本地批量OCR",
                        rules_ai: "AI结构化规则整理",
                        materials_rules_autopilot: "资料与规则 AI 托管",
                        outline_ai: "规划提纲",
                        knowledge_index: "更新文献知识库",
                        provider_probe: "验证模型生成",
                        provider_image_probe: "验证模型图片识别",
                      } as Row
                    )[t.kind] ?? t.kind}
                  </strong>
                  <small>
                    {t.phase || "等待执行"} · {date(t.created_at)}
                  </small>
                  {t.error && <p className="error-text">{t.error}</p>}
                </div>
                <Badge value={t.status} />
                <Button onClick={() => taskOpen(t)}>查看</Button>
                {["queued", "running"].includes(t.status) && (
                  <Button
                    onClick={() =>
                      act(
                        () => send("/tasks/" + t.id + "/cancel"),
                        "已请求取消",
                      )
                    }
                  >
                    <Square size={13} />
                  </Button>
                )}
                {["failed", "cancelled", "outcome_unknown"].includes(
                  t.status,
                ) && (
                  <Button
                    onClick={() => {
                      if (
                        (!["outcome_unknown", "incomplete"].includes(
                          t.status,
                        ) &&
                          ![
                            "write",
                            "ocr",
                            "outline_ai",
                            "semantic_review",
                            "support_check",
                            "provider_probe",
                          ].includes(t.kind)) ||
                        confirm(
                          "远端可能已执行，重试可能重复计费。确认已核实并继续？",
                        )
                      )
                        act(
                          () =>
                            send("/tasks/" + t.id + "/retry", {
                              acknowledge_possible_duplicate_charge: true,
                            }),
                          "已重新排队",
                        );
                    }}
                  >
                    重试
                  </Button>
                )}
                {["failed", "cancelled", "outcome_unknown", "incomplete"].includes(t.status) && ["write", "rules_ai", "outline_ai", "semantic_review", "support_check", "ocr", "material_summary", "provider_probe", "provider_image_probe"].includes(t.kind) && (
                  <Button onClick={() => {
                    if (!confirm("确认改用 GPT 重试？如果此前远端已经执行，可能产生重复费用；请先核实账单。")) return;
                    act(() => send("/tasks/" + t.id + "/retry", {provider_profile:"gpt",acknowledge_possible_duplicate_charge:true}), "已使用 GPT 重新排队");
                  }}>用 GPT 重试</Button>
                )}
              </div>
            ))
          ) : (
            <Empty title="还没有后台任务" />
          )}
        </Modal>
      )}
      {taskDetail && (
        <TaskDetail task={taskDetail} onClose={() => setTaskDetail(null)} />
      )}
    </AppContext.Provider>
  );
}
function Overview() {
  const { project, pid, tick, setPage, act, archiveProject } = useApp();
  const [editing, setEditing] = useState(false);
  const { data: sections } = useLoad("/projects/" + pid + "/sections", tick);
  const stats = project.stats ?? {};
  const steps: [string, string, boolean][] = [
    ["materials", "整理资料", stats.files > 0],
    ["rules", "确认论文规则", !!project.rule_package_id],
    ["outline", "规划章节", stats.sections > 0],
    ["editor", "逐节写作", project.word_count > 0],
    ["review", "核查与交付", false],
  ];
  return (
    <>
      <PageTitle
        eyebrow="YOUR RESEARCH, IN ONE PLACE"
        title="把研究，写成有依据的论文。"
        description="先厘清要求与材料，再一步步完成论证、修改与交付。"
        actions={
          <>
            <Button onClick={archiveProject}>归档项目</Button>
            <Button onClick={() => setEditing(true)}>
              <PenLine size={15} />
              编辑研究简报
            </Button>
          </>
        }
      />
      <section className="hero">
        <div className="hero-content">
          <span className="hero-kicker">
            <span className="status-dot" />
            当前研究项目
          </span>
          <h2>{project.title}</h2>
          <p>
            {project.brief?.question ||
              "还没有填写研究问题。先用一句话说明：这篇论文具体要解决什么问题？"}
          </p>
          <div className="hero-tags">
            <span>{kinds[project.paper_type] ?? "研究论文"}</span>
            <span>目标 {Number(project.target_words).toLocaleString()} 字</span>
            <span>
              {project.rule_package_id ? "已加载生效规则包" : "论文规则待确认"}
            </span>
          </div>
          <Button
            variant="primary"
            onClick={() =>
              setPage(project.rule_package_id ? "editor" : "materials")
            }
          >
            {project.rule_package_id ? "进入写作工作台" : "开始整理资料"}
            <ArrowUpRight size={16} />
          </Button>
        </div>
        <div className="hero-visual">
          <div className="paper-sheet back" />
          <div className="paper-sheet">
            <div className="paper-seal">研</div>
            <div className="paper-line title" />
            <div className="paper-line short" />
            <div className="paper-section">01 / 研究与依据</div>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className={`paper-line l${i}`} />
            ))}
            <div className="paper-citation">
              <Check size={13} />
              每一处依据，都有来处
            </div>
            <div className="paper-line" />
            <div className="paper-line short" />
          </div>
        </div>
      </section>
      <div className="metric-grid">
        {[
          [FolderOpen, "资料文件", stats.files ?? 0, "原件与版本保存在本机"],
          [
            ShieldCheck,
            "论文规则",
            stats.rules ?? 0,
            project.rule_package_id ? "已有生效规则包" : "等待整理与确认",
          ],
          [
            PenLine,
            "已写篇幅",
            (project.word_count ?? 0).toLocaleString(),
            "中文字符与英文单词合计",
          ],
          [FlaskConical, "研究事实", stats.facts ?? 0, "区分计划、实现与测量"],
        ].map(([Icon, label, value, detail]: any) => (
          <div className="metric-card" key={label}>
            <div className="metric-top">
              <span>{label}</span>
              <Icon size={19} />
            </div>
            <strong>{value}</strong>
            <small>{detail}</small>
          </div>
        ))}
      </div>
      <ReadinessPanel pid={pid} tick={tick} act={act} />
      <div className="two-col">
        <section className="panel">
          <div className="section-heading">
            <h2>论文进程</h2>
            <span>按研究节奏推进</span>
          </div>
          <div className="steps">
            {steps.map(([id, label, done], i) => (
              <button key={String(id)} onClick={() => setPage(id)}>
                <span className={`step-number ${done ? "done" : ""}`}>
                  {done ? <Check size={16} /> : i + 1}
                </span>
                <div>
                  <strong>{label}</strong>
                  <small>{done ? "已具备基础材料" : "待完成"}</small>
                </div>
                <ChevronRight size={16} />
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>接下来值得关注</h2>
            <span>缺口与复查</span>
          </div>
          {!project.rule_package_id && (
            <div className="notice">
              <ShieldCheck size={20} />
              <div>
                <strong>先发布适用的论文规则</strong>
                <p>
                  从 PPT 和规范中整理要求，核对冲突后，后续每次写作自动加载。
                </p>
                <button
                  className="text-button"
                  onClick={() => setPage("rules")}
                >
                  前往规则中心
                  <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}
          {sections
            ?.filter((s: Row) => s.stale)
            .slice(0, 3)
            .map((s: Row) => (
              <div className="notice" key={s.id}>
                <AlertTriangle size={18} />
                <div>
                  <strong>{s.title}</strong>
                  <p>{s.stale_reason}</p>
                </div>
              </div>
            ))}
          {project.findings?.slice(0, 3).map((f: Row) => (
            <div className="notice" key={f.id}>
              <ClipboardCheck size={18} />
              <div>
                <p>{f.message}</p>
              </div>
            </div>
          ))}
          {project.rule_package_id &&
            !project.findings?.length &&
            !sections?.some((s: Row) => s.stale) && (
              <Empty
                title="继续积累你的研究内容"
                detail="记录真实成果，再从材料充分的章节开始写作。"
              />
            )}
        </section>
      </div>
      {editing && (
        <Modal
          title="研究简报与项目信息"
          wide
          onClose={() => setEditing(false)}
        >
          <form
            onSubmit={async (e) => {
              const f = formValues(e);
              const brief = {
                ...project.brief,
                ...Object.fromEntries(
                  Object.entries(f).filter(
                    ([k]) => !["title", "target_words", "budget"].includes(k),
                  ),
                ),
              };
              const d = await act(
                () =>
                  send(
                    "/projects/" + pid,
                    {
                      title: f.title,
                      target_words: Number(f.target_words),
                      budget: Number(f.budget),
                      brief,
                    },
                    "PATCH",
                  ),
                "研究简报已保存，相关章节将提示复查",
              );
              if (d) setEditing(false);
            }}
          >
            <Field label="论文题目">
              <input name="title" defaultValue={project.title} required />
            </Field>
            <div className="form-grid">
              {[
                ["研究问题", "question"],
                ["研究目标", "objective"],
                ["采用的方法", "method"],
                ["研究范围与限制", "scope"],
              ].map(([label, key]) => (
                <Field key={key} label={label}>
                  <textarea
                    name={key}
                    rows={3}
                    defaultValue={project.brief?.[key] ?? ""}
                  />
                </Field>
              ))}
            </div>
            <div className="form-grid">
              {[
                ["作者", "author"],
                ["学校", "school"],
                ["专业", "major"],
                ["指导教师", "supervisor"],
              ].map(([l, k]) => (
                <Field key={k} label={l}>
                  <input name={k} defaultValue={project.brief?.[k] ?? ""} />
                </Field>
              ))}
              <Field label="目标字数">
                <input
                  type="number"
                  name="target_words"
                  defaultValue={project.target_words}
                />
              </Field>
              <Field label="API 项目预算">
                <input
                  type="number"
                  step="0.01"
                  name="budget"
                  defaultValue={project.budget}
                />
              </Field>
            </div>
            <Button type="submit" variant="primary">
              <Save size={15} />
              保存简报
            </Button>
          </form>
        </Modal>
      )}
    </>
  );
}
function Materials() {
  const { pid, tick, act, notify, refresh, setPage } = useApp();
  return <Suspense fallback={<div className="panel muted" role="status">正在打开资料中心…</div>}><MaterialsWorkspace key={pid} pid={pid} tick={tick} act={act} notify={notify} refresh={refresh} onRules={() => setPage("rules")} renderSource={(file, onClose) => <SourceViewer file={file} onClose={onClose} />} /></Suspense>;
}
function SourceViewer({ file, onClose }: { file: Row; onClose: () => void }) {
  const { tick, act, notify } = useApp();
  const { data: pages, set: setPages } = useLoad("/files/" + file.id + "/pages", tick);
  const { data: parseReport, set: setParseReport } = useLoad("/files/" + file.id + "/parse-report", tick);
  const [renderTask, setRenderTask] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);
  const { data: renderJob } = useLoad(renderTask ? "/tasks/" + renderTask : null, tick);
  useEffect(() => { if (renderJob?.status === "succeeded") setShowOriginal(true); }, [renderJob?.status]);
  const [idx, setIdx] = useState((file.initialPage ?? 1) - 1),
    [transcript, setTranscript] = useState(""),
    [reason, setReason] = useState("");
  const p = pages?.[idx];
  const images = parse(p?.images, []);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [documentReviewBusy, setDocumentReviewBusy] = useState(false);
  const reviewInFlight = useRef(false);
  const hasTranscript = Boolean(transcript.trim());
  const reviewComplete = p?.review_state === "reviewed" && !hasTranscript;
  const documentReviewComplete = Boolean(parseReport?.pages) && parseReport.unreviewed_pages === 0;
  useEffect(() => { setTranscript(""); setReason(""); }, [p?.id]);
  return (
    <WorkspaceDialog title={file.name} wide onClose={onClose} closeLabel="关闭资料查看" busy={reviewBusy || documentReviewBusy}>
      <div className="viewer-toolbar">
        <Button onClick={() => act(() => send("/files/" + file.id + "/reparse", {}), "已排队执行本地补解析，保留来源和人工修改")}>补解析 / 重试缺项</Button>
        {file.extension === ".pptx" && <Button disabled={renderJob?.status === "running" || renderJob?.status === "queued"} onClick={async () => { const job = await act(() => send("/projects/" + file.project_id + "/advanced/office", {file_id:file.id}), "原版式预览正在本地准备"); if (job) setRenderTask(job.id); }}>对照整页原件</Button>}
        {file.role === "draft" && (
          <Button
            onClick={() =>
              act(
                () => send("/files/" + file.id + "/import-draft", {}),
                "已有草稿已建立正文版本，请到写作工作台核对引用与图表",
              )
            }
          >
            从此草稿建立正文
          </Button>
        )}
        <Button disabled={documentReviewBusy || reviewBusy || idx === 0} onClick={() => setIdx((i) => i - 1)}>
          <ChevronLeft size={15} />
        </Button>
        <select value={idx} disabled={reviewBusy || documentReviewBusy} onChange={(e) => setIdx(Number(e.target.value))}>
          {pages?.map((p: Row, i: number) => (
            <option key={p.id} value={i}>
              第 {p.page_number} 页 · {p.title.slice(0, 40)}
            </option>
          ))}
        </select>
        <Button
          disabled={documentReviewBusy || reviewBusy || !pages || idx >= pages.length - 1}
          onClick={() => setIdx((i) => i + 1)}
        >
          <ChevronRight size={15} />
        </Button>
        <a className="btn" href={"/api/v1/files/" + file.id + "/download"}>
          <Download size={14} />
          原文件
        </a>
      </div>
      <MaterialSummary file={file} tick={tick} onPage={(num) => { if (reviewInFlight.current) return; const next = pages?.findIndex((page: Row) => page.page_number === num); if (next !== undefined && next >= 0) { setIdx(next); document.querySelector(".source-content")?.scrollIntoView({ behavior: "smooth", block: "start" }); } }} />
      {parseReport && <div className="material-parse-detail" data-testid="parse-report">
        <div className="summary-heading">
          <div>
            <strong>原文提取：{parseReport.label}</strong> · 图片识别完成 {parseReport.ocr_completed_pages} 页，待补 {parseReport.ocr_pending_pages} 页，缺项 {parseReport.ocr_failed_pages} 页。
            <p>原文提取核对 {parseReport.reviewed_pages}/{parseReport.pages} 页{parseReport.excluded_pages > 0 ? "；已排除 " + parseReport.excluded_pages + " 页" : ""}。请对照原页检查数字、否定词、表格和正反例；此进度不代表 AI 总结已审核。</p>
          </div>
          {documentReviewComplete ? (
            <span className="badge green document-review-complete" role="status"><Check size={15} />本文档原文核对已完成</span>
          ) : (
            <Button variant="primary" disabled={documentReviewBusy || reviewBusy || hasTranscript || !pages?.length || file.status !== "ready"} onClick={async () => {
              if (reviewInFlight.current || hasTranscript) return;
              reviewInFlight.current = true;
              setDocumentReviewBusy(true);
              try {
                const result = await act(() => send("/files/" + file.id + "/review", { confirm: true, page_ids: pages.map((page: Row) => page.id) }), "本文档原文核对已完成；AI 总结仍需另行审阅");
                if (result) { setPages(result.pages); setParseReport(result.parse_report); }
              } finally { reviewInFlight.current = false; setDocumentReviewBusy(false); }
            }}>
              {documentReviewBusy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
              {documentReviewBusy ? "正在保存整份文档核对…" : "确认整份文档原文已核对"}
            </Button>
          )}
        </div>
        {!documentReviewComplete && <p className="muted">整份确认会将本文件剩余 {parseReport.unreviewed_pages} 页原文标记为已核对，保留已排除页面，不代表 AI 总结已审核。{hasTranscript ? "请先保存或清空当前页的补充内容。" : ""}</p>}
      </div>}
      {renderJob?.status === "failed" && <p className="error-text">原版式预览失败：{renderJob.error}</p>}
      {p ? (
        <>
          {showOriginal && file.extension === ".pptx" && <img className="material-original-page" alt={"第" + p.page_number + "页原版式预览"} src={"/api/v1/pages/" + p.id + "/office-preview"} onError={() => {setShowOriginal(false);notify("整页预览暂不可用，请查看原文件或重试本地渲染", true);}} />}
          <PageSourceTools key={p.id} pageId={p.id} tick={tick} act={act} />
          <div className="section-heading"><h3>本页原文提取核对</h3></div>
          <div className="viewer-status">
            <Badge value={p.review_state === "reviewed" ? "原文已核对" : p.review_state === "not_applicable" ? "原文页已排除" : "原文待核对"} tone={p.review_state === "reviewed" ? "green" : p.review_state === "not_applicable" ? "" : "amber"} />
            <span>本页识别：{({pending:"待处理",running:"处理中",legacy_ocr:"旧版识别，需补解析",complete:"文字已识别，原文待核对",no_text:"已检查，未识别到文字",failed:"失败",partial:"存在缺项",unavailable:"引擎不可用",not_needed:"原生文字提取",reviewed:"保留原文人工核对结果",excluded:"原文页已人工排除"} as Row)[p.extraction?.ocr_state] ?? "待检查"}</span>
            <span>
              {p.has_visual
                ? "请对照原页核对提取文字与图片中的文字"
                : "请对照原页检查提取文字是否完整、准确"}
            </span>
          </div>
          {!!p.extraction?.errors?.length && <div className="material-parse-errors" role="status">{p.extraction.errors.map((error: string, i: number) => <p key={i}>{error}</p>)}</div>}
          <div className="source-content">
            <h3>{p.title}</h3>
            <pre>
              {p.text || "本页没有可直接提取的文字，请查看图片或原件。"}
            </pre>
            {p.notes && (
              <details>
                <summary>讲者备注</summary>
                <pre>{p.notes}</pre>
              </details>
            )}
            {file.extension === ".pdf" && (
              <img
                className="source-preview"
                alt="PDF原页"
                src={"/api/v1/pages/" + p.id + "/preview"}
              />
            )}
            <div className="source-images">
              {images.map((im: Row) => (
                <a
                  key={im.sha256}
                  href={"/api/v1/images/" + im.sha256}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    loading="lazy"
                    alt="幻灯片原始图片"
                    src={"/api/v1/images/" + im.sha256}
                  />
                </a>
              ))}
            </div>
          </div>
          <Field label="补充原文转录或 OCR 纠错说明" hint="内容将追加到当前页原文，留空则保留现有文字。确认仅记录本页原文核对，不代表 AI 总结已审核。">
            <textarea
              rows={4}
              disabled={reviewBusy || documentReviewBusy}
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="补充遗漏的原文，或注明识别错误及正确内容；保留数字、否定词与正反例标签"
            />
          </Field>
          <div className="actions">
            {reviewComplete ? (
              <span className="badge green source-review-complete" role="status"><Check size={15} />本页原文已核对</span>
            ) : (
              <Button
                variant="primary"
                disabled={reviewBusy || documentReviewBusy}
                onClick={async () => {
                  if (reviewInFlight.current || (p.review_state === "reviewed" && !transcript.trim())) return;
                  reviewInFlight.current = true;
                  setReviewBusy(true);
                  try {
                    const d = await act(
                      () => send("/pages/" + p.id, { review_state: "reviewed", transcript }, "PATCH"),
                      "本页原文已核对；AI 总结仍需另行对照原文审阅",
                    );
                    if (d) {
                      setPages((current: Row[]) => current.map((page: Row) => page.id === d.id ? { ...page, ...d } : page));
                      setTranscript("");
                    }
                  } finally {
                    reviewInFlight.current = false;
                    setReviewBusy(false);
                  }
                }}
              >
                {reviewBusy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
                {reviewBusy ? "正在保存核对…" : p.review_state === "reviewed" ? "保存补充并确认核对" : "确认本页原文已核对"}
              </Button>
            )}
            {reviewComplete && idx < pages.length - 1 && (
              <Button disabled={documentReviewBusy} onClick={() => setIdx((i) => i + 1)}>核对下一页<ChevronRight size={15} /></Button>
            )}
            <Button
              onClick={() =>
                act(
                  () => send("/pages/" + p.id + "/ocr", {}),
                  "已创建视觉识别任务，请在任务中心查看候选并人工核对",
                )
              }
              disabled={documentReviewBusy || reviewBusy || (!images.length && file.extension !== ".pdf")}
            >
              <Sparkles size={15} />
              API 图片识别
            </Button>
            <Button
              disabled={reviewBusy || documentReviewBusy}
              onClick={async () => {
                if (!reason) {
                  notify("排除页面需要填写理由", true);
                  return;
                }
                await act(
                  () =>
                    send(
                      "/pages/" + p.id,
                      { review_state: "not_applicable", review_note: reason },
                      "PATCH",
                    ),
                  "本页已排除",
                );
              }}
            >
              不作为规则来源
            </Button>
          </div>
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="排除理由，例如：封面或与当前论文无关的示例"
            className="mt"
          />
        </>
      ) : (
        <Empty
          title="页面仍在解析"
          detail="可在任务中心查看进度，解析完成后会出现在这里。"
        />
      )}
    </WorkspaceDialog>
  );
}
function Rules() {
  const { pid, tick, act, setPage, refresh } = useApp();
  return <Suspense fallback={<div className="panel muted" role="status">正在打开论文规则…</div>}><RulesWorkspace pid={pid} tick={tick} act={act} refresh={refresh} onMaterials={()=>setPage("materials")} onTasks={()=>document.querySelector<HTMLButtonElement>(".task-trigger")?.click()} renderSource={(file,onClose)=><SourceViewer file={file} onClose={onClose}/>}/></Suspense>;
}

function Outline() {
  const { pid, tick, act, setPage } = useApp();
  return (
    <OutlineWorkspace
      key={pid}
      pid={pid}
      tick={tick}
      act={act}
      onWrite={(sectionId) => {
        localStorage.setItem("paper_section", sectionId);
        setPage("editor");
      }}
    />
  );
}
const StableIds = Extension.create({
  name: "stableIds",
  addGlobalAttributes() {
    return [
      {
        types: [
          "paragraph",
          "heading",
          "blockquote",
          "table",
          "tableRow",
          "tableCell",
          "tableHeader",
          "bulletList",
          "orderedList",
          "listItem",
        ],
        attributes: {
          sourceIds: { default: [], renderHTML: () => ({}) },
          factIds: { default: [], renderHTML: () => ({}) },
          nodeId: {
            default: null,
            parseHTML: (e: HTMLElement) => e.getAttribute("data-node-id"),
            renderHTML: (a: Row) =>
              a.nodeId ? { "data-node-id": a.nodeId } : {},
          },
        },
      },
    ];
  },
});
const Citation = Node.create({
  name: "citation",
  inline: true,
  group: "inline",
  atom: true,
  addAttributes() {
    return { citationId: { default: "" }, nodeId: { default: null } };
  },
  parseHTML() {
    return [{ tag: "span[data-citation]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-citation": HTMLAttributes.citationId,
        class: "citation-node",
      }),
      "[文献]",
    ];
  },
});
let artifactImages: Record<string, string> = {};
const Figure = Node.create({
  name: "figure",
  group: "block",
  atom: true,
  addAttributes() {
    return {
      artifactId: { default: "" },
      caption: { default: "" },
      nodeId: { default: null },
    };
  },
  parseHTML() {
    return [{ tag: "figure[data-artifact]" }];
  },
  renderHTML({ HTMLAttributes }) {
    return [
      "figure",
      { "data-artifact": HTMLAttributes.artifactId },
      [
        "img",
        {
          src: artifactImages[HTMLAttributes.artifactId] ?? "",
          alt: HTMLAttributes.caption,
        },
      ],
      ["figcaption", {}, HTMLAttributes.caption || "研究图表"],
    ];
  },
});
const Formula = Node.create({
  name: "formula",
  group: "block",
  atom: true,
  addAttributes() {
    return { expression: { default: "" }, latex: { default: "" }, omml: { default: "" }, nodeId: { default: null } };
  },
  parseHTML() {
    return [{ tag: "div[data-formula]" }];
  },
  renderHTML({ HTMLAttributes }) {
    const element = document.createElement("div");
    element.setAttribute("data-formula", "true");element.className="formula-node";
    const expression=HTMLAttributes.latex || HTMLAttributes.expression;
    if (expression) katex.render(expression, element, {throwOnError:false,trust:false,displayMode:true});
    else element.textContent="Word 原生复杂公式（导出保留）";
    return element;
  },
});
const Footnote = Node.create({
  name:"footnote",inline:true,group:"inline",atom:true,
  addAttributes(){return {noteText:{default:""},nodeId:{default:null}}},
  parseHTML(){return [{tag:"span[data-footnote]"}]},
  renderHTML({HTMLAttributes}){return ["span",{"data-footnote":"true",class:"footnote-node",title:HTMLAttributes.noteText},"[脚注]"]}
});
function Writing() {
  const { pid, tick, act, notify, setPage, refresh } = useApp();
  const { data: sections } = useLoad("/projects/" + pid + "/sections", tick);
  const [sid, setSid] = useState(localStorage.getItem("paper_section") ?? "");
  const { data: content } = useLoad(
    sid ? "/sections/" + sid + "/content" : null,
    tick,
  );
  const { data: refs } = useLoad("/projects/" + pid + "/references", tick);
  const { data: artifacts } = useLoad("/projects/" + pid + "/artifacts", tick);
  const { data: facts } = useLoad("/projects/" + pid + "/facts", tick);
  const { data: writingLink } = useLoad("/projects/" + pid + "/writing-link", tick);
  const { data: history } = useLoad(
    sid ? "/sections/" + sid + "/revisions" : null,
    tick,
  );
  const [historyOpen, setHistoryOpen] = useState(false),
    [instruction, setInstruction] = useState(""),
    [operation, setOperation] = useState("generate"),
    [saveState, setSaveState] = useState("已保存"),
    [sourceQuery, setSourceQuery] = useState(""),
    [sourceResults, setSourceResults] = useState<Row[]>([]),
    [panel, setPanel] = useState("sources");
  useEffect(() => {
    if (sections?.length && !sections.some((s: Row) => s.id === sid))
      setSid(sections[0].id);
  }, [sections, sid]);
  useEffect(() => {
    localStorage.setItem("paper_section", sid);
  }, [sid]);
  if (!sections?.length)
    return (
      <>
        <PageTitle title="写作工作台" />
        <Empty
          title="先建立提纲，再开始逐节写作"
          detail="章节目标与适用规则能帮助模型聚焦，也便于你核对结果。"
        >
          <Button variant="primary" onClick={() => setPage("outline")}>
            前往提纲计划
            <ArrowRight size={15} />
          </Button>
        </Empty>
      </>
    );
  return (
    <>
      <PageTitle
        title="写作工作台"
        description="让每段论述有依据，让每次修改可追溯。"
        actions={
          <>
            <Button onClick={() => setPage("agent-writing")}><BookOpen size={16} />有据写作</Button>
            <Button onClick={() => { refresh(); setHistoryOpen(true); }}><History size={16} />版本历史</Button>
          </>
        }
      />
      <div className="writing-layout">
        <aside className="chapter-list">
          <div className="section-heading">
            <h3>论文目录</h3>
            <ListTree size={16} />
          </div>
          {sections.map((s: Row, i: number) => (
            <button
              className={s.id === sid ? "selected" : ""}
              key={s.id}
              onClick={() => {
                if (
                  saveState !== "保存中…" &&
                  (saveState !== "有未保存修改" ||
                    confirm("仍有未保存修改，确认切换章节？"))
                )
                  setSid(s.id);
              }}
            >
              <span>{String(i + 1).padStart(2, "0")}</span>
              <div>
                {s.title}
                <small>
                  {s.stale
                    ? "需要复查"
                    : s.current_revision_id
                      ? "已有正文"
                      : "等待写作"}
                </small>
              </div>
              {!!s.stale && <i className="amber-dot" />}
            </button>
          ))}
        </aside>
        <div className="writing-center">
          {content && content.section.id === sid ? (
            <RichEditor
              key={sid}
              content={content}
              refs={refs ?? []}
              artifacts={artifacts ?? []}
              facts={facts ?? []}
              onSaveState={setSaveState}
              onGenerate={async (targetNodeIds: string[] = []) => {
                const latest = await api("/sections/" + sid + "/content");
                await act(
                  () =>
                    send("/sections/" + sid + "/writing-tasks", {
                      operation: latest.section.kind === "abstract_en" ? "translate" : operation,
                      base_revision_id: latest.section.current_revision_id,
                      instruction,
                      target_node_ids: targetNodeIds,
                    }),
                  latest.section.kind === "abstract_en"
                    ? "英文摘要翻译候选已创建；请在任务中心审阅后采用"
                    : "写作任务已创建；完成后在任务中心审阅候选稿",
                );
              }}
              operation={content.section.kind === "abstract_en" ? "translate" : operation}
              setOperation={setOperation}
              instruction={instruction}
              setInstruction={setInstruction}
            />
          ) : (
            <div className="loading">
              <LoaderCircle className="spin" />
              加载章节…
            </div>
          )}
        </div>
        <aside className="evidence-panel">
          {writingLink?.sections?.find((item: Row) => item.section_id === sid) && (() => {
            const current = writingLink.sections.find((item: Row) => item.section_id === sid);
            const linkedFacts = (writingLink.facts ?? []).filter((fact: Row) => current.fact_ids?.includes(fact.id));
            return <section className="writing-link-card">
              <div className="section-heading"><h3>本节研究链路</h3><Badge value={current.stale ? "stale" : current.revision_id ? "draft" : "pending"} /></div>
              <p className="muted small">{current.contract?.goal || "尚未发布章节写作任务"}</p>
              <p className="small">研究事实 {linkedFacts.length} 项 · 任务证据 {current.source_ids?.length ?? 0} 项</p>
              {linkedFacts.map((fact: Row) => <button className="text-button writing-link-fact" key={fact.id} onClick={() => notify(`${fact.label}：${fact.statement}`)}>{fact.label}</button>)}
              <button className="text-button" onClick={() => setPage("agent-writing")}>在有据写作中核验任务 <ArrowUpRight size={14} /></button>
            </section>;
          })()}
          <div className="tabs">
            <button
              className={panel === "sources" ? "active" : ""}
              onClick={() => setPanel("sources")}
            >
              资料依据
            </button>
            <button
              className={panel === "references" ? "active" : ""}
              onClick={() => setPanel("references")}
            >
              文献与资源
            </button>
          </div>
          {panel === "sources" ? (
            <>
              <p className="muted small">
                检索与本节有关的原文。检索相关不等同于论断已核实。
              </p>
              <div className="mini-search">
                <input
                  value={sourceQuery}
                  onChange={(e) => setSourceQuery(e.target.value)}
                  placeholder="关键词或研究问题"
                />
                <button
                  className="icon-button"
                  onClick={async () => {
                    try {
                      setSourceResults(
                        await send("/projects/" + pid + "/search", {
                          query: sourceQuery || content?.section.title,
                          kind: "research",
                        }),
                      );
                    } catch (e) {
                      notify((e as Error).message, true);
                    }
                  }}
                >
                  <Search size={16} />
                </button>
              </div>
              {sourceResults.map((s) => (
                <details className="evidence-card" key={s.id}>
                  <summary>
                    <FileText size={15} />
                    {s.name}
                    <small>{sourceLocation(s)}</small>
                  </summary>
                  <p>{s.text}</p>
                  <Button
                    onClick={() => {
                      window.dispatchEvent(
                        new CustomEvent("paper-attach-source", {
                          detail: { sectionId: sid, sourceId: s.id },
                        }),
                      );
                    }}
                  >
                    关联当前段落
                  </Button>
                  <small>来源 ID：{s.id}</small>
                </details>
              ))}
              {!sourceResults.length && (
                <div className="small-empty">
                  <Search size={24} />
                  <p>检索原文，为本节建立证据。</p>
                </div>
              )}
            </>
          ) : (
            <>
              <h4>可用参考文献</h4>
              {refs?.map((r: Row) => (
                <div className="reference-mini" key={r.id}>
                  {parse(r.metadata).title || "未命名文献"}
                  <small>{parse(r.metadata).authors || "作者待补"}</small>
                </div>
              ))}
              <button
                className="text-button"
                onClick={() => setPage("references")}
              >
                管理文献与术语
                <ArrowUpRight size={14} />
              </button>
              <h4>研究资源</h4>
              {artifacts?.map((a: Row) => (
                <div className="reference-mini" key={a.id}>
                  {a.name}
                  <small>{a.kind}</small>
                </div>
              ))}
            </>
          )}
        </aside>
      </div>
      {historyOpen && (
        <Modal
          title="章节版本与恢复"
          wide
          onClose={() => setHistoryOpen(false)}
        >
          {history?.length ? (
            history.map((r: Row) => (
              <details className="history-item" key={r.id}>
                <summary>
                  <span>
                    {date(r.created_at)} ·{" "}
                    {r.kind === "manual"
                      ? "人工编辑"
                      : r.kind === "accepted"
                        ? "接受模型候选"
                        : "恢复历史版本"}
                  </span>
                  <span>{r.id.slice(0, 10)}…</span>
                </summary>
                <pre>{r.text}</pre>
                <Button
                  onClick={async () => {
                    const latest = await api("/sections/" + sid + "/content");
                    const d = await act(
                      () =>
                        send("/sections/" + sid + "/restore", {
                          revision_id: r.id,
                          base_revision_id: latest.section.current_revision_id,
                        }),
                      "已创建恢复版本",
                    );
                    if (d) setHistoryOpen(false);
                  }}
                >
                  <ArchiveRestore size={15} />
                  从此稿创建恢复版本
                </Button>
              </details>
            ))
          ) : (
            <Empty title="还没有保存版本" />
          )}
        </Modal>
      )}
    </>
  );
}
function RichEditor({
  content,
  refs,
  artifacts,
  facts,
  onSaveState,
  onGenerate,
  operation,
  setOperation,
  instruction,
  setInstruction,
}: any) {
  const { act, notify, pid } = useApp();
  const isEnglishAbstract = content.section.kind === "abstract_en";
  const revision = useRef(content.section.current_revision_id),
    dirty = useRef(false),
    saving = useRef(false),
    generation = useRef(0),
    timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [status, setStatus] = useState("已保存"),
    [refId, setRefId] = useState(""),
    [figureId, setFigureId] = useState(""),
    [factId, setFactId] = useState(""),
    [locks, setLocks] = useState<string[]>(content.locks ?? []);
  const [words, setWords] = useState(0);
  const [selectionOnly, setSelectionOnly] = useState(false);
  const editorRef = useRef<any>(null);
  const lockIdsRef = useRef<string[]>(locks);
  lockIdsRef.current = locks;
  artifactImages = Object.fromEntries(
    artifacts
      .filter((a: Row) => a.sha256)
      .map((a: Row) => [a.id, "/api/v1/images/" + a.sha256]),
  );
  const updateState = (s: string) => {
    setStatus(s);
    onSaveState(s);
  };
  const saveNow = async () => {
    const ed = editorRef.current;
    if (!ed || !dirty.current) return true;
    if (saving.current) return false;
    saving.current = true;
    const version = generation.current;
    const savedSelection = {
      from: ed.state.selection.from,
      to: ed.state.selection.to,
    };
    updateState("保存中…");
    try {
      const d = await send(
        "/sections/" + content.section.id + "/content",
        { document: ed.getJSON(), base_revision_id: revision.current },
        "PUT",
      );
      revision.current = d.id;
      if (generation.current === version) {
        dirty.current = false;
        ed.commands.setContent(parse(d.document), { emitUpdate: false });
        const max = ed.state.doc.content.size;
        ed.commands.setTextSelection({
          from: Math.min(savedSelection.from, max),
          to: Math.min(savedSelection.to, max),
        });
        localStorage.removeItem("paper_emergency_" + content.section.id);
        updateState("已保存");
      } else {
        updateState("有未保存修改");
        timer.current = setTimeout(() => saveNow(), 700);
      }
      return true;
    } catch (e) {
      updateState("保存失败");
      notify((e as Error).message, true);
      return false;
    } finally {
      saving.current = false;
    }
  };
  const editor = useEditor({
    extensions: [
      StarterKit,
      LockedNodes.configure({ getLocks: () => lockIdsRef.current }),
      StableIds,
      Citation,
      Figure,
      Formula,
      Footnote,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: content.document,
    editorProps: {
      attributes: { class: "paper-editor", "aria-label": "论文正文编辑器" },
    },
    onUpdate: ({ editor }) => {
      dirty.current = true;
      try {
        localStorage.setItem(
          "paper_emergency_" + content.section.id,
          JSON.stringify({
            document: editor.getJSON(),
            base_revision_id: revision.current,
            saved_at: Date.now(),
          }),
        );
      } catch {}
      generation.current++;
      updateState("有未保存修改");
      setWords(editor.getText().length);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => saveNow(), 1300);
    },
  });
  editorRef.current = editor;
  useEffect(() => {
    if (editor && !editor.isDestroyed)
      editor.view.dispatch(editor.state.tr.setMeta("locksChanged", Date.now()));
  }, [editor, locks]);
  useEffect(() => {
    if (!editor) return;
    const attach = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail.sectionId !== content.section.id) return;
      if (!editor.isActive("paragraph")) {
        notify("请先把光标放到正文段落内", true);
        return;
      }
      const ids = editor.getAttributes("paragraph").sourceIds ?? [];
      editor
        .chain()
        .focus()
        .updateAttributes("paragraph", {
          sourceIds: Array.from(new Set([...ids, detail.sourceId])),
        })
        .run();
      notify("已将原文依据关联到当前段落");
    };
    window.addEventListener("paper-attach-source", attach);
    return () => window.removeEventListener("paper-attach-source", attach);
  }, [editor]);

  const recoveredDraft = useRef(false);
  useEffect(() => {
    if (!editor || recoveredDraft.current) return;
    recoveredDraft.current = true;
    const raw = localStorage.getItem("paper_emergency_" + content.section.id);
    if (raw) {
      try {
        const draft = JSON.parse(raw);
        if (
          confirm(
            "发现上次未保存到项目库的本机草稿，是否恢复到编辑区？恢复后请核对再保存。",
          )
        ) {
          editor.commands.setContent(draft.document);
          dirty.current = true;
          updateState("有未保存修改");
        } else localStorage.removeItem("paper_emergency_" + content.section.id);
      } catch {}
    }
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [editor]);

  useEffect(() => {
    if (
      content.section.current_revision_id !== revision.current &&
      !dirty.current &&
      !saving.current
    ) {
      editor?.commands.setContent(content.document, { emitUpdate: false });
      revision.current = content.section.current_revision_id;
      setLocks(content.locks ?? []);
    }
  }, [content.section.current_revision_id, editor]);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const lockCurrent = async () => {
    if (!(await saveNow())) return;
    const { from } = editor!.state.selection;
    let nodeId = "";
    editor!.state.doc.nodesBetween(
      from,
      Math.min(from + 1, editor!.state.doc.content.size),
      (n: any) => {
        if (n.type.name === "paragraph" && !nodeId) nodeId = n.attrs.nodeId;
      },
    );
    if (!nodeId) {
      notify("请把光标放在已保存的段落内", true);
      return;
    }
    const updated = locks.includes(nodeId)
      ? locks.filter((x) => x !== nodeId)
      : [...locks, nodeId];
    const d = await act(
      () =>
        send(
          "/sections/" + content.section.id + "/locks",
          { node_ids: updated },
          "PUT",
        ),
      "锁定状态已更新",
    );
    if (d) setLocks(updated);
  };
  return (
    <>
      <div className="editor-heading">
        <div>
          <span className="eyebrow">CHAPTER</span>
          <h2>{content.section.title}</h2>
        </div>
        <span className="save-status">
          <Check size={12} />
          {status}
        </span>
      </div>
      {content.section.stale && (
        <div className="alert compact">
          <AlertTriangle size={15} />
          {content.section.stale_reason}
        </div>
      )}
      <div className="editor-toolbar">
        <button
          title="加粗"
          onClick={() => editor?.chain().focus().toggleBold().run()}
        >
          <Bold size={15} />
        </button>
        <button
          title="斜体"
          onClick={() => editor?.chain().focus().toggleItalic().run()}
        >
          <Italic size={15} />
        </button>
        <button
          title="二级标题"
          onClick={() =>
            editor?.chain().focus().toggleHeading({ level: 2 }).run()
          }
        >
          <Heading2 size={17} />
        </button>
        <button
          title="列表"
          onClick={() => editor?.chain().focus().toggleBulletList().run()}
        >
          <List size={17} />
        </button>
        <button
          title="引文"
          onClick={() => editor?.chain().focus().toggleBlockquote().run()}
        >
          <Quote size={16} />
        </button>
        <button
          title="插入表格"
          onClick={() =>
            editor
              ?.chain()
              .focus()
              .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
              .run()
          }
        >
          <Table2 size={16} />
        </button>
        <button title="锁定或解锁当前段落" onClick={lockCurrent}>
          <LockKeyhole size={16} />
        </button>
        <span className="toolbar-divider" />
        <select
          aria-label="选择引用文献"
          value={refId}
          onChange={(e) => setRefId(e.target.value)}
        >
          <option value="">插入引用…</option>
          {refs.map((r: Row) => (
            <option key={r.id} value={r.id}>
              {parse(r.metadata).title ?? "未命名"}
            </option>
          ))}
        </select>
        <button
          title="插入所选引用"
          disabled={!refId}
          onClick={() =>
            editor
              ?.chain()
              .focus()
              .insertContent({
                type: "citation",
                attrs: { citationId: refId, nodeId: crypto.randomUUID() },
              })
              .run()
          }
        >
          <Link size={16} />
        </button>
        <button title="保存" onClick={() => saveNow()}>
          <Save size={16} />
        </button>
      </div>
      <div className="paper-canvas">
        <SentenceLocator pid={pid} revisionId={revision.current} editor={editor} disabled={dirty.current}/>
        <EditorContent editor={editor} />
      </div>
      <div className="actions" style={{ padding: "12px 20px" }}>
        <Button
          onClick={async () => {
            if (await saveNow())
              await act(
                () =>
                  send("/sections/" + content.section.id + "/reviewed", {
                    revision_id: revision.current,
                    note: "用户在工作台审阅当前版本",
                  }),
                "已记录当前正文版本的人工审阅",
              );
          }}
        >
          记录本节已人工审阅
        </Button>
      </div>
      <div className="editor-stats">
        <span>
          约 {words || content.revision?.text?.length || 0} 字符 · 目标{" "}
          {content.section.target_words}
        </span>
        <span>{locks.length} 个锁定段落</span>
      </div>
      <div className="inline-resource">
        <select
          aria-label="选择关联事实"
          value={factId}
          onChange={(e) => setFactId(e.target.value)}
        >
          <option value="">关联研究事实…</option>
          {facts?.map((f: Row) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
        <Button
          disabled={!factId}
          onClick={() => {
            if (!editor?.isActive("paragraph")) {
              notify("请先将光标放入正文段落", true);
              return;
            }
            const current = editor.getAttributes("paragraph").factIds ?? [];
            editor
              .chain()
              .focus()
              .updateAttributes("paragraph", {
                factIds: Array.from(new Set([...current, factId])),
              })
              .run();
            notify("研究事实已关联到当前段落");
          }}
        >
          关联事实
        </Button>
        <select
          aria-label="选择研究图表"
          value={figureId}
          onChange={(e) => setFigureId(e.target.value)}
        >
          <option value="">选择已上传图表…</option>
          {artifacts
            .filter((a: Row) => a.kind === "figure")
            .map((a: Row) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
        </select>
        <Button
          disabled={!figureId}
          onClick={() => {
            const a = artifacts.find((x: Row) => x.id === figureId);
            editor
              ?.chain()
              .focus()
              .insertContent({
                type: "figure",
                attrs: {
                  artifactId: a.id,
                  caption: a.name,
                  nodeId: crypto.randomUUID(),
                },
              })
              .run();
          }}
        >
          <Image size={15} />
          插图
        </Button>
        <Button
          onClick={async () => {
            const expression = prompt("输入LaTeX公式（转换为可编辑Word公式）");
            if (!expression) return;
            const converted=await act(()=>send("/advanced/math/convert",{latex:expression}),"已转换为原生Word公式");
            if(converted) editor?.chain().focus().insertContent({type:"formula",attrs:{expression,latex:expression,omml:converted.omml,nodeId:crypto.randomUUID()}}).run();
          }}
        >
          公式
        </Button>
        <Button onClick={()=>{const text=prompt("输入当前文字位置的脚注内容");if(text)editor?.chain().focus().insertContent({type:"footnote",attrs:{noteText:text,nodeId:crypto.randomUUID()}}).run()}}>脚注</Button>
      </div>
      <div className="ai-composer">
        <div>
          <Sparkles size={17} />
          <strong>{isEnglishAbstract ? "英文摘要翻译" : "辅助本节写作"}</strong>
          <span>{isEnglishAbstract ? "直接翻译已保存的中文摘要" : "先加载规则，再检索证据"}</span>
        </div>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={isEnglishAbstract ? "可补充专业术语的英文译法或英文表达偏好。" : "例如：结合已有资料补充方案比较，保留所有已确认事实。"}
          rows={3}
        />
        {!isEnglishAbstract && <label className="checkbox">
          <input
            type="checkbox"
            checked={selectionOnly}
            onChange={(e) => setSelectionOnly(e.target.checked)}
          />
          仅改写当前/选中的连续段落，其余正文保持原样
        </label>}
        <div className="composer-bottom">
          <select
            aria-label="写作操作"
            value={operation}
            onChange={(e) => setOperation(e.target.value)}
          >
            {(isEnglishAbstract ? [["translate", "翻译中文摘要"]] : [
              ["generate", "生成本节"],
              ["revise", "定向修改"],
              ["expand", "扩写"],
              ["compress", "压缩"],
              ["translate", "翻译"],
            ]).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <Button
            variant="primary"
            onClick={async () => {
              if (saving.current) {
                notify("请等待保存完成");
                return;
              }
              const selected: string[] = [];
              if (!isEnglishAbstract && selectionOnly && editor) {
                const { from, to } = editor.state.selection;
                editor.state.doc.forEach((node, pos) => {
                  if (
                    node.type.name === "paragraph" &&
                    pos < Math.max(to, from + 1) &&
                    pos + node.nodeSize > from
                  )
                    selected.push(node.attrs.nodeId);
                });
                if (!selected.length) {
                  notify("请先选择要改写的连续正文段落", true);
                  return;
                }
              }
              if (await saveNow()) {
                if (!isEnglishAbstract && selectionOnly && editor) {
                  selected.length = 0;
                  const { from, to } = editor.state.selection;
                  editor.state.doc.forEach((node, pos) => {
                    if (
                      node.type.name === "paragraph" &&
                      pos < Math.max(to, from + 1) &&
                      pos + node.nodeSize > from
                    )
                      selected.push(node.attrs.nodeId);
                  });
                }
                await onGenerate(!isEnglishAbstract && selectionOnly ? selected : []);
              }
            }}
          >
            <Sparkles size={15} />
            {isEnglishAbstract ? "翻译中文摘要" : "生成候选稿"}
          </Button>
        </div>
      </div>
    </>
  );
}
function TaskDetail({ task, onClose }: { task: Row; onClose: () => void }) {
  const { act, refresh, notify } = useApp();
  const [t, setT] = useState(task),
    [selected, setSelected] = useState<string[]>([]);
  const result = t.result ?? {};
  useEffect(() => {
    if (["running", "queued"].includes(t.status)) {
      const id = setInterval(() => api("/tasks/" + t.id).then(setT), 2000);
      return () => clearInterval(id);
    }
  }, [t.id, t.status]);
  const [before, setBefore] = useState("");
  useEffect(() => {
    if (result.section_id)
      api("/sections/" + result.section_id + "/content").then((d) =>
        setBefore(d.revision?.text ?? ""),
      );
  }, [result.section_id]);
  const apply = async (partial = false) => {
    const d = await act(
      () =>
        send("/sections/" + result.section_id + "/accept", {
          task_id: t.id,
          base_revision_id: result.base_revision_id,
          ...(partial ? { node_ids: selected } : {}),
        }),
      "已形成新的正文版本",
    );
    if (d) {
      refresh();
      onClose();
    }
  };
  return (
    <Modal title="任务结果与候选审阅" wide onClose={onClose}>
      <div className="inline">
        <Badge value={t.status} />
        <span>{t.phase}</span>
      </div>
      {t.error && <div className="alert">{t.error}</div>}
      {result.sections && <CandidateOutline task={t} />}
      {result.document && (
        <CandidateMerge task={t} act={act} onClose={onClose} />
      )}
      {result.document ? (
        <>
          <p className="muted">
            接受前检查依据、数字和限定条件。版本冲突时不会覆盖新稿。
          </p>
          <div className="diff-grid">
            <section>
              <h4>当前正文</h4>
              <pre>{before || "尚无正文"}</pre>
            </section>
            <section>
              <h4>候选正文</h4>
              {result.document.content?.map((n: Row) => (
                <div className="candidate-paragraph" key={n.attrs.nodeId}>
                  <input
                    type="checkbox"
                    aria-label="选择候选段落"
                    checked={selected.includes(n.attrs.nodeId)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, n.attrs.nodeId]
                          : selected.filter((x) => x !== n.attrs.nodeId),
                      )
                    }
                  />
                  <p>
                    {n.content
                      ?.map((c: Row) => (c.type === "text" ? c.text : "[引用]"))
                      .join("")}
                  </p>
                </div>
              ))}
            </section>
          </div>
          {result.missing_materials?.length > 0 && (
            <div className="alert">
              待补资料：{result.missing_materials.join("；")}
            </div>
          )}
          <div className="actions">
            <Button variant="primary" onClick={() => apply(false)}>
              接受完整候选稿
            </Button>
            <Button disabled={!selected.length} onClick={() => apply(true)}>
              追加选中的 {selected.length} 段
            </Button>
          </div>
        </>
      ) : result.raw_text ? (
        <>
          <div className="alert">{result.validation_error}</div>
          <pre className="source-content">{result.raw_text}</pre>
        </>
      ) : result.text ? (
        <>
          <p className="muted">
            这是识别候选，核对后转录到对应页面，再确认规则。
          </p>
          <pre className="source-content">{result.text}</pre>
          <Button
            onClick={() => {
              navigator.clipboard.writeText(result.text);
              notify("候选文字已复制");
            }}
          >
            复制识别文字
          </Button>
        </>
      ) : result.export_id ? (
        <>
          <div className="success-box">
            <CheckCircle2 size={24} />
            <div>
              <h3>交付文件已生成</h3>
              <p>请在目标软件检查目录、页码与视觉版式。</p>
            </div>
          </div>
          <div className="download-grid">
            {result.files?.map((name: string) => (
              <a
                className="download-card"
                key={name}
                href={
                  "/api/v1/exports/" +
                  result.export_id +
                  "/files/" +
                  encodeURIComponent(name)
                }
              >
                <Download size={18} />
                {name}
              </a>
            ))}
          </div>
        </>
      ) : (
        <>
          {!result.sections && (
            <JsonDetail value={result || { phase: t.phase }} />
          )}
        </>
      )}
      <details className="mt">
        <summary>任务依据与调用清单</summary>
        <JsonDetail value={{ payload: t.payload, manifest: result.manifest }} />
      </details>
    </Modal>
  );
}
function Research() {
  const { pid, tick, act, setPage } = useApp();
  const { data: facts } = useLoad("/projects/" + pid + "/facts", tick),
    { data: artifacts } = useLoad("/projects/" + pid + "/artifacts", tick),
    { data: files } = useLoad("/projects/" + pid + "/files", tick),
    { data: writingLink } = useLoad("/projects/" + pid + "/writing-link", tick);
  const [editing, setEditing] = useState<Row | null>(null),
    [upload, setUpload] = useState(false),
    [analysis, setAnalysis] = useState(false);
  return (
    <>
      <PageTitle
        title="把真实研究，登记清楚。"
        description="计划、实现与测量分别记录，每个结论都能回到对应材料。"
        actions={
          <>
            <ChartTools />
            <Button onClick={() => setPage("agent-writing")}><BookOpen size={15} />进入有据写作</Button>
            <Button onClick={() => setAnalysis(true)}>
              <FlaskConical size={15} />
              描述性统计
            </Button>
            <Button onClick={() => setUpload(true)}>
              <Upload size={15} />
              上传研究产物
            </Button>
            <Button variant="primary" onClick={() => setEditing({})}>
              <Plus size={15} />
              登记事实
            </Button>
          </>
        }
      />
      <ResearchMap />
      <section className="panel research-chain">
        <div className="section-heading"><h2>写作链路状态</h2><span>研究事实 → 章节任务 → 正文复核</span></div>
        <div className="research-chain-grid">
          <button onClick={() => setEditing({})}><FlaskConical size={18} /><strong>{facts?.length ?? 0} 项研究事实</strong><small>登记后可绑定到章节任务</small></button>
          <button onClick={() => setPage("agent-writing")}><BookOpen size={18} /><strong>{writingLink?.sections?.filter((s: Row) => s.contract_id).length ?? 0} 个章节任务</strong><small>发布目标、事实和证据</small></button>
          <button onClick={() => setPage("editor")}><PenLine size={18} /><strong>{writingLink?.sections?.filter((s: Row) => s.revision_id).length ?? 0} 个正文版本</strong><small>回到编辑器核对关联</small></button>
        </div>
      </section>
      <div className="panel">
        <div className="section-heading">
          <h2>研究事实与成果</h2>
          <span>事实变化后，相关章节提示复查</span>
        </div>
        {facts?.length ? (
          facts.map((f: Row) => (
            <div className="fact-row" key={f.id}>
              <div className="fact-icon">
                <FlaskConical size={19} />
              </div>
              <div className="grow">
                <h3>{f.label}</h3>
                <p>{f.statement}</p>
                <div className="inline">
                  <Badge value={f.status} />
                  <Badge value={f.verification} />
                  <small>
                    修订 {f.revision} · {date(f.updated_at)}
                  </small>
                </div>
                {!!writingLink?.facts?.find((item: Row) => item.id === f.id)?.used_by?.length && <small>已用于：{writingLink.facts.find((item: Row) => item.id === f.id).used_by.map((item: Row) => item.title).join("、")}</small>}
              </div>
              <div className="actions"><Button onClick={() => setEditing(f)}>编辑依据</Button><Button onClick={() => { const first = writingLink?.facts?.find((item: Row) => item.id === f.id)?.used_by?.[0]; if (first) { localStorage.setItem("paper_section", first.id); setPage("editor"); } else setPage("agent-writing"); }}>进入写作</Button></div>
            </div>
          ))
        ) : (
          <Empty
            title="记录你的实际工作"
            detail="登记已实现的模块、实验与测试记录，也可以先记录计划工作。"
          />
        )}
      </div>
      <section className="panel mt">
        <div className="section-heading">
          <h2>研究产物</h2>
          <span>图表、截图、数据和代码说明</span>
        </div>
        <div className="artifact-grid">
          {artifacts?.map((a: Row) => (
            <div className="artifact-card" key={a.id}>
              {a.kind === "figure" ? (
                <img alt={a.name} src={"/api/v1/images/" + a.sha256} />
              ) : (
                <div className="artifact-placeholder">
                  <FileText size={28} />
                </div>
              )}
              <h4>{a.name}</h4>
              <small>
                {a.kind} · {date(a.created_at)}
              </small>
              {a.kind === "analysis" && (
                <details>
                  <summary>查看实际计算结果</summary>
                  <JsonDetail value={parse(a.metadata)} />
                </details>
              )}
            </div>
          ))}
        </div>
        {!artifacts?.length && <p className="muted">尚未上传研究产物。</p>}
      </section>
      {editing && (
        <Modal
          title={editing.id ? "修订研究事实" : "登记研究事实"}
          onClose={() => setEditing(null)}
        >
          <form
            onSubmit={async (e) => {
              const f = formValues(e);
              const d = await act(
                () =>
                  send(
                    editing.id
                      ? "/facts/" + editing.id
                      : "/projects/" + pid + "/facts",
                    {
                      ...f,
                      source_ids: f.source_ids
                        .split(/[,，\s]+/)
                        .filter(Boolean),
                      artifact_id: f.artifact_id || null,
                    },
                    editing.id ? "PATCH" : "POST",
                  ),
                "研究事实已保存",
              );
              if (d) setEditing(null);
            }}
          >
            <Field label="事实名称">
              <input
                name="label"
                required
                defaultValue={editing.label ?? ""}
                placeholder="例如：用户管理模块已经实现"
              />
            </Field>
            <Field label="具体内容与适用范围">
              <textarea
                name="statement"
                rows={4}
                required
                defaultValue={editing.statement ?? ""}
              />
            </Field>
            <div className="form-grid">
              <Field label="工作状态">
                <select
                  name="status"
                  defaultValue={editing.status ?? "planned"}
                >
                  {["planned", "implemented", "measured"].map((x) => (
                    <option key={x} value={x}>
                      {stateLabel[x]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="核查状态">
                <select
                  name="verification"
                  defaultValue={editing.verification ?? "unverified"}
                >
                  <option value="unverified">待核实</option>
                  <option value="user_confirmed">用户确认</option>
                  <option value="artifact_linked">已关联材料</option>
                  <option value="independently_checked">已独立核对</option>
                </select>
              </Field>
            </div>
            <Field label="关联研究产物">
              <select
                name="artifact_id"
                defaultValue={editing.artifact_id ?? ""}
              >
                <option value="">暂未关联</option>
                {artifacts?.map((a: Row) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="原文来源 ID（可选）"
              hint="可从资料来源详情取得，多个用逗号分隔。"
            >
              <input
                name="source_ids"
                defaultValue={parse(editing.source_ids, []).join(",")}
              />
            </Field>
            <Button type="submit" variant="primary">
              保存事实
            </Button>
          </form>
        </Modal>
      )}
      {upload && (
        <Modal title="上传研究产物" onClose={() => setUpload(false)}>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const d = await act(
                () =>
                  api("/projects/" + pid + "/artifacts", {
                    method: "POST",
                    body: new FormData(e.currentTarget),
                  }),
                "研究产物已保存",
              );
              if (d) setUpload(false);
            }}
          >
            <Field label="原始文件">
              <input type="file" name="file" required />
            </Field>
            <Field label="产物类型">
              <select name="kind">
                <option value="figure">图表/实际截图</option>
                <option value="data">数据</option>
                <option value="test_record">测试记录</option>
                <option value="code">代码说明/快照</option>
                <option value="analysis">已有分析结果</option>
              </select>
            </Field>
            <Field label="模型使用范围">
              <select name="egress">
                <option value="selected_content">允许按任务使用选定内容</option>
                <option value="local_only">仅限本地，不发送模型</option>
              </select>
            </Field>
            <Button type="submit" variant="primary">
              上传保存
            </Button>
          </form>
        </Modal>
      )}
      {analysis && (
        <Modal title="执行描述性统计" onClose={() => setAnalysis(false)}>
          <p className="muted">
            对CSV/XLSX计算缺失情况及数值列的均值、标准差和分位数，保存输入版本和实际结果。
          </p>
          <form
            onSubmit={async (e) => {
              const d = await act(
                () => send("/projects/" + pid + "/analysis", formValues(e)),
                "统计结果已保存到研究产物",
              );
              if (d) setAnalysis(false);
            }}
          >
            <Field label="数据文件">
              <select name="file_id" required>
                <option value="">请选择</option>
                {files
                  ?.filter((f: Row) => [".csv", ".xlsx"].includes(f.extension))
                  .map((f: Row) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Button type="submit" variant="primary">
              执行并保存
            </Button>
          </form>
        </Modal>
      )}
    </>
  );
}
function References() {
  const { pid, tick, act } = useApp();
  return (
    <Suspense fallback={<div className="panel muted" role="status">正在打开文献与术语…</div>}>
      <ReferencesWorkspace
        key={pid}
        pid={pid}
        tick={tick}
        act={act}
        renderSource={(file, onClose) => <SourceViewer file={file} onClose={onClose} />}
      />
    </Suspense>
  );
}
function Review() {
  const { pid, tick, act } = useApp();
  const { data: findings } = useLoad("/projects/" + pid + "/findings", tick),
    { data: mentor } = useLoad("/projects/" + pid + "/mentor-tasks", tick),
    { data: sections } = useLoad("/projects/" + pid + "/sections", tick);
  const [adding, setAdding] = useState(false),
    [filter, setFilter] = useState("all");
  const ff = (findings ?? []).filter(
    (f: Row) => filter === "all" || f.result === filter,
  );
  return (
    <>
      <PageTitle
        title="让问题，停在交付之前。"
        description="程序检查与人工核对分别记录，未解决事项不会被隐藏。"
        actions={
          <>
            <Button onClick={() => setAdding(true)}>
              <MessageSquare size={15} />
              登记导师意见
            </Button>
            <Button
              onClick={() =>
                act(
                  () => send("/projects/" + pid + "/semantic-review", {}),
                  "AI语义检查已排队，按实际调用计费",
                )
              }
            >
              <Sparkles size={15} />
              AI语义检查
            </Button>
            <Button
              variant="primary"
              onClick={() =>
                act(
                  () => send("/projects/" + pid + "/reviews", {}),
                  "全文检查已排队",
                )
              }
            >
              <ClipboardCheck size={16} />
              执行全文检查
            </Button>
          </>
        }
      />
      <ImpactPanel pid={pid} tick={tick} act={act} />
      <ManualRuleReview pid={pid} tick={tick} act={act} />
      <div className="summary-strip">
        {[
          ["fail", "未通过"],
          ["unknown", "待判断"],
          ["pass", "已通过"],
        ].map(([k, v]) => (
          <span key={k}>
            {v}
            <strong>
              {findings?.filter((f: Row) => f.result === k).length ?? 0}
            </strong>
          </span>
        ))}
      </div>
      <div className="tabs">
        {[
          ["all", "全部"],
          ["fail", "未通过"],
          ["unknown", "待判断"],
          ["pass", "通过"],
        ].map(([k, v]) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={filter === k ? "active" : ""}
          >
            {v}
          </button>
        ))}
      </div>
      <section className="panel no-padding">
        {ff.length ? (
          ff.map((f: Row) => (
            <div className="finding-row" key={f.id}>
              <div className={"finding-icon " + f.result}>
                {f.result === "pass" ? (
                  <CheckCircle2 size={19} />
                ) : f.result === "fail" ? (
                  <AlertTriangle size={19} />
                ) : (
                  <Circle size={17} />
                )}
              </div>
              <div className="grow">
                <p>{f.message}</p>
                <small>
                  {f.method === "human" ? "人工核对项" : "程序检查"}
                  {f.section_id
                    ? " · " +
                      (sections?.find((s: Row) => s.id === f.section_id)
                        ?.title ?? "相关章节")
                    : ""}
                </small>
              </div>
              <Badge value={f.result} />
              <select
                className="small-select"
                value={f.status}
                onChange={(e) =>
                  act(
                    () =>
                      send(
                        "/findings/" + f.id,
                        { status: e.target.value },
                        "PATCH",
                      ),
                    "处理状态已保存，原检查结果保留",
                  )
                }
              >
                <option value="open">未处理</option>
                <option value="reviewed">已查看</option>
                <option value="resolved">已处理</option>
                <option value="accepted_risk">已知悉保留</option>
              </select>
            </div>
          ))
        ) : (
          <Empty
            title="还没有检查结果"
            detail="检查结构、引用关联、待补内容和材料状态，语义与版面仍需人工核对。"
          />
        )}
      </section>
      <section className="panel mt">
        <div className="section-heading">
          <h2>导师意见与修改任务</h2>
          <span>已修改与已复查分别记录</span>
        </div>
        {mentor?.map((m: Row) => (
          <div className="mentor-row" key={m.id}>
            <MessageSquare size={18} />
            <div className="grow">
              <p>{m.comment}</p>
              <small>
                {sections?.find((s: Row) => s.id === m.section_id)?.title ??
                  "全文"}{" "}
                · {date(m.created_at)}
              </small>
            </div>
            <select
              value={m.status}
              onChange={(e) =>
                act(
                  () =>
                    send(
                      "/mentor-tasks/" + m.id,
                      { status: e.target.value },
                      "PATCH",
                    ),
                  "修改任务已更新",
                )
              }
            >
              {[
                ["open", "待处理"],
                ["planned", "已计划"],
                ["candidate_ready", "候选就绪"],
                ["applied", "已修改"],
                ["rechecked", "已复查"],
                ["dismissed", "不采纳"],
              ].map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
        ))}
        {!mentor?.length && <p className="muted">暂无导师意见。</p>}
      </section>
      {adding && (
        <Modal title="登记导师意见" onClose={() => setAdding(false)}>
          <form
            onSubmit={async (e) => {
              const d = await act(
                () => send("/projects/" + pid + "/mentor-tasks", formValues(e)),
                "导师意见已保存",
              );
              if (d) setAdding(false);
            }}
          >
            <Field label="意见原文">
              <textarea required name="comment" rows={5} />
            </Field>
            <Field label="涉及章节">
              <select name="section_id">
                <option value="">全文</option>
                {sections?.map((s: Row) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </Field>
            <Button type="submit" variant="primary">
              保存任务
            </Button>
          </form>
        </Modal>
      )}
    </>
  );
}
function Exports() {
  const { pid, tick, act } = useApp();
  const { data: format } = useLoad("/projects/" + pid + "/format", tick),
    { data: exports } = useLoad("/projects/" + pid + "/exports", tick),
    { data: backups } = useLoad("/backups", tick);
  const [editing, setEditing] = useState(false),
    [restored, setRestored] = useState<Row | null>(null);
  const [writingPolicy, setWritingPolicy] = useState("draft");
  return (
    <>
      <PageTitle
        title="从草稿，到可交付的文档。"
        description="正文、引用、规则与格式一起冻结版本，在目标软件检查最终版面。"
        actions={
          <>
            <Button
              onClick={() =>
                act(
                  () => send("/backups", { project_id: pid }),
                  "正在创建一致性备份",
                )
              }
            >
              <ArchiveRestore size={15} />
              备份工作台
            </Button>
            <Button
              variant="primary"
              onClick={() =>
                act(
                  () =>
                    send("/projects/" + pid + "/exports", {
                      mode: writingPolicy,
                      pdf: true,
                    }),
                  "导出已排队，完成后可下载文件与报告",
                )
              }
            >
              <Download size={16} />
              导出论文与报告
            </Button>
          </>
        }
      />
      <label style={{display:"flex",alignItems:"center",gap:12,marginBottom:16}}>交付核验策略<select aria-label="交付核验策略" value={writingPolicy} onChange={e=>setWritingPolicy(e.target.value)}><option value="draft">草稿：保留并报告所有未决项</option><option value="strict">严格交付：检查当前依据、逐句核验与全文复核</option></select></label>
      <FormatPlans pid={pid} tick={tick} act={act} />
      <div className="two-col">
        <section className="panel">
          <div className="section-heading">
            <h2>排版配置</h2>
            <Button onClick={() => setEditing(true)}>调整格式</Button>
          </div>
          <dl className="format-list">
            <div>
              <dt>中文字体</dt>
              <dd>{format?.config.font}</dd>
            </div>
            <div>
              <dt>西文字体</dt>
              <dd>{format?.config.latin_font}</dd>
            </div>
            <div>
              <dt>字号 / 行距</dt>
              <dd>
                {format?.config.font_size} pt / {format?.config.line_spacing} 倍
              </dd>
            </div>
            <div>
              <dt>页面</dt>
              <dd>
                A4 · {format?.config.margin_top}/{format?.config.margin_bottom}/
                {format?.config.margin_left}/{format?.config.margin_right} mm
              </dd>
            </div>
            <div>
              <dt>模板</dt>
              <dd>
                {format?.template_sha256
                  ? "用户 DOCX 模板（待验收）"
                  : "内置基础模板（非学校认证）"}
              </dd>
            </div>
            <div>
              <dt>PDF 渲染组件</dt>
              <dd>
                {format?.renderer_available ? "可用" : "未检测到，DOCX仍可导出"}
              </dd>
            </div>
          </dl>
          <label className="btn upload-label">
            <FileUp size={15} />
            上传学校 DOCX 模板
            <input
              hidden
              type="file"
              accept=".docx"
              onChange={(e) => {
                if (e.target.files?.[0]) {
                  const f = new FormData();
                  f.append("file", e.target.files[0]);
                  act(
                    () =>
                      api("/projects/" + pid + "/template", {
                        method: "POST",
                        body: f,
                      }),
                    "模板已保存，请导出样本验收",
                  );
                }
              }}
            />
          </label>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>交付前核对</h2>
            <ClipboardCheck size={18} />
          </div>
          <ul className="checklist">
            <li>
              <CheckCircle2 size={17} />
              确认本次规则包与版本
            </li>
            <li>
              <CheckCircle2 size={17} />
              核对研究结果及引用原文
            </li>
            <li>
              <CheckCircle2 size={17} />
              处理待补内容和导师意见
            </li>
            <li>
              <CheckCircle2 size={17} />
              在目标软件更新目录与页码
            </li>
            <li>
              <CheckCircle2 size={17} />
              逐页检查图表、分页和字体
            </li>
          </ul>
          <div className="notice compact">
            <AlertTriangle size={17} />
            <p>导出成功表示文件生成成功，报告会保留尚未人工验收的状态。</p>
          </div>
        </section>
      </div>
      <section className="panel mt">
        <div className="section-heading">
          <h2>导出历史</h2>
          <span>每次导出保留独立快照</span>
        </div>
        {exports?.map((x: Row) => (
          <div className="export-row" key={x.id}>
            <div className="file-icon">
              <FileText size={21} />
            </div>
            <div className="grow">
              <h3>论文交付快照</h3>
              <small>
                {date(x.created_at)} · {x.id.slice(0, 10)}…
              </small>
            </div>
            <a
              className="btn"
              href={
                "/api/v1/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("论文.docx")
              }
            >
              DOCX
              <Download size={14} />
            </a>
            <a
              className="btn"
              href={
                "/api/v1/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("检查报告.md")
              }
            >
              检查报告
            </a>
            <a
              className="btn primary"
              href={
                "/api/v1/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("论文交付包.zip")
              }
            >
              交付包
              <Download size={14} />
            </a>
          </div>
        ))}
        {!exports?.length && (
          <Empty
            title="尚无导出快照"
            detail="可以先导出草稿，未解决问题会出现在随附报告中。"
          />
        )}
      </section>
      <section className="panel mt">
        <div className="section-heading">
          <h2>备份与恢复</h2>
          <label className="btn upload-label">
            <ArchiveRestore size={15} />
            恢复隔离副本
            <input
              hidden
              type="file"
              accept=".zip"
              onChange={async (e) => {
                if (e.target.files?.[0]) {
                  const f = new FormData();
                  f.append("file", e.target.files[0]);
                  const d = await act(
                    () => api("/backups/restore", { method: "POST", body: f }),
                    "备份已校验并恢复到隔离目录",
                  );
                  if (d) setRestored(d);
                }
              }}
            />
          </label>
        </div>
        {backups?.map((b: Row) => (
          <div className="export-row" key={b.id}>
            <ArchiveRestore size={19} />
            <div className="grow">
              <strong>工作台一致性备份</strong>
              <small>{date(b.created_at)} · 不含 API key</small>
            </div>
            <a className="btn" href={"/api/v1/backups/" + b.id + "/download"}>
              下载备份
            </a>
          </div>
        ))}
        {!backups?.length && (
          <p className="muted">
            暂无备份。备份包含所有项目、资料及版本，不包含密钥。
          </p>
        )}
        {restored && (
          <div className="success-box">
            <div>
              <p>{restored.message}</p>
              <code>{restored.restored_data_directory}</code>
            </div>
          </div>
        )}
      </section>
      {editing && (
        <Modal title="论文格式配置" wide onClose={() => setEditing(false)}>
          <form
            onSubmit={async (e) => {
              const f = formValues(e);
              const cfg: Row = {};
              for (const [k, v] of Object.entries(f))
                cfg[k] = ["font", "latin_font", "heading_font"].includes(k)
                  ? v
                  : Number(v);
              cfg.reference_superscript = true;
              const d = await act(
                () => send("/projects/" + pid + "/format", cfg, "PUT"),
                "格式配置已保存",
              );
              if (d) setEditing(false);
            }}
          >
            <div className="form-grid">
              {[
                ["中文字体", "font"],
                ["西文字体", "latin_font"],
                ["标题字体", "heading_font"],
                ["字号pt", "font_size"],
                ["行距倍数", "line_spacing"],
                ["首行缩进字符", "first_line_chars"],
                ["上边距mm", "margin_top"],
                ["下边距mm", "margin_bottom"],
                ["左边距mm", "margin_left"],
                ["右边距mm", "margin_right"],
              ].map(([label, key]) => (
                <Field key={key} label={label}>
                  <input
                    name={key}
                    type={
                      ["font", "latin_font", "heading_font"].includes(key)
                        ? "text"
                        : "number"
                    }
                    step="0.1"
                    defaultValue={format?.config[key]}
                  />
                </Field>
              ))}
            </div>
            <Button type="submit" variant="primary">
              保存格式
            </Button>
          </form>
        </Modal>
      )}
    </>
  );
}
function SettingsPage() {
  const { tick, act, notify, pid, refresh } = useApp();
  const { data, set: setSettings } = useLoad("/settings", tick);
  const { data: usage } = useLoad(
    pid ? "/projects/" + pid + "/usage" : null,
    tick,
  );
  const [cfg, setCfg] = useState<Row | null>(null),
    [key, setKey] = useState(""),
    [models, setModels] = useState<Row[]>([]),
    [probing, setProbing] = useState(false),
    [saving, setSaving] = useState(false),
    [deleting, setDeleting] = useState(false),
    [balanceLoading, setBalanceLoading] = useState(false),
    [balanceError, setBalanceError] = useState(""),
    [feedback, setFeedback] = useState<{
      kind: "success" | "error" | "info";
      text: string;
    } | null>(null);
  const busy = probing || saving || deleting || balanceLoading;
  const acceptSaved = (saved: Row) => {
    setSettings(saved);
    setCfg(saved.provider);
    setModels(saved.capabilities?.models ?? []);
    setKey("");
  };
  const persistSettings = async (detect = false) => {
    if (busy || !cfg) return;
    let persisted = false;
    if (detect) setProbing(true);
    else setSaving(true);
    setFeedback({
      kind: "info",
      text: detect ? "正在保存设置并检测模型…" : "正在安全保存接口设置…",
    });
    try {
      const trimmed = key.trim();
      if (key && !trimmed)
        throw new Error("密钥不能只有空格；留空表示保留已有密钥。");
      const saved = await send(
        "/settings",
        { provider: cfg, ...(trimmed ? { api_key: trimmed } : {}) },
        "PUT",
      );
      acceptSaved(saved);
      persisted = true;
      const credentialMessage = saved.credential.configured
        ? "密钥已保存在本机，输入框显示的是脱敏提示，无需重复填写。"
        : "接口设置已保存；尚未填写API key。";
      if (!detect) {
        setFeedback({ kind: "success", text: credentialMessage });
        notify("接口设置已保存");
        return;
      }
      if (!saved.credential.configured)
        throw new Error("尚未填写API key，请先输入密钥。");
      setFeedback({
        kind: "info",
        text: "密钥已保存，正在向中转站获取模型列表…",
      });
      const detected = await send("/provider/models");
      setModels(detected.models);
      setFeedback({
        kind: "success",
        text:
          "密钥已保存；检测成功，取得 " +
          detected.models.length +
          " 个模型。选择模型ID后请保存设置。",
      });
      notify("连接成功，取得 " + detected.models.length + " 个模型");
    } catch (error) {
      const message = (error as Error).message;
      const text =
        persisted && detect
          ? "设置已保存；模型检测未完成：" +
            message +
            " 已保存的密钥会继续保留，无需重新输入。"
          : "保存失败：" + message + " 输入内容已保留，请修正后重试。";
      setFeedback({ kind: "error", text });
      notify(text, true);
    } finally {
      setProbing(false);
      setSaving(false);
      refresh();
    }
  };
  const refreshBalance = async () => {
    if (busy || key || JSON.stringify(cfg) !== JSON.stringify(data?.provider)) return;
    setBalanceLoading(true);
    setBalanceError("");
    try {
      const result = await send("/provider/account-usage");
      setSettings({ ...data, account_usage: result });
      notify("中转站账户余额已更新");
    } catch (error) {
      setBalanceError((error as Error).message);
    } finally {
      setBalanceLoading(false);
    }
  };
  const deleteCredential = async () => {
    if (busy || !confirm("确认从本机删除已保存密钥？")) return;
    setDeleting(true);
    setFeedback({ kind: "info", text: "正在删除本机密钥…" });
    try {
      const saved = await send("/settings", { api_key: "" }, "PUT");
      acceptSaved(saved);
      setFeedback({
        kind: saved.credential.configured ? "info" : "success",
        text: saved.credential.configured
          ? "已删除文件中的密钥，但当前环境变量仍提供有效密钥。"
          : "本机密钥已删除。",
      });
      notify("本机密钥删除操作已完成");
    } catch (error) {
      setFeedback({
        kind: "error",
        text: "删除失败：" + (error as Error).message,
      });
      notify((error as Error).message, true);
    } finally {
      setDeleting(false);
      refresh();
    }
  };
  useEffect(() => {
    if (data && !cfg) {
      setCfg(data.provider);
      setModels(data.capabilities?.models ?? []);
    }
  }, [data, cfg]);
  if (!cfg)
    return (
      <div className="loading">
        <LoaderCircle className="spin" />
        加载接口设置…
      </div>
    );
  const update = (k: string, v: any) => setCfg({ ...cfg, [k]: v });
  return (
    <>
      <PageTitle
        title="接口与设置"
        description="使用自己的中转站与密钥，模型 ID 以服务商实际返回为准。"
      />
      <MaterialSummarySettings onSaved={refresh} />
      <GptFallbackSettings pid={pid} />
      <div className="settings-layout">
        <section className="panel">
          <div className="section-heading">
            <h2>
              <KeyRound size={18} />
              DeepSeek 主模型接口
            </h2>
            <Badge
              tone={
                key.trim()
                  ? "amber"
                  : data?.credential.configured
                    ? "green"
                    : undefined
              }
              value={
                key.trim()
                  ? "新密钥待保存"
                  : data?.credential.configured
                    ? "已保存密钥"
                    : "待填写密钥"
              }
            />
          </div>
          <form
            noValidate
            aria-label="模型接口设置"
            onSubmit={async (e) => {
              e.preventDefault();
              await persistSettings(false);
            }}
          >
            <fieldset disabled={busy} className="settings-fieldset">
              <Field label="API 中转站地址">
                <input
                  value={cfg.base_url}
                  onChange={(e) => update("base_url", e.target.value)}
                  required
                />
              </Field>
              <Field
                label="API key"
                hint={`Windows DPAPI 加密保存，不回传完整密钥。${data?.credential.masked ? " 当前保存：" + data.credential.masked + "；留空不会删除。" : ""}`}
              >
                <input
                  type="password"
                  aria-label="API key"
                  autoComplete="new-password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder={
                    data?.credential.configured
                      ? "已保存 " + data.credential.masked + "，留空继续使用"
                      : "填写自己的 key"
                  }
                />
              </Field>
              {feedback && (
                <div
                  role={feedback.kind === "error" ? "alert" : "status"}
                  className={"settings-feedback " + feedback.kind}
                  data-testid="settings-feedback"
                  aria-live="polite"
                >
                  {feedback.kind === "info" && busy ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : feedback.kind === "error" ? (
                    <AlertTriangle size={16} />
                  ) : (
                    <CheckCircle2 size={16} />
                  )}
                  <span>{feedback.text}</span>
                </div>
              )}
              <Field label="接口协议">
                <select
                  value={cfg.protocol}
                  onChange={(e) => update("protocol", e.target.value)}
                >
                  <option value="openai">
                    OpenAI 兼容 Chat Completions（/v1）
                  </option>
                  <option value="responses">OpenAI Responses（/v1）</option>
                </select>
              </Field>
              <div className="model-field">
                <Field label="模型 ID" hint="先保存密钥，再检测模型列表。">
                  <input
                    list="model-list"
                    value={cfg.model}
                    onChange={(e) => update("model", e.target.value)}
                    placeholder="填写中转站的 DeepSeek 模型 ID"
                  />
                  <datalist id="model-list">
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </datalist>
                </Field>
                <Button disabled={busy} onClick={() => persistSettings(true)}>
                  {probing ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : (
                    <RefreshCw size={15} />
                  )}
                  {probing ? "检测中…" : "检测模型"}
                </Button>
              </div>
              <div className="form-grid">
                <Field label="单次输出上限 Token">
                  <input
                    type="number"
                    value={cfg.max_output_tokens}
                    onChange={(e) =>
                      update("max_output_tokens", Number(e.target.value))
                    }
                  />
                </Field>
                <Field label="请求超时秒数">
                  <input
                    type="number"
                    min="15"
                    value={cfg.timeout_seconds}
                    onChange={(e) =>
                      update("timeout_seconds", Number(e.target.value))
                    }
                  />
                </Field>
              </div>
              <details>
                <summary>预算与兼容性设置</summary>
                <label className="checkbox"><input type="checkbox" checked={!!cfg.supports_images} onChange={(e) => update("supports_images", e.target.checked)} />此 DeepSeek 接口支持图片输入（先验证再开启）</label>
                <Button disabled={busy || !pid || !!key || JSON.stringify(cfg) !== JSON.stringify(data?.provider)} onClick={() => act(() => send("/provider/probe-image", {project_id:pid,provider_profile:"primary"}), "DeepSeek 图片验证已排队，按实际用量计费；结果见任务中心")}>测试 DeepSeek 图片（计费）</Button>
                <div className="form-grid">
                  <Field label="每百万输入Token单价">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={cfg.input_price ?? ""}
                      placeholder="未配置则费用未知"
                      onChange={(e) =>
                        update(
                          "input_price",
                          e.target.value === "" ? null : Number(e.target.value),
                        )
                      }
                    />
                  </Field>
                  <Field label="每百万输出Token单价">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      value={cfg.output_price ?? ""}
                      onChange={(e) =>
                        update(
                          "output_price",
                          e.target.value === "" ? null : Number(e.target.value),
                        )
                      }
                    />
                  </Field>
                  <Field label="单次未知费用预留">
                    <input
                      type="number"
                      min="0.01"
                      step="any"
                      value={cfg.max_request_cost}
                      onChange={(e) =>
                        update("max_request_cost", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field label="计价币种">
                    <input
                      value={cfg.currency}
                      onChange={(e) => update("currency", e.target.value)}
                    />
                  </Field>
                </div>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={!!cfg.json_mode}
                    onChange={(e) => update("json_mode", e.target.checked)}
                  />
                  启用服务商 JSON 模式（确认支持后开启）
                </label>
              </details>
              <div className="actions mt">
                <Button type="submit" variant="primary" disabled={busy}>
                  {saving ? (
                    <LoaderCircle className="spin" size={15} />
                  ) : (
                    <Save size={15} />
                  )}
                  {saving ? "保存中…" : "保存接口设置"}
                </Button>
                <Button
                  disabled={
                    busy ||
                    !pid ||
                    !data?.credential.configured ||
                    !data?.provider.model ||
                    !!key ||
                    JSON.stringify(cfg) !== JSON.stringify(data?.provider)
                  }
                  onClick={() =>
                    act(
                      () =>
                        send("/provider/probe-generation", { project_id: pid }),
                      "生成能力测试已排队，按实际用量计费；结果在任务中心",
                    )
                  }
                >
                  测试生成能力（计费）
                </Button>
                {data?.credential.configured && (
                  <Button disabled={busy} onClick={deleteCredential}>
                    {deleting ? "删除中…" : "删除密钥"}
                  </Button>
                )}
              </div>
            </fieldset>
          </form>
        </section>
        <aside>
          <section className="panel" aria-label="中转站账户余额">
            <div className="section-heading">
              <h2>中转站账户余额</h2>
              <Button
                disabled={busy || !data?.credential.configured || !!key || JSON.stringify(cfg) !== JSON.stringify(data?.provider)}
                onClick={refreshBalance}
              >
                {balanceLoading ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}
                {balanceLoading ? "查询中…" : "查询余额"}
              </Button>
            </div>
            {data?.account_usage ? (
              <div data-testid="account-usage">
                <strong className="balance-amount">{data.account_usage.balance_known ? data.account_usage.remaining_exact : "余额未知"} {data.account_usage.unit}</strong>
                <dl className="format-list">
                  <div><dt>账户状态</dt><dd>{data.account_usage.validity_source === "default_assumed" ? "接口未报告有效性" : data.account_usage.is_valid === true ? "有效" : data.account_usage.is_valid === false ? "已停用" : "无法识别"}</dd></div>
                  <div><dt>查询时间</dt><dd>{date(data.account_usage.fetched_at)}</dd></div>
                </dl>
                {data.account_usage.unit_source !== "response" && <p className="muted">接口未提供单位，按约定显示 USD，请核对账单。</p>}
              </div>
            ) : <p className="muted">尚未查询。保存接口设置后，点击查询余额。</p>}
            {balanceError && <p role="alert" className="settings-feedback error">查询失败：{balanceError}。如有上次结果，其查询时间保持不变。</p>}
            <p className="muted">余额来自 /v1/usage，属于整个中转站账户。项目预算和单次调用费用分别记录；未填写价格时费用仍为待结算。</p>
            {(!!key || JSON.stringify(cfg) !== JSON.stringify(data?.provider)) && <p className="muted">请先保存当前修改，再查询对应账户。</p>}
          </section>
          <section className="panel mt">
            <div className="section-heading">
              <h2>使用说明</h2>
              <Settings size={17} />
            </div>
            <ol className="guide-list">
              <li>填写中转站与自己的 API key。</li>
              <li>检测模型，选择实际模型 ID。</li>
              <li>核对资料并发布论文规则包。</li>
              <li>建立提纲，逐节生成并审阅。</li>
            </ol>
            <div className="notice compact">
              <ShieldCheck size={18} />
              <p>
                原件在本机。API
                调用会将选定资料发送给中转站，每份资料可设置为“仅限本地”。
              </p>
            </div>
          </section>
          <ArchivedProjects />
          <section className="panel mt">
            <h3>本机环境</h3>
            <dl className="format-list">
              <div>
                <dt>版本</dt>
                <dd>{data?.version}</dd>
              </div>
              <div>
                <dt>密钥存储</dt>
                <dd>{data?.credential.storage}</dd>
              </div>
              <div>
                <dt>文档渲染</dt>
                <dd>{data?.renderer_available ? "可用" : "未安装"}</dd>
              </div>
            </dl>
            <details>
              <summary>数据目录</summary>
              <code className="path-text">{data?.data_directory}</code>
            </details>
          </section>
        </aside>
      </div>
      <section className="panel mt">
        <div className="section-heading">
          <h2>当前项目调用记录</h2>
          <span>未知费用保留预留额度</span>
        </div>
        {usage?.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>时间 / 模型</th>
                <th>状态</th>
                <th>输入 / 输出Token</th>
                <th>费用</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {usage.map((u: Row) => {
                const v = parse(u.usage);
                return (
                  <tr key={u.id}>
                    <td>
                      <strong>{u.model}</strong>
                      <small>{date(u.created_at)}</small>
                    </td>
                    <td>
                      <Badge value={u.status} />
                    </td>
                    <td>
                      {v.input_tokens ?? "未知"} / {v.output_tokens ?? "未知"}
                    </td>
                    <td>
                      {u.cost === null ? "待结算" : Number(u.cost).toFixed(5)}
                    </td>
                    <td>
                      {u.cost === null && (
                        <Button
                          onClick={() => {
                            const cost = prompt("按实际账单填写该调用费用");
                            if (cost !== null && Number.isFinite(Number(cost)))
                              act(
                                () =>
                                  send("/usage/" + u.id + "/settle", {
                                    cost: Number(cost),
                                  }),
                                "已登记实际费用",
                              );
                          }}
                        >
                          登记账单
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="muted">尚未进行收费模型调用。</p>
        )}
      </section>
    </>
  );
}

function ReferenceTools() {
  const { pid, tick, act } = useApp();
  const { data: citations } = useLoad("/projects/" + pid + "/citations", tick);
  const [lookup, setLookup] = useState(false),
    [results, setResults] = useState<Row[]>([]);
  return (
    <>
      <div className="actions mb">
        <label className="btn upload-label">
          <Upload size={15} />
          导入 BibTeX / RIS
          <input
            hidden
            type="file"
            accept=".bib,.ris"
            onChange={async (e) => {
              if (e.target.files?.[0]) {
                const form = new FormData();
                form.append("file", e.target.files[0]);
                await act(
                  () =>
                    api("/projects/" + pid + "/references/import", {
                      method: "POST",
                      body: form,
                    }),
                  "书目已导入，作者与年份等信息仍需核对",
                );
              }
            }}
          />
        </label>
        <Button onClick={() => setLookup(true)}>
          <Search size={15} />
          查询 DOI / 文献元数据
        </Button>
      </div>
      {citations?.length > 0 && (
        <section className="panel mb">
          <div className="section-heading">
            <h2>正文引用支持性核查</h2>
            <span>AI 判断保留为建议，人工确认另行记录</span>
          </div>
          {citations.map((c: Row) => (
            <details className="history-item" key={c.id}>
              <summary>
                <span>{c.section_title}</span>
                <Badge value={c.support} />
              </summary>
              <p className="muted">{c.section_text.slice(0, 1000)}</p>
              {c.sources?.map(
                (s: Row) =>
                  s && (
                    <blockquote key={s.id}>
                      <small>{s.name}</small>
                      <p>{s.text}</p>
                    </blockquote>
                  ),
              )}
              <div className="actions">
                <Button
                  onClick={() =>
                    act(
                      () => send("/citations/" + c.id + "/check", {}),
                      "引用核查任务已排队，结果在任务中心",
                    )
                  }
                >
                  <Sparkles size={15} />
                  AI 辅助核对
                </Button>
                <Button
                  onClick={() =>
                    act(
                      () =>
                        send(
                          "/citations/" + c.id,
                          { support: "human_confirmed" },
                          "PATCH",
                        ),
                      "已记录人工确认支持",
                    )
                  }
                >
                  人工确认支持
                </Button>
                <Button
                  onClick={() =>
                    act(
                      () =>
                        send(
                          "/citations/" + c.id,
                          { support: "partial" },
                          "PATCH",
                        ),
                      "已记录部分支持",
                    )
                  }
                >
                  部分支持
                </Button>
                <Button
                  onClick={() =>
                    act(
                      () =>
                        send(
                          "/citations/" + c.id,
                          { support: "unsupported" },
                          "PATCH",
                        ),
                      "已记录不支持",
                    )
                  }
                >
                  不支持
                </Button>
              </div>
            </details>
          ))}
        </section>
      )}
      {lookup && (
        <Modal title="查询文献元数据" wide onClose={() => setLookup(false)}>
          <form
            onSubmit={async (e) => {
              const d = await act(
                () =>
                  send(
                    "/projects/" + pid + "/references/lookup",
                    formValues(e),
                  ),
                "",
              );
              if (d) setResults(d.candidates);
            }}
          >
            <Field label="DOI 或文献题名">
              <input
                name="query"
                required
                placeholder="10.xxxx/xxxxx 或论文标题"
              />
            </Field>
            <Button type="submit">查询 Crossref</Button>
          </form>
          <p className="muted mt">
            匹配元数据不代表已阅读全文，查询无结果也不能证明文献不存在。
          </p>
          {results.map((r: Row, i: number) => (
            <div className="search-result" key={i}>
              <h3>{r.title}</h3>
              <p className="muted">
                {r.authors?.join("，")} · {r.year} · {r.journal}
              </p>
              <small>{r.doi}</small>
              <Button
                onClick={() =>
                  act(
                    () =>
                      send("/projects/" + pid + "/references", {
                        metadata: r,
                        verification: "metadata_matched",
                      }),
                    "已保存元数据；请补充原文并核对匹配",
                  )
                }
              >
                保存此候选
              </Button>
            </div>
          ))}
        </Modal>
      )}
    </>
  );
}
function ChartTools() {
  const { pid, tick, act } = useApp();
  const { data: files } = useLoad("/projects/" + pid + "/files", tick);
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <FlaskConical size={15} />
        从真实数据绘图
      </Button>
      {open && (
        <Modal title="绘制数值分布图" onClose={() => setOpen(false)}>
          <form
            onSubmit={async (e) => {
              const d = await act(
                () => send("/projects/" + pid + "/charts", formValues(e)),
                "图表已保存到研究产物，可在编辑器插入",
              );
              if (d) setOpen(false);
            }}
          >
            <Field label="数据文件">
              <select name="file_id" required>
                {files
                  ?.filter((f: Row) => [".csv", ".xlsx"].includes(f.extension))
                  .map((f: Row) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="数值列名">
              <input name="column" required placeholder="与原始数据表头一致" />
            </Field>
            <Field label="图表标题">
              <input name="title" placeholder="不填写则使用列名" />
            </Field>
            <Button type="submit" variant="primary">
              实际计算并绘图
            </Button>
          </form>
        </Modal>
      )}
    </>
  );
}

function CandidateOutline({ task }: { task: Row }) {
  const { act } = useApp();
  const [sections, setSections] = useState<Row[]>(task.result.sections);
  return (
    <section className="panel mt">
      <h3>提纲候选（接受前可编辑）</h3>
      {sections.map((s, i) => (
        <div className="outline-candidate" key={i}>
          <input
            aria-label={"候选章节标题" + (i + 1)}
            value={s.title}
            onChange={(e) =>
              setSections((old) =>
                old.map((x, n) =>
                  n === i ? { ...x, title: e.target.value } : x,
                ),
              )
            }
          />
          <input
            aria-label={"候选章节篇幅" + (i + 1)}
            type="number"
            value={s.target_words}
            onChange={(e) =>
              setSections((old) =>
                old.map((x, n) =>
                  n === i ? { ...x, target_words: Number(e.target.value) } : x,
                ),
              )
            }
          />
          <textarea
            aria-label={"候选章节目标" + (i + 1)}
            value={s.goal ?? ""}
            onChange={(e) =>
              setSections((old) =>
                old.map((x, n) =>
                  n === i ? { ...x, goal: e.target.value } : x,
                ),
              )
            }
          />
        </div>
      ))}
      <Button
        variant="primary"
        onClick={() =>
          act(
            () =>
              send(
                "/projects/" + task.project_id + "/outline/accept-candidate",
                { task_id: task.id, sections, mode: "append" },
              ),
            "已追加可编辑章节，原有内容保留",
          )
        }
      >
        采用并追加这些章节
      </Button>
    </section>
  );
}
function ArchivedProjects() {
  const { tick, act } = useApp();
  const { data } = useLoad("/projects?include_archived=true", tick);
  const archived = (data ?? []).filter((p: Row) => p.archived);
  return archived.length ? (
    <section className="panel mt">
      <div className="section-heading">
        <h2>已归档项目</h2>
        <span>资料与版本仍保留</span>
      </div>
      {archived.map((p: Row) => (
        <div className="export-row" key={p.id}>
          <div className="grow">
            <strong>{p.title}</strong>
            <small>{date(p.updated_at)}</small>
          </div>
          <Button
            onClick={() =>
              act(
                () => send("/projects/" + p.id, { archived: 0 }, "PATCH"),
                "项目已恢复到当前列表",
              )
            }
          >
            恢复项目
          </Button>
        </div>
      ))}
    </section>
  ) : null;
}
function ResearchMap() {
  const { pid, tick, setPage } = useApp();
  const { data } = useLoad("/projects/" + pid + "/research-map", tick);
  return (
    <section className="panel mb">
      <div className="section-heading">
        <h2>研究问题—章节—证据</h2>
        <span>在编辑器中关联原文与研究事实</span>
      </div>
      <p className="muted mb">
        {data?.research_question || "请先在研究简报中明确研究问题。"}
      </p>
      {data?.sections?.length ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>章节</th>
              <th>有关联依据的段落</th>
              <th>原文 / 事实 / 产物</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {data.sections.map((s: Row) => (
              <tr key={s.section_id}>
                <td>
                  <button
                    className="text-button"
                    onClick={() => {
                      localStorage.setItem("paper_section", s.section_id);
                      setPage("editor");
                    }}
                  >
                    {s.title}
                  </button>
                </td>
                <td>{s.claim_count}</td>
                <td>
                  {s.source_ids.length} / {s.fact_ids.length} / {s.artifact_ids?.length ?? 0}
                </td>
                <td>
                  <Badge
                    value={
                      s.stale ? "待复查" : s.revision_id ? "已有正文" : "待写作"
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">建立提纲后显示研究材料覆盖情况。</p>
      )}
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

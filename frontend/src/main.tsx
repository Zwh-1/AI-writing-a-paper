import SentenceLocator from "./SentenceLocator";
import WritingSources from "./WritingSources";
import PaperVisualsButton from "./PaperVisuals";
import { lazy, Suspense } from "react";
import WorkspaceDialog from "./workspace/WorkspaceDialog";
import type { ViewMutationRecord } from "@tiptap/pm/view";
import OutlineWorkspace from "./outline/OutlineWorkspace";
import { PaperWritingButton, PaperTaskDialog } from "./paper/PaperWriting";
import DocumentPreview from "./paper/DocumentPreview";
const RulesWorkspace = lazy(() => import("./RulesWorkspace"));
const MaterialsWorkspace = lazy(() => import("./materials/MaterialsWorkspace"));
const AgentWriting = lazy(() => import("./AgentWriting"));
const ReferencesWorkspace = lazy(() => import("./references/ReferencesWorkspace"));
import GptFallbackSettings from "./GptFallbackSettings";
import ImageGenerationSettings from "./ImageGenerationSettings";
import DeepSeekSettings from "./DeepSeekSettings";
import ExpenseLedger from "./ExpenseLedger";
import { SOURCE_NAVIGATION, type SourceLocation } from './workspace/sourceNavigation';
import { MaterialSummary } from "./MaterialSummary";
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
import { Table, TableView } from "@tiptap/extension-table";
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
  Wallet,
} from "lucide-react";
import "./styles.css";
import { LockedNodes } from "./editorLocks";
type Row = Record<string, any>;
import { apiUrl, api, send, setCsrf } from "./client";
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
  acknowledged: "已核实",
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
      className={`badge ${tone ?? (["succeeded", "ready", "reviewed", "confirmed", "pass", "human_confirmed", "acknowledged"].includes(value) ? "green" : ["failed", "fail", "outcome_unknown"].includes(value) ? "red" : ["running", "queued", "candidate", "unreviewed", "unknown", "pending"].includes(value) ? "amber" : "")}`}
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
      <section className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="关闭窗口">
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
const taskVerified = (task: Row) =>
  Boolean(task.acknowledged_at) || task.status === "acknowledged";
function taskGroups(tasks: Row[]) {
  const groups: { task: Row; count: number; ids: string[] }[] = [];
  const index = new Map<string, number>();
  for (const task of tasks) {
    if (["succeeded", "cancelled"].includes(task.status)) continue;
    const grouped = ["failed", "budget_blocked", "incomplete"].includes(task.status) && !task.acknowledged_at;
    const key = grouped ? `${task.kind}:${task.status}:${task.error ?? task.phase ?? ""}` : task.id;
    const existing = index.get(key);
    if (existing === undefined) {
      index.set(key, groups.length);
      groups.push({ task, count: 1, ids: [task.id] });
    } else {
      groups[existing].count += 1;
      groups[existing].ids.push(task.id);
    }
  }
  // 已核实的条目降到列表末尾：仍可回看和重试，但不再占用待处理的位置。
  return [
    ...groups.filter((g) => !g.task.acknowledged_at),
    ...groups.filter((g) => g.task.acknowledged_at),
  ];
}
function formValues(e: FormEvent<HTMLFormElement>) {
  e.preventDefault();
  return Object.fromEntries(new FormData(e.currentTarget).entries()) as Record<
    string,
    string
  >;
}
const sourceNavPages = [
  { id: "materials", label: "资料中心" },
  { id: "rules", label: "论文规则" },
  { id: "references", label: "文献与知识" },
] as const;
type SourceNavPage = (typeof sourceNavPages)[number]["id"];
const isSourcePage = (value: string): value is SourceNavPage =>
  sourceNavPages.some((item) => item.id === value);
const sourcePageLabel = (value: string) =>
  sourceNavPages.find((item) => item.id === value)?.label;
const sourceSubPageKey = (pid: string) => `paper-source-subpage:${pid}`;
const nav: {
  id: string;
  label: string;
  Icon: typeof LayoutDashboard;
  pages?: readonly { id: string; label: string }[];
}[] = [
  { id: "overview", label: "项目概览", Icon: LayoutDashboard },
  {
    id: "materials",
    label: "资料与文献",
    Icon: Library,
    pages: sourceNavPages,
  },
  { id: "outline", label: "提纲与计划", Icon: ListTree },
  { id: "editor", label: "写作工作台", Icon: PenLine },
  { id: "agent-writing", label: "有据写作", Icon: BookOpen },
  { id: "research", label: "研究与成果", Icon: FlaskConical },
  { id: "review", label: "修改与检查", Icon: ClipboardCheck },
  { id: "exports", label: "排版与交付", Icon: Download },
  { id: "advanced", label: "高级研究与协作", Icon: Activity },
];
function navLabel(page: string) {
  const entry = nav.find((item) =>
    item.pages
      ? item.pages.some((sub) => sub.id === page)
      : item.id === page,
  );
  if (!entry) return "";
  return entry.pages ? `${entry.label} · ${sourcePageLabel(page)}` : entry.label;
}
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
  useEffect(() => {
    const navigate = (event: Event) => {
      const detail = (event as CustomEvent<{ pid: string; location: SourceLocation }>).detail;
      if (detail?.pid === pid && ['materials','rules','references'].includes(detail.location?.page)) setPage(detail.location.page);
    };
    window.addEventListener(SOURCE_NAVIGATION, navigate);
    return () => window.removeEventListener(SOURCE_NAVIGATION, navigate);
  }, [pid]);
  useEffect(() => {
    if (pid && isSourcePage(page))
      sessionStorage.setItem(sourceSubPageKey(pid), page);
  }, [pid, page]);
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
  const liveProjectVersion = useRef({pid:"",version:""});
  useEffect(() => {
    if (!ready || !pid) return;
    let active=true, polling=false, timer: ReturnType<typeof setTimeout>;
    const schedule=(delay:number)=>{clearTimeout(timer);timer=setTimeout(()=>void poll(),delay);};
    const poll=async()=>{
      if(polling||!active)return;polling=true;
      try {
        const value=await api(`/projects/${pid}/writing-live?stamp=true`);
        if(!active)return;
        const previous=liveProjectVersion.current;
        if(previous.pid===pid&&previous.version&&previous.version!==value.version)setTick(t=>t+1);
        liveProjectVersion.current={pid,version:value.version};
      } catch {} finally {polling=false;if(active)schedule(15000);}
    };
    const onFocus=()=>schedule(2000);
    schedule(5000);
    window.addEventListener("focus",onFocus);
    return()=>{active=false;clearTimeout(timer);window.removeEventListener("focus",onFocus);};
  },[ready,pid]);
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
      api("/tasks?summary=true&active_only=true" + (pid ? "&project_id=" + pid : ""), { signal: controller.signal })
        .then((d) => {
          if (!alive) return;
          const visible = d.filter((t: Row) => !["succeeded", "cancelled"].includes(t.status));
          setAllTasks(visible);
          const sig = visible.map((t: Row) => t.id + t.status).join();
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
  const groupedTasks = taskGroups(allTasks);
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
            {nav.map(({ id, label, Icon, pages }) => {
              const current = pages?.some((item) => item.id === page)
                ? page
                : "";
              const active = pages ? !!current : page === id;
              return (
                <button
                  key={id}
                  className={active ? "active" : ""}
                  aria-current={active ? "page" : undefined}
                  title={
                    pages
                      ? `${label}：${pages.map((item) => item.label).join(" / ")}`
                      : label
                  }
                  onClick={() => {
                    if (!pages) return setPage(id);
                    if (current) return;
                    const saved = sessionStorage.getItem(
                      sourceSubPageKey(pid),
                    );
                    setPage(
                      pages.find((item) => item.id === saved)?.id ??
                        pages[0].id,
                    );
                  }}
                >
                  <Icon size={18} />
                  <span className="nav-copy">
                    {label}
                    {current && <small>{sourcePageLabel(current)}</small>}
                  </span>
                  {active && <span className="nav-dot" />}
                </button>
              );
            })}
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
                {page === "settings" ? "接口与设置" : navLabel(page)}
              </span>
            </div>
            <div className="topbar-right">
              <BudgetEntry />
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
                {page === "agent-writing" && <Suspense fallback={<p>正在打开有据写作…</p>}><AgentWriting pid={pid} tick={tick} onOpenEditor={(sectionId)=>{localStorage.setItem("paper_section",sectionId);setPage("editor");}} onOpenResearch={()=>setPage("research")} onOpenProject={()=>setPage("overview")} onOpenSettings={()=>setPage("settings")} onOpenRules={()=>setPage("rules")} /></Suspense>}
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
          {groupedTasks.length ? (
            groupedTasks.map(({ task: t, count, ids }) => (
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
                        write_paper: "整篇论文写作",
                        paper_visual: "论文图表候选",
                        paper_image: "GPT 图片生成与修改",
                        review: "论文检查",
                        export: "导出论文",
                        backup: "创建备份",
                        ocr: "API图片识别",
                        ocr_local: "本地页面OCR",
                        ocr_local_batch: "本地批量OCR",
                        rules_ai: "AI结构化规则整理",
                        materials_rules_autopilot: "资料与规则 AI 托管",
                        autopilot_exception_repair: "修复资料与规则例外",
                        outline_ai: "规划提纲",
                        knowledge_index: "更新文献知识库",
                        evidence_knowledge_build: "构建结构化证据知识库",
                        provider_probe: "验证模型生成",
                        provider_image_probe: "验证模型图片识别",
                      } as Row
                    )[t.kind] ?? t.kind}
                  </strong>
                  <small>
                    {t.phase || "等待执行"} · {date(t.created_at)}
                  </small>
                  {count > 1 && <small className="task-group-note">相同问题任务 {count} 个，已合并显示</small>}
                  {t.acknowledged_at && (
                    <small className="task-group-note">
                      已于 {date(t.acknowledged_at)} 人工核实
                      {t.acknowledged_note ? "：" + t.acknowledged_note : ""}，通知已关闭，任务记录仍保留
                    </small>
                  )}
                  {t.error && <p className="error-text">{t.error}</p>}
                </div>
                <Badge value={taskVerified(t) ? "acknowledged" : t.status} />
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
                {!taskVerified(t) &&
                  ["failed", "incomplete", "budget_blocked", "cancelled", "outcome_unknown"].includes(t.status) && (
                  <Button
                    onClick={() => {
                      if (
                        !confirm(
                          t.status === "outcome_unknown"
                            ? "请确认已核对服务方账单或远端任务状态；标记后不再把它显示为待处理警告。"
                            : `标记后${count > 1 ? `这 ${count} 条同因任务` : "这一条"}不再作为待处理问题提醒，任务结果、费用记录和失败原因都会保留，之后仍可重试。确认已核对失败原因？`,
                        )
                      )
                        return;
                      act(
                        async () => {
                          for (const id of ids) await send("/tasks/" + id + "/acknowledge", {});
                          return true;
                        },
                        count > 1 ? `已核实 ${count} 条任务` : "已标记为核实",
                      );
                    }}
                  >
                    已核实
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
                          "write_paper",
                            "ocr",
                            "outline_ai",
                            "semantic_review",
                            "support_check",
                            "provider_probe",
                          ].includes(t.kind)) ||
                        confirm(
                          t.kind === "write_paper"
                            ? "整篇任务会重新生成未通过章节契约校验的章节。旧请求可能已计费，请先核实账单；确认继续？"
                            : "远端可能已执行，重试可能重复计费。确认已核实并继续？",
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
                    act(() => send("/tasks/" + t.id + "/retry", {provider_profile:"gpt",acknowledge_possible_duplicate_charge:true,...(t.kind === "rules_ai" ? {retry_unknown:true,retry_invalid:true} : {})}), "已使用 GPT 重新排队，并续接未完成范围");
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
        taskDetail.kind === "write_paper" ? <PaperTaskDialog pid={pid} task={taskDetail} act={act} onChanged={refresh} onClose={() => setTaskDetail(null)} /> : <TaskDetail task={taskDetail} onClose={() => setTaskDetail(null)} />
      )}
    </AppContext.Provider>
  );
}
const money = (value: unknown) => Number(value ?? 0).toFixed(2);
function BudgetDialog({
  pid,
  data,
  onClose,
}: {
  pid: string;
  data: Row | null;
  onClose: () => void;
}) {
  const { act } = useApp();
  const limit = Number(data?.limit ?? 0);
  const used = Number(data?.used ?? 0);
  const available = Number(data?.available ?? 0);
  const currency = String(data?.currency ?? "USD");
  const [value, setValue] = useState(String(limit));
  const [busy, setBusy] = useState(false);
  const amount = Number(value);
  const valid = Number.isFinite(amount) && amount >= 0;
  const unchanged = Math.abs(amount - limit) < 1e-9;
  const add = (step: number) =>
    setValue(String(Math.round((limit + step) * 1e6) / 1e6));
  const save = async () => {
    if (!valid || busy) return;
    setBusy(true);
    try {
      const d = await act(
        () => send("/projects/" + pid, { budget: amount }, "PATCH"),
        `项目预算已更新为 ${money(amount)} ${currency}`,
      );
      if (d) onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="项目预算" onClose={onClose}>
      <div className="budget-summary">
        <div>
          <span>项目总预算</span>
          <strong>
            {money(limit)} {currency}
          </strong>
        </div>
        <div>
          <span>已占用</span>
          <strong>
            {money(used)} {currency}
          </strong>
        </div>
        <div className={available <= 0 ? "alert" : ""}>
          <span>可用</span>
          <strong>
            {money(available)} {currency}
          </strong>
        </div>
      </div>
      <p className="muted">
        可用 = 项目总预算 − 已占用。已占用计入已完成、无效完成和手动结算的费用；发送前失败与被拒绝的请求不计入。写作、规则整理等任务的本次预算不能超过「可用」。
      </p>
      {Number(data?.unknown_reserved ?? 0) > 0 && (
        <p className="muted">
          另有待核实预留 {money(data?.unknown_reserved)} {currency}
          {Number(data?.unresolved_calls ?? 0) > 0
            ? `，来自 ${data?.unresolved_calls} 次结果未知的调用`
            : ""}
          。可在「费用依据」中核对服务商账单后释放。
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field
          label={`新的项目总预算（${currency}）`}
          hint="只是本机控制的上限，不会向服务商充值；填小数也可以。"
        >
          <input
            autoFocus
            type="number"
            min="0"
            step="0.01"
            aria-label="新的项目总预算"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        <div className="actions mb">
          {[5, 10, 20].map((step) => (
            <Button key={step} onClick={() => add(step)}>
              +{step}
            </Button>
          ))}
          <Button onClick={() => setValue(String(Math.round((used + 10) * 1e6) / 1e6))}>
            已占用 +10
          </Button>
        </div>
        {valid && amount < used && (
          <p className="budget-warn" role="alert">
            低于已占用 {money(used)} {currency}
            ，保存后可用额度会变成 0，写作等任务将无法发起。
          </p>
        )}
        {!valid && (
          <p className="budget-warn" role="alert">
            请填写不小于 0 的金额。
          </p>
        )}
        <div className="actions">
          <Button variant="primary" type="submit" disabled={!valid || busy || unchanged}>
            <Save size={15} />
            {busy ? "保存中…" : "保存预算"}
          </Button>
          <Button onClick={onClose}>取消</Button>
        </div>
      </form>
    </Modal>
  );
}
function BudgetEntry() {
  const { pid, tick } = useApp();
  const [open, setOpen] = useState(false);
  const { data } = useLoad(pid ? "/projects/" + pid + "/budget" : null, tick);
  if (!pid) return null;
  const available = Number(data?.available ?? 0);
  const limit = Number(data?.limit ?? 0);
  const currency = String(data?.currency ?? "USD");
  const low = !!data && available <= Math.max(2, limit * 0.1);
  return (
    <>
      <button
        className={`budget-trigger${low ? " low" : ""}`}
        aria-label="项目预算"
        title={`项目预算 ${money(limit)} ${currency}，可用 ${money(available)} ${currency}`}
        onClick={() => setOpen(true)}
      >
        <Wallet size={16} />
        <span>预算</span>
        <strong>
          {money(available)} / {money(limit)} {currency}
        </strong>
      </button>
      {open && <BudgetDialog pid={pid} data={data} onClose={() => setOpen(false)} />}
    </>
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
        <a className="btn" href={apiUrl("/files/" + file.id + "/download")}>
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
          {showOriginal && file.extension === ".pptx" && <img className="material-original-page" alt={"第" + p.page_number + "页原版式预览"} src={apiUrl("/pages/" + p.id + "/office-preview")} onError={() => {setShowOriginal(false);notify("整页预览暂不可用，请查看原文件或重试本地渲染", true);}} />}
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
                src={apiUrl("/pages/" + p.id + "/preview")}
              />
            )}
            <div className="source-images">
              {images.map((im: Row) => (
                <a
                  key={im.sha256}
                  href={apiUrl("/images/" + im.sha256)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <img
                    loading="lazy"
                    alt="幻灯片原始图片"
                    src={apiUrl("/images/" + im.sha256)}
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
          evidenceIds: { default: [], renderHTML: () => ({}) },
          knowledgeUnitIds: { default: [], renderHTML: () => ({}) },
          documentReadingIds: { default: [], renderHTML: () => ({}) },
          knowledgeClaimIds: { default: [], renderHTML: () => ({}) },
          wikiVersionIds: { default: [], renderHTML: () => ({}) },
          wikiBlockIds: { default: [], renderHTML: () => ({}) },
          describesFigureId: { default: "", parseHTML: (e:HTMLElement)=>e.getAttribute("data-describes-figure")||"", renderHTML:(a:Row)=>a.describesFigureId?{"data-describes-figure":a.describesFigureId}:{} },
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
    return { citationId: { default: "",parseHTML:(e:HTMLElement)=>e.getAttribute("data-citation")||"" }, nodeId: { default: null,parseHTML:(e:HTMLElement)=>e.getAttribute("data-node-id") } };
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
      citationLabels[HTMLAttributes.citationId] ? `[${citationLabels[HTMLAttributes.citationId]}]` : "[引用待核对]",
    ];
  },
  addNodeView(){return ({node})=>{let current=node;const dom=document.createElement("span");dom.className="citation-node";dom.contentEditable="false";const sync=()=>{dom.textContent=citationLabels[current.attrs.citationId]?`[${citationLabels[current.attrs.citationId]}]`:"[引用待核对]";dom.dataset.citation=current.attrs.citationId;dom.dataset.nodeId=current.attrs.nodeId||"";dom.title=citationTitles[current.attrs.citationId]||"文献信息待核对";};sync();window.addEventListener("paper-reference-index",sync);return {dom,update(next:any){if(next.type.name!=="citation")return false;current=next;sync();return true;},ignoreMutation:()=>true,destroy(){window.removeEventListener("paper-reference-index",sync);}};}},
});
let citationLabels:Record<string,number>={};
let citationTitles:Record<string,string>={};
let artifactImages: Record<string, string> = {};
let figureLabels: Record<string,string> = {};
let activeFigureSection = "";
function figureReferenceLabel(attrs:Row){
  if(attrs.targetSectionId)return figureLabels[attrs.targetSectionId+":"+attrs.targetId];
  const matches=Object.entries(figureLabels).filter(([key])=>key.includes(":")&&key.endsWith(":"+attrs.targetId));
  return matches.length===1?matches[0][1]:undefined;
}
const CrossReference = Node.create({
  name:"crossReference",inline:true,group:"inline",atom:true,
  addAttributes(){return {targetId:{default:"",parseHTML:(e:HTMLElement)=>e.getAttribute("data-figure-reference")||""},targetSectionId:{default:"",parseHTML:(e:HTMLElement)=>e.getAttribute("data-target-section")||""},nodeId:{default:null,parseHTML:(e:HTMLElement)=>e.getAttribute("data-node-id")}}},
  parseHTML(){return [{tag:"span[data-figure-reference]"}]},
  renderHTML({HTMLAttributes}){return ["span",{"data-figure-reference":HTMLAttributes.targetId,"data-target-section":HTMLAttributes.targetSectionId,class:"paper-crossref"},figureReferenceLabel(HTMLAttributes)||"[图表引用待核对]"]},
  addNodeView(){return ({node})=>{let current=node;const dom=document.createElement("span");dom.className="paper-crossref";dom.contentEditable="false";const sync=()=>{dom.textContent=figureReferenceLabel(current.attrs)||"[图表引用待核对]";dom.dataset.figureReference=current.attrs.targetId;dom.dataset.targetSection=current.attrs.targetSectionId;};sync();window.addEventListener("paper-figure-index",sync);return {dom,update(next:any){if(next.type.name!=="crossReference")return false;current=next;sync();return true;},ignoreMutation:()=>true,destroy(){window.removeEventListener("paper-figure-index",sync)}};}}
});
class ThesisTableView extends TableView {
  private indexListener=()=>this.syncAttributes();
  constructor(...args: ConstructorParameters<typeof TableView>) { super(...args); this.syncAttributes();window.addEventListener("paper-figure-index",this.indexListener); }
  syncAttributes() {
    this.table.dataset.tableStyle = this.node.attrs.tableStyle || "grid";
    const label=figureLabels[activeFigureSection+":"+this.node.attrs.nodeId];
    this.table.dataset.caption = this.node.attrs.caption||"";
    if(this.table.dataset.caption){const caption=this.table.caption||this.table.createCaption();caption.textContent=(label?label+" ":"")+this.table.dataset.caption;caption.contentEditable="false";}
    this.table.dataset.note = this.node.attrs.note || "";
  }
  update(node: Parameters<TableView["update"]>[0]) {
    const updated = super.update(node);
    if (updated) this.syncAttributes();
    return updated;
  }
  ignoreMutation(mutation:ViewMutationRecord){if(this.table.caption&&(mutation.target===this.table.caption||this.table.caption.contains(mutation.target)))return true;if(mutation.type==="selection")return false;if(mutation.type==="childList"&&mutation.target===this.table&&[...Array.from(mutation.addedNodes),...Array.from(mutation.removedNodes)].every(node=>node.nodeName==="CAPTION"))return true;return super.ignoreMutation(mutation);}
  destroy(){window.removeEventListener("paper-figure-index",this.indexListener);}
}
const ThesisTable = Table.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      tableStyle: { default: "grid", parseHTML: (element: HTMLElement) => element.getAttribute("data-table-style") || "grid", renderHTML: (attrs: Row) => ({ "data-table-style": attrs.tableStyle }) },
      caption: { default: "", parseHTML: (element: HTMLElement) => element.getAttribute("data-caption") || "", renderHTML: (attrs: Row) => ({ "data-caption": attrs.caption }) },
      note: { default: "", parseHTML: (element: HTMLElement) => element.getAttribute("data-note") || "", renderHTML: (attrs: Row) => ({ "data-note": attrs.note }) },
      artifactId: { default: null, parseHTML: (element: HTMLElement) => element.getAttribute("data-table-artifact"), renderHTML: (attrs: Row) => attrs.artifactId ? ({ "data-table-artifact": attrs.artifactId }) : ({}) },
    };
  },
});
const Figure = Node.create({
  name: "figure",
  group: "block",
  atom: true,
  addAttributes() {
    return {
      artifactId: { default: "" },
      caption: { default: "" },
      nodeId: { default: null },
      diagramType: { default: "" }, figurePurpose: { default: "" }, explanationNodeId: { default: "" }, relatedSectionIds: { default: [] },
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
  addNodeView(){return ({node})=>{let current=node;const dom=document.createElement("figure"),img=document.createElement("img"),caption=document.createElement("figcaption");dom.append(img,caption);const sync=()=>{dom.dataset.artifact=current.attrs.artifactId;img.src=artifactImages[current.attrs.artifactId]??"";img.alt=current.attrs.caption;const label=figureLabels[activeFigureSection+":"+current.attrs.nodeId];caption.textContent=(label?label+" ":"")+(current.attrs.caption||"研究图表");};sync();window.addEventListener("paper-figure-index",sync);return {dom,update(next:any){if(next.type.name!=="figure")return false;current=next;sync();return true;},ignoreMutation:()=>true,destroy(){window.removeEventListener("paper-figure-index",sync)}};}}
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
    [sourceFile, setSourceFile] = useState<Row | null>(null),
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
          title="先建立论文提纲"
          detail="整篇或按章写作都使用提纲安排内容，章节目标与适用规则也便于你统一核对结果。"
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
            <PaperWritingButton pid={pid} act={act} onChanged={refresh} />
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
              <div className="section-heading"><h3>本节研究链路</h3>{current.revision_id||current.stale?<Badge value={current.stale ? "stale" : "draft"} />:<span className="badge">待写作</span>}</div>
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
            <WritingSources key={pid + ":" + sid} pid={pid} sid={sid} tick={tick} onOpen={file => setSourceFile({ ...file, ...writingLink?.materials?.find((item: Row) => item.id === file.id) })} onMaterials={() => setPage("materials")} location={sourceLocation} />
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
      {sourceFile && <SourceViewer file={sourceFile} onClose={() => setSourceFile(null)} />}
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
function editorDocument(document:Row){
  const body=document?.content??[];
  return body.length&&body[body.length-1].type!=="paragraph"?{...document,content:[...body,{type:"paragraph",attrs:{nodeId:crypto.randomUUID()}}]}:document;
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
  const { act, notify, pid, tick, refresh } = useApp();
  const isEnglishAbstract = content.section.kind === "abstract_en";
  const [contextRevision, setContextRevision] = useState(0);
  const writingContext = useLoad(isEnglishAbstract ? null : "/sections/" + content.section.id + "/writing-context", tick + contextRevision);
  const liveInputs=useLoad(`/projects/${pid}/writing-live`,tick+contextRevision);
  const [citationRevision,setCitationRevision]=useState(0);
  const [remoteConflict,setRemoteConflict]=useState(false);
  const manuscript = writingContext.data?.section_id === content.section.id ? writingContext.data : null;
  activeFigureSection=content.section.id;
  const figureIndex=useLoad("/projects/"+pid+"/figure-index",tick+contextRevision);
  const [crossTarget,setCrossTarget]=useState("");
  useEffect(()=>{const rows=figureIndex.data?.figures??[];figureLabels={};for(const row of rows){figureLabels[row.section_id+":"+row.node_id]=row.label;figureLabels[row.node_id]=row.label;}window.dispatchEvent(new Event("paper-figure-index"));},[figureIndex.data,artifacts]);
  const revision = useRef(content.section.current_revision_id),
    dirty = useRef(false),
    saving = useRef(false),
    generation = useRef(0),
    timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seenRevisions=useRef(new Set([content.section.current_revision_id]));
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
  artifactImages = { ...artifactImages, ...Object.fromEntries(
    artifacts
      .filter((a: Row) => a.sha256 && a.kind === "figure")
      .map((a: Row) => [a.id, apiUrl("/images/" + a.sha256)]),
  ) };
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
      seenRevisions.current.add(d.id);
      setContextRevision(value => value + 1);
      if (generation.current === version) {
        dirty.current = false;
        ed.commands.setContent(editorDocument(parse(d.document)), { emitUpdate: false });
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
      CrossReference,
      Figure,
      Formula,
      Footnote,
      ThesisTable.configure({ resizable: true, View: ThesisTableView }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: editorDocument(content.document),
    editorProps: {
      attributes: { class: "paper-editor", "aria-label": "论文正文编辑器" },
    },
    onUpdate: ({ editor }) => {
      setCitationRevision(value=>value+1);
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
  useEffect(()=>{
    if(!editor||!liveInputs.data)return;
    const live=liveInputs.data;const numbers:Record<string,number>={};
    const collect=(node:Row):string[]=>[...(node.type==="citation"?[node.attrs.citationId]:[]),...(node.content??[]).flatMap(collect)];
    for(const section of live.sections??[]){const ids=section.id===content.section.id?collect(editor.getJSON()):section.citation_ids??[];for(const id of ids){if(!numbers[id])numbers[id]=Object.keys(numbers).length+1;}}
    citationLabels=numbers;citationTitles=Object.fromEntries((live.references??[]).map((r:Row)=>[r.id,r.metadata?.title||r.metadata?.citation_raw||"文献信息待核对"]));
    window.dispatchEvent(new Event("paper-reference-index"));
    const fmt=live.formats_by_section?.[content.section.id]?.config||live.format?.config;
    if(fmt){
      const dom=editor.view.dom;dom.style.fontFamily=`"${fmt.latin_font}","${fmt.font}",serif`;dom.style.fontSize=`${fmt.font_size}pt`;
      dom.style.lineHeight=fmt.line_spacing_points?`${fmt.line_spacing_points}pt`:String(fmt.line_spacing);dom.style.textAlign=fmt.alignment||"justify";
      dom.style.setProperty("--paper-first-line",`${fmt.first_line_chars}em`);dom.style.setProperty("--paper-heading-font",`"${fmt.heading_font}",serif`);
      dom.style.setProperty("--paper-citation-position",fmt.reference_superscript?"super":"baseline");dom.style.setProperty("--paper-citation-size",fmt.reference_superscript?".75em":"1em");
      for(const level of [1,2,3]){
        const heading=live.format?.components?.[`heading_${level}`]??{};
        dom.style.setProperty(`--paper-h${level}-font`,`"${heading.font||fmt.heading_font}",serif`);
        dom.style.setProperty(`--paper-h${level}-size`,`${heading.font_size??({1:16,2:14,3:12} as Record<number,number>)[level]}pt`);
        dom.style.setProperty(`--paper-h${level}-align`,heading.alignment||"left");dom.style.setProperty(`--paper-h${level}-indent`,`${heading.left_indent_chars??0}em`);
      }
    }
  },[editor,liveInputs.data,citationRevision]);
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
    if(content.section.current_revision_id!==revision.current&&seenRevisions.current.has(content.section.current_revision_id))return;
    if(content.section.current_revision_id!==revision.current&&(dirty.current||saving.current)){setRemoteConflict(true);return;}
    if (
      content.section.current_revision_id !== revision.current &&
      !dirty.current &&
      !saving.current
    ) {
      editor?.commands.setContent(editorDocument(content.document), { emitUpdate: false });
      revision.current = content.section.current_revision_id;
      seenRevisions.current.add(content.section.current_revision_id);setRemoteConflict(false);
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
      {!!content.section.stale && (
        <div className="alert compact">
          <AlertTriangle size={15} />
          {content.section.stale_reason}
        </div>
      )}
      <section className="ai-composer" aria-label="本节辅助写作">
        <div>
          <Sparkles size={17} />
          <strong>{isEnglishAbstract ? "英文摘要翻译" : "辅助本节写作"}</strong>
          <span>{isEnglishAbstract ? "直接翻译已保存的中文摘要" : "读取已保存全文后处理当前章"}</span>
        </div>
        {remoteConflict&&<p className="alert" role="alert">正文在其他窗口已更新，本机未保存修改已保留。请核对版本差异后再保存，避免覆盖新内容。</p>}
        {liveInputs.data&&<div className="writing-live-summary" aria-label="写作关联依据"><span>已连接{liveInputs.data.repository?.target_snapshot_id?"项目代码 · ":""}{liveInputs.data.material_count} 份资料 · {liveInputs.data.reference_count} 篇文献 · {liveInputs.data.figures?.length??0} 个正文图表</span><small>资料与格式变更会同步刷新，生成时读取当前项目依据。</small>{liveInputs.data.repository?.target_snapshot_id&&<Button onClick={()=>act(()=>send(`/projects/${pid}/writing-live/refresh-project`),"项目依据已更新，后续写作将使用新版本")}>更新项目依据</Button>}</div>}
        {!isEnglishAbstract && <div className="writing-context" aria-label="全文上下文">
          {manuscript?.can_load ? <details>
            <summary>{manuscript.saved_sections ? `全文上下文：${manuscript.saved_sections}/${manuscript.total_sections} 章已有正文 · ${manuscript.characters.toLocaleString()} 字` : "全文上下文：尚无已保存正文，目前只有提纲与研究资料"}</summary>
            <p className="muted">生成、修改、润写、扩写、压缩均读取以下章节的完整已保存正文。只输出当前章或选区的候选。</p>
            <ul>{manuscript.sections.map((item: Row) => <li key={item.section_id}>{item.title} · {item.has_content ? `${item.characters.toLocaleString()} 字` : "尚无正文"}</li>)}</ul>
          </details> : <p className="muted" role="status">{manuscript?.message || writingContext.error || "正在检查已保存全文…"}</p>}
        </div>}
        {!isEnglishAbstract && operation === "polish" && <p className="muted" role="note">润写按独立规范改善措辞、句式与衔接，保留事实、数值、限定条件和引用。可在论文规则中单独勾选“润写”并发布适用条款。{!editor?.getText().trim() && "请先保存需要润写的正文。"}</p>}
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder={isEnglishAbstract ? "可补充专业术语的英文译法或英文表达偏好。" : operation === "polish" ? "例如：改善句式与衔接，保持原有事实、数值和结论。" : "例如：结合已有资料补充方案比较，保留所有已确认事实。"}
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
              ["polish", "润写"],
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
            disabled={!isEnglishAbstract && ((!!manuscript && !manuscript.can_load) || (operation === "polish" && !editor?.getText().trim()))}
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
                if (!isEnglishAbstract) {
                  try {
                    const latestContext = await api("/sections/" + content.section.id + "/writing-context");
                    writingContext.set(latestContext);
                    if (!latestContext.can_load) { notify(latestContext.message, true); return; }
                  } catch (error) { notify((error as Error).message, true); return; }
                }
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
            {isEnglishAbstract ? "翻译中文摘要" : operation === "polish" ? "生成润写候选" : "生成候选稿"}
          </Button>
        </div>
      </section>
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
        <PaperVisualsButton pid={pid} sectionId={content.section.id} onPrepare={saveNow} getAnchor={() => {
          if (!editor) return undefined;
          let anchor: string | undefined;
          editor.state.doc.forEach((block, pos) => { if (pos <= editor.state.selection.from && pos + block.nodeSize > editor.state.selection.from) anchor = block.attrs.nodeId; });
          return anchor;
        }} onSaved={refresh} onInsert={async (node, asset) => {
          if (!editor || editor.isDestroyed) return false;
          if (parse(asset.metadata).render_engine === "gpt-image-2") {
            if (!(await saveNow())) return false;
            const result = await send(`/sections/${content.section.id}/paper-visuals/${asset.id}/insert`, {
              base_revision_id: revision.current, review_id: asset.alignment_review_id,
            });
            revision.current = result.revision.id;
            dirty.current = false;
            artifactImages[asset.id] = apiUrl("/images/" + asset.sha256);
            editor.commands.setContent(parse(result.revision.document), { emitUpdate: false });
            localStorage.removeItem("paper_emergency_" + content.section.id);
            updateState("已保存");
            setContextRevision(value => value + 1);
            return true;
          }
          let alreadyInserted = false;
          editor.state.doc.descendants(current => { if (current.attrs.nodeId === node.attrs?.nodeId) alreadyInserted = true; });
          if (alreadyInserted) return await saveNow();
          if (asset.kind === "figure") artifactImages[asset.id] = apiUrl("/images/" + asset.sha256);
          const insertNodes=asset.insert_nodes??[node];
          const inserted = editor.isActive("table")
            ? editor.chain().focus().insertContentAt(editor.state.selection.$from.after(1), insertNodes).run()
            : editor.chain().focus().insertContent(insertNodes).run();
          if (!inserted) return false;
          return await saveNow();
        }} />
        <select aria-label="选择图表交叉引用" value={crossTarget} onChange={e=>setCrossTarget(e.target.value)}><option value="">选择图表交叉引用…</option>{(figureIndex.data?.figures??[]).map((row:Row)=><option key={row.section_id+":"+row.node_id} value={row.section_id+":"+row.node_id}>{row.label} {row.caption||"【题名待补】"}</option>)}</select>
        <Button disabled={!crossTarget} onClick={()=>{const split=crossTarget.indexOf(":"),targetSectionId=crossTarget.slice(0,split),targetId=crossTarget.slice(split+1);if(!editor?.chain().focus().insertContent({type:"crossReference",attrs:{targetSectionId,targetId,nodeId:crypto.randomUUID()}}).run())notify("请将光标放入可编辑的正文段落后插入引用",true);}}>插入图表引用</Button>
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

    </>
  );
}
function TaskDetail({ task, onClose }: { task: Row; onClose: () => void }) {
  const { pid, act, refresh, notify } = useApp();
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
  if (t.kind === "paper_image") {
    const asset = result.asset;
    return <Modal title="gpt-image-2 图片结果" wide onClose={onClose}>
      <div className="inline"><Badge value={t.status} /><strong>{t.phase || "GPT 生图与修改"}</strong></div>
      {t.error && <div className="alert">{t.error}</div>}
      {asset && <><img alt={asset.name} src={apiUrl("/images/" + asset.sha256)} style={{display:"block",maxWidth:"100%",maxHeight:540,objectFit:"contain",margin:"16px auto"}} /><p>{asset.name} · {parse(asset.metadata).width} × {parse(asset.metadata).height} px</p><p>图片保存在“论文图表”的本项目列表中，核对后可从写作工作台插入正文。</p><a className="btn" href={apiUrl(`/projects/${pid}/paper-visuals/${asset.id}/download?format=png`)} download>下载 PNG</a></>}
      {["budget_blocked","failed","outcome_unknown","cancelled"].includes(t.status) && <div className="actions"><Button onClick={() => void act(() => send(`/tasks/${t.id}/retry`,{acknowledge_possible_duplicate_charge:true}),"已重新排队 gpt-image-2 图片任务")}>重新生图（可能再次计费）</Button><Button onClick={() => void act(async () => {const bill=await send(`/projects/${pid}/tasks/billing-reconcile`,{profile:"image-generation"});if(!bill.reconciled)throw new Error(bill.reason||"账单未能匹配，继续保留费用预留");return bill;},"独立生图接口账单已核对")}>核对生图接口账单</Button></div>}
      <details><summary>查看生图记录</summary><pre className="source-content">{JSON.stringify(result,null,2)}</pre></details>
    </Modal>;
  }
  if (t.kind === "materials_rules_autopilot" || t.kind === "autopilot_exception_repair") {
    const materials = result.materials ?? {};
    const rules = result.rules ?? {};
    const budget = result.budget ?? {};
    const recover = () => act(
      () => send("/tasks/" + t.id + "/retry", { acknowledge_possible_duplicate_charge: true }),
      "已保存当前结果，并重新排队恢复未完成范围",
    );
    return <Modal title="任务结果与托管恢复" wide onClose={onClose}>
      <div className="inline"><Badge value={t.status} /><strong>{t.phase || "资料与规则 AI 托管"}</strong></div>
      {t.error && <div className="alert">{t.error}</div>}
      <div className="task-recovery-summary">
        <div><strong>资料</strong><span>{materials.automatic_completed ?? 0}/{materials.total ?? 0} 已完成</span><small>AI 覆盖 {materials.ai_percent ?? 0}%</small></div>
        <div><strong>规则</strong><span>{rules.automatic_completed ?? 0}/{rules.total ?? 0} 已完成</span><small>AI 覆盖 {rules.ai_percent ?? 0}%</small></div>
        <div><strong>预算</strong><span>预算占用 {Number(budget.used ?? 0).toFixed(2)}</span><small>待核实预留 {Number(budget.unknown_reserved ?? 0).toFixed(2)}</small></div>
      </div>
      <div className="alert compact">已保存的资料总结、规则判定和原文依据会被复用。恢复操作只处理尚未完成的范围；远端未知请求不会自动重复发送。</div>
      {t.status !== "acknowledged" && !result.complete && <div className="actions"><Button variant="primary" onClick={recover}>恢复未完成范围</Button><Button onClick={() => onClose()}>稍后处理</Button></div>}
      <details><summary>查看原始托管结果</summary><pre className="source-content">{JSON.stringify(result,null,2)}</pre></details>
      <details><summary>查看任务输入</summary><pre className="source-content">{JSON.stringify(t.payload??{},null,2)}</pre></details>
    </Modal>;
  }
  if (t.kind === "rules_ai") {
    const unresolved = result.unresolved_items ?? [];
    const budget = result.budget ?? {};
    const recover = () => act(
      () => send("/tasks/" + t.id + "/retry", {
        acknowledge_possible_duplicate_charge: true,
        retry_unknown: true,
        retry_invalid: true,
      }),
      "已保存当前规则结果，并重新排队恢复未完成批次",
    );
    return <Modal title="规则整理结果与恢复" wide onClose={onClose}>
      <div className="inline"><Badge value={t.status} /><strong>{t.phase || "AI 结构化规则整理"}</strong></div>
      {t.error && <div className="alert">{t.error}</div>}
      <div className="task-recovery-summary">
        <div><strong>新增候选</strong><span>{result.added ?? 0} 条</span><small>本次新增 {result.added_this_attempt ?? 0} 条</small></div>
        <div><strong>原文片段</strong><span>{result.processed_source_units ?? 0}/{result.selected_source_units ?? 0}</span><small>批次 {result.completed_batches ?? 0}/{result.total_batches ?? 0}</small></div>
        <div><strong>待恢复</strong><span>{unresolved.length} 个</span><small>复用批次 {result.reused_batches ?? 0} 个</small></div>
      </div>
      {unresolved.length ? <div className="alert compact">已保存可用规则。以下原文片段没有得到完整远端结论，恢复时会复用已完成批次，只处理这些未完成范围。</div> : <div className="success-box"><CheckCircle2 size={20} /><span>当前结果没有列出未完成片段。</span></div>}
      {unresolved.length > 0 && <ul className="task-unresolved-list">{unresolved.slice(0,20).map((item:Row,i:number)=><li key={item.source_id||i}><strong>{item.source_id ? "来源 "+item.source_id.slice(0,10)+"…" : "未定位来源"}</strong><span>{item.state} · {item.error}</span></li>)}</ul>}
      {["incomplete","outcome_unknown","budget_blocked","failed"].includes(t.status) && <div className="actions"><Button variant="primary" onClick={recover}>恢复未完成批次</Button>{Boolean(budget.pending_bill_calls||budget.pending_bill_reserved||t.status==="budget_blocked")&&<Button onClick={()=>void act(async()=>{
        const billing=await send("/projects/"+pid+"/tasks/billing-reconcile",{profile:t.payload?.provider_profile==="gpt"?"gpt-fallback":"primary"});
        if(billing.budget)setT(current=>({...current,result:{...current.result,budget:billing.budget}}));
        if(!billing.reconciled)throw new Error(billing.reason||"账单尚未核对，已保留费用预留");
      },"账单核对完成，可用预算已刷新")}>核对账单并刷新预算</Button>}<Button onClick={onClose}>稍后处理</Button></div>}
      <details><summary>查看原始规则结果</summary><pre className="source-content">{JSON.stringify(result,null,2)}</pre></details>
      <details><summary>查看任务输入</summary><pre className="source-content">{JSON.stringify(t.payload??{},null,2)}</pre></details>
    </Modal>;
  }
  return (
    <Modal title="任务结果与候选审阅" wide onClose={onClose}>
      <div className="inline">
        <Badge value={t.status} />
        <span>{t.phase}</span>
      </div>
      {t.error && <div className="alert">{t.error}</div>}
      {result.sections && <CandidateOutline task={t} />}
      {result.polish_validation && <p className="muted">{result.polish_validation.passed ? `独立润写检查：已核对 ${result.polish_validation.paragraphs_checked} 个原段落及其数值、单位、限定标记和保留片段。采用前仍需核对原意。` : "独立润写检查未通过，以下结果需重新处理，不能直接采用。"}</p>}
      {!!result.polish_issues?.length && <div className="alert">润写中发现的待核对问题：{result.polish_issues.join("；")}</div>}
      {result.document && result.validation_error && (
        <div className="alert">{result.validation_error}</div>
      )}
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
                  <DocumentPreview document={{type:"doc",content:[n]}} sectionId={result.section_id} figures={result.figure_index??[]} assets={result.preview_assets??[]} citations={result.citation_numbers??{}}/>
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
                  apiUrl("/exports/" +
                  result.export_id +
                  "/files/" +
                  encodeURIComponent(name))
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
  const { pid, tick, act, setPage, refresh } = useApp();
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
            <PaperVisualsButton pid={pid} onSaved={refresh} />
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
                <img alt={a.name} src={apiUrl("/images/" + a.sha256)} />
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
                {format?.config.font_size} pt / {format?.config.line_spacing_points?`固定 ${format.config.line_spacing_points} 磅`:`${format?.config.line_spacing} 倍`}
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
                apiUrl("/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("论文.docx"))
              }
            >
              DOCX
              <Download size={14} />
            </a>
            <a
              className="btn"
              href={
                apiUrl("/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("检查报告.md"))
              }
            >
              检查报告
            </a>
            <a
              className="btn primary"
              href={
                apiUrl("/exports/" +
                x.id +
                "/files/" +
                encodeURIComponent("论文交付包.zip"))
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
            <a className="btn" href={apiUrl("/backups/" + b.id + "/download")}>
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
                cfg[k] = ["font", "latin_font", "heading_font","alignment"].includes(k)
                  ? v
                  : Number(v);
              cfg.reference_superscript = f.reference_superscript==="on";
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
                ["行距倍数（未启用固定行距时使用）", "line_spacing"],
                ["固定行距（磅；0 为不启用）", "line_spacing_points"],
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
                    defaultValue={format?.config[key]??""}
                  />
                </Field>
              ))}
              <Field label="段落对齐"><select name="alignment" defaultValue={format?.config.alignment??"justify"}><option value="justify">两端对齐</option><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></Field>
              <label className="checkbox"><input type="checkbox" name="reference_superscript" defaultChecked={format?.config.reference_superscript??true}/>文献编号使用上标</label>
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
  const { tick, pid, refresh } = useApp();
  const { data } = useLoad("/settings", tick);
  return (
    <>
      <PageTitle title="接口与设置" description="管理 DeepSeek 接口，选择当前使用的服务商与模型。" />
      <DeepSeekSettings onChanged={refresh} />
      <GptFallbackSettings pid={pid} />
      <ImageGenerationSettings />
      <div className="settings-layout">
        <section className="panel">
          <div className="section-heading"><h2>使用说明</h2><Settings size={17} /></div>
          <ol className="guide-list">
            <li>在 DeepSeek 接口列表中添加服务商地址、密钥与模型。</li>
            <li>点击接口右侧的切换按钮，标记为“使用中”的接口用于后续 DeepSeek 请求。</li>
            <li>论文写作、资料总结、规则整理与知识库共用当前 DeepSeek 接口。</li>
            <li>可在接口的更多操作中查询余额，在编辑窗口中检测模型。</li>
          </ol>
          <div className="notice compact"><ShieldCheck size={18} /><p>原件在本机。API 调用会将选定资料发送给当前服务商，每份资料可设置为“仅限本地”。</p></div>
          <ArchivedProjects />
        </section>
        <section className="panel">
          <h3>本机环境</h3>
          <dl className="format-list">
            <div><dt>版本</dt><dd>{data?.version}</dd></div>
            <div><dt>密钥存储</dt><dd>{data?.credential.storage}</dd></div>
            <div><dt>文档渲染</dt><dd>{data?.renderer_available ? "可用" : "未安装"}</dd></div>
          </dl>
          <details><summary>数据目录</summary><code className="path-text">{data?.data_directory}</code></details>
        </section>
      </div>
      {pid && <ExpenseLedger pid={pid} tick={tick} onChanged={refresh} />}
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

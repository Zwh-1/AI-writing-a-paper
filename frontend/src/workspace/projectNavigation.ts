import {
  Activity,
  BookOpen,
  ClipboardCheck,
  Download,
  FlaskConical,
  FolderOpen,
  Library,
  ListTree,
  PenLine,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export type ProjectWorkspacePage = {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
};
export type ProjectWorkspace = {
  id: string;
  label: string;
  description: string;
  note: string;
  icon: LucideIcon;
  pages: readonly ProjectWorkspacePage[];
};

export const projectWorkspaces: readonly ProjectWorkspace[] = [
  {
    id: "sources",
    label: "文献资料与规则",
    description: "同一份原件，贯穿整理、规则和研究",
    note: "分类和发布状态分别核对",
    icon: Library,
    pages: [
      { id: "materials", title: "资料中心", description: "原件、用途与解析", icon: FolderOpen },
      { id: "rules", title: "论文规则", description: "适用要求与发布", icon: ShieldCheck },
      { id: "references", title: "文献与知识", description: "书目、阅读与综合", icon: BookOpen },
    ],
  },
  {
    id: "writing",
    label: "提纲与写作",
    description: "从章节计划到正文，再核对每一处依据",
    note: "共用当前项目的章节与正文",
    icon: PenLine,
    pages: [
      { id: "outline", title: "提纲与计划", description: "章节结构、目标与依赖", icon: ListTree },
      { id: "editor", title: "写作工作台", description: "正文编辑、修改与版本", icon: PenLine },
      { id: "agent-writing", title: "有据写作", description: "章节任务、证据与候选核验", icon: BookOpen },
    ],
  },
  {
    id: "research",
    label: "研究与协作",
    description: "记录研究成果，连接讨论、创作与协作工具",
    note: "研究记录和资产保留在当前项目",
    icon: FlaskConical,
    pages: [
      { id: "research", title: "研究与成果", description: "事实、实现与测量依据", icon: FlaskConical },
      { id: "studio", title: "研究助手", description: "学术对话、随记与创作", icon: Sparkles },
      { id: "products", title: "产物与互通", description: "跨功能复用总结、分析与正文", icon: Library },
      { id: "advanced", title: "高级研究与协作", description: "检索、统计、恢复与同步", icon: Activity },
    ],
  },
  {
    id: "delivery",
    label: "检查与交付",
    description: "先处理修改与检查，再完成排版和文件交付",
    note: "核对记录与导出版本分别保留",
    icon: ClipboardCheck,
    pages: [
      { id: "review", title: "修改与检查", description: "导师意见、规则与正文核对", icon: ClipboardCheck },
      { id: "exports", title: "排版与交付", description: "排版方案、导出与交付记录", icon: Download },
    ],
  },
];

export const workspaceForPage = (page: string) =>
  projectWorkspaces.find((group) => group.pages.some((item) => item.id === page));

export const projectPageLabel = (page: string) =>
  workspaceForPage(page)?.pages.find((item) => item.id === page)?.title || "";

export function projectBreadcrumb(page: string) {
  if (page === "overview") return "项目概览";
  if (page === "settings") return "接口与设置";
  const group = workspaceForPage(page);
  return group ? `${group.label} · ${projectPageLabel(page)}` : "";
}

function subpageKey(pid: string, group: ProjectWorkspace) {
  return group.id === "sources"
    ? `paper-source-subpage:${pid}`
    : `paper-workspace-subpage:${pid}:${group.id}`;
}

export function readWorkspacePage(pid: string, group: ProjectWorkspace) {
  try {
    const saved = sessionStorage.getItem(subpageKey(pid, group));
    return group.pages.find((item) => item.id === saved)?.id || group.pages[0].id;
  } catch {
    return group.pages[0].id;
  }
}

export function rememberWorkspacePage(pid: string, page: string) {
  const group = workspaceForPage(page);
  if (!pid || !group) return;
  try {
    sessionStorage.setItem(subpageKey(pid, group), page);
  } catch {}
}

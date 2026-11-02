import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { BookOpen, X } from "lucide-react";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import type { Row } from "../workspace/types";
import { metadataOf } from "./ReferenceDialogs";

const Titles = createContext<Record<string, string>>({});
const toolCopy: Record<string, [string, string]> = {
  自动路由与论证计划: [
    "查询与阅读计划",
    "提出研究问题，预览检索方式，再生成可回查原文的阅读计划。",
  ],
  完整增量维护队列: [
    "更新待处理内容",
    "查看资料变化后需要重新核对或更新的内容，按检查点继续处理。",
  ],
  Wiki并发编辑合并: [
    "合并页面修订",
    "比较同一页面的不同修改，逐项选择要保留的内容。",
  ],
  论断支持性裁决: [
    "核对有分歧的论断",
    "查看不同核对记录，回到原文判断论断的支持程度，并保留处理依据。",
  ],
  完整对照评测与统计: [
    "评测与质量记录",
    "查看对照问题、检索表现和人工评审记录，区分自动结果与实际质量。",
  ],
  真实论文效果验收: [
    "检查论文中的实际效果",
    "对照论文和原件，记录知识库内容在当前论文中的实际使用情况。",
  ],
  完整知识包与跨项目导入: [
    "备份与导入知识包",
    "导出完整知识包，或先预览导入内容，再选择副本或新项目的恢复方式。",
  ],
  完整知识云同步: [
    "云端同步",
    "使用自己的云端地址和独立加密口令同步知识包，先检查同步范围与版本。",
  ],
};

export function ReferenceTitles({
  references,
  files,
  children,
}: {
  references: Row[];
  files: Row[];
  children: ReactNode;
}) {
  const titles = useMemo(() => {
    const result: Record<string, string> = {};
    for (const reference of references) {
      const title = metadataOf(reference).title;
      if (!title || !reference.file_id) continue;
      result[reference.file_id] = title;
      const file = files.find((item) => item.id === reference.file_id);
      if (file) result[file.name] = title;
    }
    return result;
  }, [references, files]);
  return <Titles.Provider value={titles}>{children}</Titles.Provider>;
}

export function useReferenceTitle() {
  const titles = useContext(Titles);
  return (file: Row | string) => {
    const fallback =
      typeof file === "string" ? file : file.title || file.name || "未命名文献";
    const id =
      typeof file === "string" ? file : file.file_id || file.id || fallback;
    return (
      titles[id] ||
      titles[fallback] ||
      String(fallback)
        .replace(/\.(pdf|txt|docx?|pptx?)$/i, "")
        .replace(/_/g, " ")
    );
  };
}

export function ResearchColumns({
  directory,
  children,
  inspector,
  className = "",
}: {
  directory: ReactNode;
  children: ReactNode;
  inspector?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`research-columns ${inspector ? "has-inspector" : ""} ${className}`}
    >
      <aside className="research-directory">{directory}</aside>
      <div className="research-content">{children}</div>
      {inspector && <aside className="research-inspector">{inspector}</aside>}
    </div>
  );
}

export function ResearchEmpty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="research-empty">
      <BookOpen size={30} strokeWidth={1.4} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

export function ReferenceSurface({
  embedded = false,
  title,
  onClose,
  busy,
  children,
  className = "",
}: {
  embedded?: boolean;
  title: string;
  onClose: () => void;
  busy?: boolean;
  children: ReactNode;
  className?: string;
}) {
  if (!embedded)
    return (
      <WorkspaceDialog title={title} onClose={onClose} busy={busy} wide>
        {children}
      </WorkspaceDialog>
    );
  return (
    <section
      className={`reference-surface ${className}`}
      aria-label={title}
      aria-busy={busy}
    >
      <header className="reference-surface-heading">
        <h2>{title}</h2>
        <button
          type="button"
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭当前详情"
        >
          <X size={17} />
        </button>
      </header>
      {children}
    </section>
  );
}

export function ReferenceInspector({
  title = "当前条目",
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <section className="reference-inspector-card">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

export function ResearchTools({
  children,
  inspector,
  result,
}: {
  children: ReactNode;
  inspector: ReactNode;
  result?: ReactNode;
}) {
  const [active, setActive] = useState(0);
  const sections = Children.toArray(children).filter(
    isValidElement<{ children: ReactNode }>,
  );
  return (
    <ResearchColumns
      className="research-tools"
      directory={
        <>
          <div className="research-directory-heading">
            <h3>维护工具</h3>
            <span>{sections.length} 项</span>
          </div>
          {sections.map((section, index) => {
            const content = Children.toArray(section.props.children);
            const summary = content.find(
              (item) => isValidElement(item) && item.type === "summary",
            );
            const title = isValidElement<{ children: ReactNode }>(summary)
              ? String(summary.props.children)
              : "操作工具";
            return (
              <button
                className="research-directory-item"
                key={index}
                aria-pressed={active === index}
                onClick={() => setActive(index)}
              >
                <small>工具 {String(index + 1).padStart(2, "0")}</small>
                <strong>{toolCopy[title]?.[0] || title}</strong>
              </button>
            );
          })}
        </>
      }
      inspector={inspector}
    >
      {sections.map((section, index) => {
        const content = Children.toArray(section.props.children);
        const summary = content.find(
          (item) => isValidElement(item) && item.type === "summary",
        );
        const title = isValidElement<{ children: ReactNode }>(summary)
          ? String(summary.props.children)
          : "操作工具";
        return (
          <section
            key={index}
            className="research-tool-section"
            hidden={active !== index}
          >
            <header className="reference-document-heading">
              <span className="research-kicker">处理与维护</span>
              <h3>{toolCopy[title]?.[0] || title}</h3>
              {toolCopy[title] && <p>{toolCopy[title][1]}</p>}
            </header>
            {content.filter(
              (item) => !(isValidElement(item) && item.type === "summary"),
            )}
          </section>
        );
      })}
      {result}
    </ResearchColumns>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  FileText,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";
import { send } from "../client";
import { summaryStateLabels } from "../MaterialSummary";
import { materialRoles, type Row } from "../workspace/types";
import { WorkspaceEmpty } from "../workspace/WorkspaceChrome";
import MaterialSearchResults from "./MaterialSearchResults";

type Props = {
  pid: string;
  files: Row[];
  busy: string;
  onOpen: (id: string, page?: number) => void;
  onPatch: (file: Row, field: string, value: string) => Promise<any>;
  onUpload: () => void;
};
export default function MaterialLibrary({
  pid,
  files,
  busy,
  onOpen,
  onPatch,
  onUpload,
}: Props) {
  const [query, setQuery] = useState(""),
    [role, setRole] = useState("all"),
    [state, setState] = useState("all"),
    [includeInactive, setIncludeInactive] = useState(false);
  const [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(10),
    [sort, setSort] = useState("recent");
  const [matches, setMatches] = useState<Row[] | null>(null),
    [searched, setSearched] = useState(""),
    [searching, setSearching] = useState(false),
    [error, setError] = useState("");
  const searchVersion = useRef(0);
  useEffect(
    () => () => {
      searchVersion.current++;
    },
    [],
  );
  const searchOriginal = async () => {
    if (!query.trim() || searching) return;
    const version = ++searchVersion.current,
      term = query.trim();
    setSearching(true);
    setError("");
    try {
      const result = await send(`/projects/${pid}/search`, {
        query: term,
        kind: "all",
      });
      if (version === searchVersion.current) {
        setMatches(result);
        setSearched(term);
      }
    } catch (failure) {
      if (version === searchVersion.current)
        setError((failure as Error).message);
    } finally {
      if (version === searchVersion.current) setSearching(false);
    }
  };
  const current = useMemo(
    () =>
      files
        .filter(
          (file) =>
            (includeInactive || file.active) &&
            (role === "all" || file.role === role) &&
            (state === "all" ||
              (state === "summarized"
                ? file.summary_state === "completed"
                : state === "attention"
                  ? ["failed", "partial"].includes(file.parse_quality?.state) ||
                    file.summary_state === "failed"
                  : file.summary_state !== "completed")) &&
            `${file.name} ${materialRoles[file.role] ?? ""}`
              .toLowerCase()
              .includes(query.trim().toLowerCase()),
        )
        .sort((a, b) =>
          sort === "name"
            ? a.name.localeCompare(b.name, "zh-CN")
            : sort === "pages"
              ? (b.pages ?? 0) - (a.pages ?? 0)
              : b.created_at.localeCompare(a.created_at),
        ),
    [files, includeInactive, role, state, query, sort],
  );
  const pages = Math.max(1, Math.ceil(current.length / pageSize)),
    currentPage = Math.min(page, pages);
  useEffect(() => {
    setPage(1);
  }, [query, role, state, includeInactive, sort, pageSize]);
  const clear = () => {
    setQuery("");
    setRole("all");
    setState("all");
    setIncludeInactive(false);
    setMatches(null);
    setError("");
    searchVersion.current++;
    setSearching(false);
  };
  return (
    <section
      className="panel material-library no-padding"
      aria-label="项目资料库"
    >
      <header className="material-library-heading">
        <div>
          <h2>
            项目资料 <span>{files.filter((file) => file.active).length}</span>
          </h2>
          <p>原件、AI 总结与用途集中管理，每份资料都能回到原文。</p>
        </div>
        <button className="text-button" onClick={onUpload}>
          添加资料
          <ArrowUpRight size={14} />
        </button>
      </header>
      <div className="material-library-toolbar">
        <label className="workspace-inline-search">
          <Search size={16} />
          <input
            aria-label="搜索资料"
            placeholder="搜索文件名称，或输入关键词检索原文"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              if (matches) setMatches(null);
              searchVersion.current++;
              setSearching(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void searchOriginal();
              }
            }}
          />
          {query && (
            <button aria-label="清除资料搜索" onClick={clear}>
              <X size={14} />
            </button>
          )}
        </label>
        <button
          className="btn"
          disabled={!query.trim() || searching || !files.length}
          onClick={searchOriginal}
        >
          {searching ? (
            <LoaderCircle className="spin" size={14} />
          ) : (
            <BookOpen size={14} />
          )}
          检索原文
        </button>
        <select
          aria-label="按资料状态筛选"
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          <option value="all">全部处理状态</option>
          <option value="summarized">已有 AI 总结</option>
          <option value="pending">尚待 AI 总结</option>
          <option value="attention">解析或总结异常</option>
        </select>
        <select
          aria-label="资料排序"
          value={sort}
          onChange={(event) => setSort(event.target.value)}
        >
          <option value="recent">最近导入</option>
          <option value="name">按名称排序</option>
          <option value="pages">按页数排序</option>
        </select>
      </div>
      <div className="material-role-filters" aria-label="按资料用途筛选">
        {[["all", "全部用途"], ...Object.entries(materialRoles)].map(
          ([key, label]) => (
            <button
              key={key}
              aria-pressed={role === key}
              onClick={() => setRole(key)}
            >
              {label}
              <small>
                {
                  files.filter(
                    (file) =>
                      (includeInactive || file.active) &&
                      (key === "all" || file.role === key),
                  ).length
                }
              </small>
            </button>
          ),
        )}
      </div>
      <div className="material-library-filter-note">
        <label>
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(event) => setIncludeInactive(event.target.checked)}
          />
          包含已停用资料
        </label>
        <span>相同内容自动去重 · 原件保留</span>
        {(query || role !== "all" || state !== "all" || includeInactive) && (
          <button className="text-button" onClick={clear}>
            清除筛选
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="error-text material-library-error">
          {error}
        </p>
      )}
      {matches ? (
        <MaterialSearchResults
          results={matches}
          query={searched}
          onClear={() => setMatches(null)}
          onOpen={onOpen}
        />
      ) : (
        <>
          <div className="material-list" aria-label="当前页资料">
            {current
              .slice((currentPage - 1) * pageSize, currentPage * pageSize)
              .map((file) => (
                <article
                  className={`material-row ${!file.active ? "is-inactive" : ""}`}
                  key={file.id}
                  data-file-id={file.id}
                >
                  <div className="material-row-main">
                    <button
                      className="file-name"
                      aria-label={file.name}
                      onClick={() => onOpen(file.id)}
                    >
                      <span
                        className={`file-icon ${file.extension === ".pptx" ? "orange" : ""}`}
                      >
                        <FileText size={19} />
                      </span>
                      <span>
                        <strong>{file.name}</strong>
                        <small>
                          {file.extension?.replace(".", "").toUpperCase()} ·{" "}
                          {(file.size / 1024 / 1024).toFixed(1)} MB ·{" "}
                          {file.pages || 0} 页{!file.active && " · 已停用"}
                        </small>
                      </span>
                    </button>
                    <button
                      className="material-summary-entry"
                      onClick={() => onOpen(file.id)}
                    >
                      概括与分点
                      <ArrowUpRight size={12} />
                    </button>
                    {file.error && <p className="error-text">{file.error}</p>}
                  </div>
                  <div className="material-row-purpose">
                    <label>
                      <span>资料用途</span>
                      <select
                        aria-label={`${file.name}的用途`}
                        value={file.role}
                        disabled={!!busy}
                        onChange={(event) =>
                          onPatch(file, "role", event.target.value)
                        }
                      >
                        {Object.entries(materialRoles).map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="material-row-status">
                    <span
                      className={`summary-file-state summary-state-${file.summary_state}`}
                    >
                      {summaryStateLabels[file.summary_state] ?? "尚未总结"}
                    </span>
                    <small
                      className={
                        ["failed", "partial"].includes(
                          file.parse_quality?.state,
                        )
                          ? "error-text"
                          : ""
                      }
                    >
                      {file.parse_quality?.label ?? file.status}
                      {file.parse_quality?.ocr_failed_pages
                        ? ` · ${file.parse_quality.ocr_failed_pages} 页缺项`
                        : ""}
                    </small>
                  </div>
                  <div className="material-row-access">
                    <label>
                      <span>模型使用范围</span>
                      <select
                        aria-label={`${file.name}的模型使用范围`}
                        value={file.egress}
                        disabled={!!busy}
                        onChange={(event) =>
                          onPatch(file, "egress", event.target.value)
                        }
                      >
                        <option value="selected_content">选定片段</option>
                        <option value="local_only">仅限本地</option>
                        <option value="allowed">按任务使用</option>
                      </select>
                    </label>
                  </div>
                  <button
                    className="btn material-row-open"
                    aria-label={`打开资料：${file.name}`}
                    onClick={() => onOpen(file.id)}
                  >
                    打开资料
                    <ArrowUpRight size={13} />
                  </button>
                </article>
              ))}
          </div>
          {!current.length && (
            <WorkspaceEmpty
              title={files.length ? "没有匹配的资料" : "先把资料放进来"}
              description={
                files.length
                  ? "调整用途或处理状态，也可以在原文中检索关键词。"
                  : "支持 PPTX、PDF、DOCX、TXT、Markdown、CSV 和 XLSX。"
              }
            >
              <button className="btn" onClick={files.length ? clear : onUpload}>
                {files.length ? "清除筛选" : "导入第一份资料"}
              </button>
            </WorkspaceEmpty>
          )}
          <footer className="workspace-pagination">
            <span>
              共 {current.length} 份 · 第 {currentPage}/{pages} 页
            </span>
            <div className="actions">
              <select
                aria-label="每页资料条数"
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {[10, 20, 50].map((value) => (
                  <option key={value} value={value}>
                    {value} 份 / 页
                  </option>
                ))}
              </select>
              <button
                className="btn"
                aria-label="上一页资料"
                disabled={currentPage === 1}
                onClick={() => setPage(currentPage - 1)}
              >
                <ChevronLeft size={15} />
              </button>
              <button
                className="btn"
                aria-label="下一页资料"
                disabled={currentPage === pages}
                onClick={() => setPage(currentPage + 1)}
              >
                <ChevronRight size={15} />
              </button>
            </div>
          </footer>
        </>
      )}
    </section>
  );
}

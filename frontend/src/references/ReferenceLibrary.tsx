import { useEffect, useMemo, useState } from "react";
import { BookOpen, FileText, Plus, Search, Upload } from "lucide-react";
import type { Row } from "../workspace/types";
import { MaterialSummary } from "../MaterialSummary";
import { metadataOf } from "./ReferenceDialogs";
import {
  ReferenceInspector,
  ResearchColumns,
  ResearchEmpty,
} from "./ReferenceWorkbenchParts";

const authors = (value: unknown) =>
  Array.isArray(value) ? value.join("，") : String(value || "作者待补");
const language = (value: string) =>
  ({ zh: "中文", en: "英文", other: "其他外文" })[value] || "语种待确认";
const verification = (value: string) =>
  ({ human_confirmed: "已人工核对", metadata_matched: "元数据匹配" })[value] ||
  "待核对";

export default function ReferenceLibrary({
  references,
  files,
  tick,
  fileId,
  loading,
  filesLoading = false,
  onEdit,
  onImport,
  onSource,
  onReading,
  onChunks,
  onPermissions,
}: {
  references: Row[];
  files: Row[];
  tick: number;
  fileId?: string;
  loading: boolean;
  filesLoading?: boolean;
  onEdit: (reference: Row) => void;
  onImport: (kind: string) => void;
  onSource: (file: Row, page?: number) => void;
  onReading: (file: Row) => void;
  onChunks: (file: Row) => void;
  onPermissions: (file: Row) => void;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [locale, setLocale] = useState("all"),
    [state, setState] = useState("all"),
    [selected, setSelected] = useState(""),
    [content, setContent] = useState<"details" | "summary">("details");
  const records = useMemo<Array<Row & { meta: Row }>>(
    () => references.map((item) => ({ ...item, meta: metadataOf(item) })),
    [references],
  );
  const categories = [
    ...new Set(records.map((item) => item.meta.category).filter(Boolean)),
  ].sort();
  const filtered = records.filter(
    (item) =>
      (!fileId || item.file_id === fileId) &&
      (category === "all" || item.meta.category === category) &&
      (locale === "all" || item.meta.language === locale) &&
      (state === "all" ||
        (state === "unverified"
          ? !["human_confirmed", "metadata_matched"].includes(item.verification)
          : item.verification === state)) &&
      (!query.trim() ||
        [
          item.meta.title,
          authors(item.meta.authors),
          item.meta.doi,
          item.meta.journal,
          item.meta.publisher,
        ]
          .join(" ")
          .toLowerCase()
          .includes(query.trim().toLowerCase())),
  );
  const active = filtered.find((item) => item.id === selected) || filtered[0];
  const original = files.find((item) => item.id === active?.file_id);
  const canRead =
    original?.status === "ready" &&
    (original.role === "research" || original.research_eligible);
  useEffect(() => setContent("details"), [active?.id]);
  return (
    <section className="reference-library-workbench" aria-label="参考文献库">
      <ResearchColumns
        directory={
          <>
            <div className="research-directory-heading">
              <h2>文献目录</h2>
              <span>
                {filtered.length} / {records.length}
              </span>
            </div>
            <label className="research-search">
              <Search size={16} />
              <input
                aria-label="搜索文献"
                placeholder="题名、作者或 DOI"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <details className="research-filters">
              <summary>
                筛选文献
                {category !== "all" || state !== "all" || locale !== "all"
                  ? " · 已筛选"
                  : ""}
              </summary>
              <label>
                分类
                <select
                  aria-label="按文献分类筛选"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  <option value="all">全部分类</option>
                  {categories.map((value) => (
                    <option key={value} value={value}>
                      {value.replace(/^\d+-/, "")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                语言
                <select
                  aria-label="按文献语种筛选"
                  value={locale}
                  onChange={(e) => setLocale(e.target.value)}
                >
                  <option value="all">全部语种</option>
                  <option value="zh">中文</option>
                  <option value="en">英文</option>
                  <option value="other">其他外文</option>
                </select>
              </label>
              <label>
                核对状态
                <select
                  aria-label="按书目核查状态筛选"
                  value={state}
                  onChange={(e) => setState(e.target.value)}
                >
                  <option value="all">全部状态</option>
                  <option value="unverified">待核对</option>
                  <option value="human_confirmed">已人工核对</option>
                  <option value="metadata_matched">元数据匹配</option>
                </select>
              </label>
            </details>
            <div className="reference-directory-list">
              {loading && !records.length && (
                <p role="status">正在读取文献目录…</p>
              )}
              {filtered.map((item, index) => (
                <article
                  className={`reference-row ${active?.id === item.id ? "is-selected" : ""}`}
                  key={item.id}
                  data-reference-id={item.id}
                >
                  <button
                    type="button"
                    className="research-directory-item"
                    aria-pressed={active?.id === item.id}
                    onClick={() => setSelected(item.id)}
                  >
                    <small>
                      {String(index + 1).padStart(2, "0")}{" "}
                      <span>
                        {item.meta.year || "年份待补"} ·{" "}
                        {language(item.meta.language)}
                      </span>
                    </small>
                    <strong>{item.meta.title || "题名待补"}</strong>
                    <span>{authors(item.meta.authors)}</span>
                  </button>
                </article>
              ))}
              {!filtered.length && !loading && (
                <p className="research-directory-empty">
                  没有匹配的文献，试试其他关键词。
                </p>
              )}
            </div>
            <div className="research-directory-footer">
              <button className="btn" onClick={() => onEdit({})}>
                <Plus size={15} />
                登记文献
              </button>
              <button className="btn" onClick={() => onImport("archive")}>
                <Upload size={15} />
                导入文献
              </button>
            </div>
          </>
        }
        inspector={
          active ? (
            <>
              <ReferenceInspector title="阅读与核对">
                <span
                  className={`badge ${active.verification === "human_confirmed" ? "green" : ""}`}
                >
                  {verification(active.verification)}
                </span>
                <p>先阅读原文，再核对书目信息和引用内容。</p>
                <div className="research-inspector-actions">
                  <button
                    className="btn primary"
                    disabled={!canRead}
                    onClick={() => original && onReading(original)}
                  >
                    <BookOpen size={16} />
                    按问题阅读
                  </button>
                  <button
                    className="btn"
                    disabled={!original}
                    onClick={() => original && onSource(original)}
                  >
                    <FileText size={16} />
                    查看原文
                  </button>
                  <button className="btn" onClick={() => onEdit(active)}>
                    核对书目
                  </button>
                  <button
                    className="btn"
                    disabled={!original}
                    onClick={() => original && onChunks(original)}
                  >
                    知识片段
                  </button>
                </div>
              </ReferenceInspector>
              <ReferenceInspector title="原件与来源">
                <p>
                  {original
                    ? original.status === "ready"
                      ? "已关联原件 · 文字已提取"
                      : "已关联原件 · 解析待完成"
                    : filesLoading && active.file_id
                      ? "正在读取关联原件…"
                      : "尚未关联原件"}
                </p>
                {original && (
                  <>
                    <details>
                      <summary>查看文件信息</summary>
                      <p className="reference-filename">{original.name}</p>
                    </details>
                    <button
                      className="text-button"
                      onClick={() => onPermissions(original)}
                    >
                      资料用途与权限
                    </button>
                  </>
                )}
              </ReferenceInspector>
              <ReferenceInspector title="其他导入方式">
                <div className="research-inspector-actions">
                  <button className="btn" onClick={() => onImport("bib")}>
                    导入 BibTeX / RIS
                  </button>
                  <button className="btn" onClick={() => onImport("lookup")}>
                    查询 DOI / 文献元数据
                  </button>
                </div>
              </ReferenceInspector>
            </>
          ) : (
            <ReferenceInspector title="建立文献库">
              <p>可以手动登记书目，或导入包含原件的文献包。</p>
              <div className="research-inspector-actions">
                <button
                  className="btn primary"
                  onClick={() => onImport("archive")}
                >
                  导入 ZIP 文献包
                </button>
                <button className="btn" onClick={() => onImport("bib")}>
                  导入 BibTeX / RIS
                </button>
                <button className="btn" onClick={() => onImport("lookup")}>
                  查询 DOI / 文献元数据
                </button>
              </div>
            </ReferenceInspector>
          )
        }
      >
        {active ? (
          <article className="reference-document" aria-label="当前文献详情">
            <header className="reference-document-heading">
              <span className="research-kicker">文献信息</span>
              <h2>{active.meta.title || "题名待补"}</h2>
              <p>{authors(active.meta.authors)}</p>
              <div className="reference-labels">
                <span>
                  {active.meta.category?.replace(/^\d+-/, "") || "未分类"}
                </span>
                <span>
                  {active.meta.scope === "full_text"
                    ? "全文原件"
                    : active.meta.scope === "abstract_only"
                      ? "仅摘要"
                      : "仅书目"}
                </span>
              </div>
            </header>
            <div
              className="research-view-switch"
              role="group"
              aria-label="文献详情内容"
            >
              <button
                className="btn"
                aria-pressed={content === "details"}
                onClick={() => setContent("details")}
              >
                书目信息
              </button>
              <button
                className="btn"
                aria-pressed={content === "summary"}
                disabled={!original}
                onClick={() => setContent("summary")}
              >
                文献总结
              </button>
            </div>
            {content === "summary" && original ? (
              <MaterialSummary
                key={original.id}
                file={original}
                tick={tick}
                onPage={(page) => onSource(original, page)}
              />
            ) : (
              <>
                <dl className="reference-metadata">
                  <div>
                    <dt>发表年份</dt>
                    <dd>{active.meta.year || "待补充"}</dd>
                  </div>
                  <div>
                    <dt>文献语言</dt>
                    <dd>{language(active.meta.language)}</dd>
                  </div>
                  <div className="wide">
                    <dt>期刊 / 出版单位</dt>
                    <dd>
                      {active.meta.journal || active.meta.publisher || "待补充"}
                    </dd>
                  </div>
                  <div className="wide">
                    <dt>DOI</dt>
                    <dd>{active.meta.doi || "尚未登记"}</dd>
                  </div>
                  {active.meta.pages && (
                    <div>
                      <dt>页码</dt>
                      <dd>{active.meta.pages}</dd>
                    </div>
                  )}
                  {active.meta.original_pages && (
                    <div>
                      <dt>原件页数</dt>
                      <dd>{active.meta.original_pages}</dd>
                    </div>
                  )}
                </dl>
                {active.meta.abstract && (
                  <section className="reference-reading-section">
                    <h3>摘要</h3>
                    <p>{active.meta.abstract}</p>
                  </section>
                )}
                <section className="reference-reading-section">
                  <h3>参考文献著录</h3>
                  <p className="reference-citation">
                    {active.meta.citation_raw ||
                      `${authors(active.meta.authors)}. ${active.meta.title || "题名待补"}. ${active.meta.journal || active.meta.publisher || "出版信息待补"}, ${active.meta.year || "年份待补"}.`}
                  </p>
                  <small>引用序号按正文中的实际引用顺序计算。</small>
                </section>
                {active.meta.import_warnings?.length > 0 && (
                  <details className="research-advanced">
                    <summary>导入时需要核对的信息</summary>
                    {active.meta.import_warnings.map((warning: string) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </details>
                )}
                <div className="research-next-step">
                  <BookOpen size={20} />
                  <div>
                    <strong>从阅读问题开始</strong>
                    <p>
                      梳理这篇文献的方法、适用条件与局限，保存原文位置，便于写作时回查。
                    </p>
                  </div>
                  <button
                    className="btn"
                    disabled={!canRead}
                    onClick={() => original && onReading(original)}
                  >
                    开始阅读
                  </button>
                </div>
              </>
            )}
          </article>
        ) : (
          <ResearchEmpty
            title={records.length ? "没有符合条件的文献" : "从第一篇文献开始"}
          >
            {records.length
              ? "调整左侧关键词或筛选条件。"
              : "导入文献包，或登记题名、作者和年份并关联原件。"}
          </ResearchEmpty>
        )}
      </ResearchColumns>
    </section>
  );
}

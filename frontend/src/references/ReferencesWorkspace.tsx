import { useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Download,
  FileText,
  Languages,
  LoaderCircle,
  Plus,
  Search,
  Upload,
  X,
} from "lucide-react";
import { send } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row, SourceRenderer, WorkspaceAction } from "../workspace/types";
import {
  ArchiveImportDialog,
  BibliographyImportDialog,
  ReferenceEditor,
  ReferenceLookupDialog,
  TermEditor,
  metadataOf,
} from "./ReferenceDialogs";
import "./referencesWorkspace.css";
import { KnowledgeBasePanel, KnowledgeDocumentDialog } from "./KnowledgeBase";
import EvidenceKnowledgePanel from "./EvidenceKnowledge";
import DocumentReadingPanel from "./DocumentReading";

const languageLabel = (language: string) =>
  ({ zh: "中文", en: "英文", other: "其他外文" })[language] ?? "语种待确认";
const verificationLabel = (value: string) =>
  ({ human_confirmed: "已人工核对", metadata_matched: "元数据匹配" })[value] ??
  "待核对";
const authorLabel = (value: unknown) =>
  Array.isArray(value) ? value.join("，") : String(value || "作者待补");

export default function ReferencesWorkspace({
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
  const references = useWorkspaceResource<Row[]>(
      `/projects/${pid}/references`,
      tick,
    ),
    files = useWorkspaceResource<Row[]>(`/projects/${pid}/files`, tick),
    terms = useWorkspaceResource<Row[]>(`/projects/${pid}/terms`, tick),
    knowledge = useWorkspaceResource<Row>(
      `/projects/${pid}/knowledge-base`,
      tick,
    ),
    archives = useWorkspaceResource<Row[]>(
      `/projects/${pid}/references/archives`,
      tick,
    );
  const [editing, setEditing] = useState<Row | null>(null),
    [knowledgeDocument, setKnowledgeDocument] = useState<Row | null>(null),
    [term, setTerm] = useState<Row | null>(null),
    [source, setSource] = useState<Row | null>(null),
    [dialog, setDialog] = useState(""),
    [query, setQuery] = useState(""),
    [language, setLanguage] = useState("all"),
    [category, setCategory] = useState("all"),
    [verification, setVerification] = useState("all"),
    [page, setPage] = useState(0),
    [pageSize, setPageSize] = useState(10),
    [notice, setNotice] = useState("");
  const records = useMemo<Array<Row & { meta: Row }>>(
    () =>
      (references.data ?? []).map((reference) => ({
        ...reference,
        meta: metadataOf(reference),
      })),
    [references.data],
  );
  const categories = [
    ...new Set(
      records.map((reference) => reference.meta.category).filter(Boolean),
    ),
  ].sort();
  const filtered = records.filter((reference) => {
    const meta = reference.meta;
    return (
      (!query.trim() ||
        [
          meta.title,
          authorLabel(meta.authors),
          meta.doi,
          meta.journal,
          meta.publisher,
          meta.citation_raw,
        ]
          .join(" ")
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase())) &&
      (language === "all" || meta.language === language) &&
      (category === "all" || meta.category === category) &&
      (verification === "all" ||
        (verification === "unverified"
          ? !["human_confirmed", "metadata_matched"].includes(
              reference.verification,
            )
          : reference.verification === verification))
    );
  });
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / pageSize) - 1),
  );
  const visible = filtered.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
  const latest = archives.data?.[0];
  const error =
    references.error || files.error || terms.error || archives.error;
  return (
    <section className="references-workspace" aria-label="文献与术语">
      <header className="page-title reference-heading">
        <div>
          <div className="eyebrow">PAPER WORKBENCH</div>
          <h1>
            文献有出处，<span>术语有统一。</span>
          </h1>
          <p>
            文献原文存入本地知识库，支持内容检索与页码追溯；书目和引用支持性分别核对。
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setTerm({})}>
            <Languages size={17} />
            新增术语
          </button>
          <button className="btn primary" onClick={() => setEditing({})}>
            <Plus size={17} />
            登记文献
          </button>
        </div>
      </header>
      <div className="actions reference-import-actions">
        <button className="btn primary" onClick={() => setDialog("archive")}>
          <Upload size={17} />
          导入 ZIP 文献包
        </button>
        <button className="btn" onClick={() => setDialog("bib")}>
          <Upload size={17} />
          导入 BibTeX / RIS
        </button>
        <button className="btn" onClick={() => setDialog("lookup")}>
          <Search size={17} />
          查询 DOI / 文献元数据
        </button>
      </div>
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
      {records.length > 0 && (
        <div className="reference-metrics" aria-label="文献统计">
          <span>
            已登记 <strong>{records.length}</strong> 篇
          </span>
          <span>
            中文{" "}
            <strong>
              {
                records.filter((reference) => reference.meta.language === "zh")
                  .length
              }
            </strong>
          </span>
          <span>
            外文{" "}
            <strong>
              {
                records.filter((reference) =>
                  ["en", "other"].includes(reference.meta.language),
                ).length
              }
            </strong>
          </span>
          <span>
            待核对{" "}
            <strong>
              {
                records.filter(
                  (reference) =>
                    !["human_confirmed", "metadata_matched"].includes(
                      reference.verification,
                    ),
                ).length
              }
            </strong>
          </span>
        </div>
      )}
      <DocumentReadingPanel
        pid={pid}
        tick={tick}
        onSource={(file, page) => setSource({ ...file, initialPage: page })}
        onChanged={() => {
          void knowledge.load();
        }}
      />
      <EvidenceKnowledgePanel
        pid={pid}
        tick={tick}
        onSource={(file, page) => setSource({ ...file, initialPage: page })}
        onImported={() => void act(async () => ({}), "研究文献已导入")}
      />
      <KnowledgeBasePanel
        pid={pid}
        knowledge={knowledge}
        files={files.data ?? []}
        act={act}
        onSource={(file, page) => setSource({ ...file, initialPage: page })}
      />
      <section
        className="panel no-padding reference-library"
        aria-label="参考文献库"
      >
        <div className="section-heading padded">
          <h2>
            参考文献 <span className="count">{records.length}</span>
          </h2>
          <span>引用序号在正文和导出时统一计算</span>
        </div>
        {records.length > 0 && (
          <div className="reference-filters">
            <label className="reference-search">
              <Search size={17} />
              <input
                aria-label="搜索文献"
                placeholder="搜索题名、作者、DOI"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(0);
                }}
              />
              {query && (
                <button
                  className="icon-button"
                  aria-label="清除文献搜索"
                  onClick={() => {
                    setQuery("");
                    setPage(0);
                  }}
                >
                  <X size={15} />
                </button>
              )}
            </label>
            <select
              aria-label="按文献分类筛选"
              value={category}
              onChange={(event) => {
                setCategory(event.target.value);
                setPage(0);
              }}
            >
              <option value="all">全部分类</option>
              {categories.map((value) => (
                <option key={value} value={value}>
                  {value.replace(/^\d+-/, "")}
                </option>
              ))}
            </select>
            <select
              aria-label="按文献语种筛选"
              value={language}
              onChange={(event) => {
                setLanguage(event.target.value);
                setPage(0);
              }}
            >
              <option value="all">全部语种</option>
              <option value="zh">中文</option>
              <option value="en">英文</option>
              <option value="other">其他外文</option>
            </select>
            <select
              aria-label="按书目核查状态筛选"
              value={verification}
              onChange={(event) => {
                setVerification(event.target.value);
                setPage(0);
              }}
            >
              <option value="all">全部核查状态</option>
              <option value="unverified">待核对</option>
              <option value="human_confirmed">已人工核对</option>
              <option value="metadata_matched">元数据匹配</option>
            </select>
          </div>
        )}
        {references.loading && !references.data ? (
          <div className="empty" role="status">
            <LoaderCircle size={27} className="spin" />
            <p>正在加载文献库…</p>
          </div>
        ) : visible.length ? (
          visible.map((reference, index) => {
            const meta = reference.meta,
              original = files.data?.find(
                (file) => file.id === reference.file_id,
              );
            return (
              <article
                className="reference-row"
                key={reference.id}
                data-reference-id={reference.id}
              >
                <span className="ref-index" aria-label="库内序号">
                  {String(currentPage * pageSize + index + 1).padStart(2, "0")}
                </span>
                <div className="grow">
                  <h3>{meta.title || "题名待补"}</h3>
                  <p>
                    {authorLabel(meta.authors)} · {meta.year || "年份待补"} ·{" "}
                    {meta.journal || meta.publisher || "出版信息待补"}
                  </p>
                  <div className="reference-labels">
                    <span>{languageLabel(meta.language)}</span>
                    {meta.category && (
                      <span>{meta.category.replace(/^\d+-/, "")}</span>
                    )}
                    <span>
                      {meta.scope === "full_text"
                        ? "全文原件"
                        : meta.scope === "abstract_only"
                          ? "仅摘要"
                          : "仅书目"}
                    </span>
                    {original && (
                      <span>
                        {original.status === "ready"
                          ? "文字已提取"
                          : original.status === "failed"
                            ? "解析待重试"
                            : "原文解析中"}
                      </span>
                    )}
                  </div>
                  <small>
                    {meta.doi ? "DOI: " + meta.doi : "DOI 待补充"}
                    {meta.original_pages
                      ? ` · 原件 ${meta.original_pages} 页`
                      : ""}
                  </small>
                  {meta.citation_raw && (
                    <details className="reference-original-citation">
                      <summary>完整著录与来源</summary>
                      <p>{meta.citation_raw}</p>
                      <small>{meta.archive_member}</small>
                      {meta.import_warnings?.map((message: string) => (
                        <p key={message}>{message}</p>
                      ))}
                    </details>
                  )}
                </div>
                <div className="reference-row-actions">
                  <span
                    className={`badge ${reference.verification === "human_confirmed" ? "green" : ""}`}
                  >
                    {verificationLabel(reference.verification)}
                  </span>
                  {reference.file_id && (
                    <button
                      className="btn"
                      disabled={!original}
                      onClick={() => original && setSource(original)}
                    >
                      <FileText size={15} />
                      查看原文
                    </button>
                  )}
                  <button className="btn" onClick={() => setEditing(reference)}>
                    核对书目
                  </button>
                  {original?.role === "research" && (
                    <button
                      className="btn"
                      onClick={() => setKnowledgeDocument(original)}
                    >
                      <BookOpen size={15} />
                      知识片段
                    </button>
                  )}
                </div>
              </article>
            );
          })
        ) : (
          <div className="empty">
            <BookOpen size={34} />
            <h3>
              {records.length ? "没有符合条件的文献" : "建立可核对的文献记录"}
            </h3>
            <p>
              {records.length
                ? "调整搜索词或筛选条件后重试。"
                : "导入文献包，或填写作者、题名和年份并关联原文。书目正确不代表正文论断已获支持。"}
            </p>
          </div>
        )}
        {filtered.length > 0 && (
          <div className="reference-pagination">
            <span>
              共 {filtered.length} 篇 · 第 {currentPage + 1} /{" "}
              {Math.max(1, Math.ceil(filtered.length / pageSize))} 页
            </span>
            <label>
              每页
              <select
                aria-label="每页文献条数"
                value={pageSize}
                onChange={(event) => {
                  setPageSize(Number(event.target.value));
                  setPage(0);
                }}
              >
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={50}>50</option>
              </select>
            </label>
            <button
              className="btn"
              aria-label="上一页文献"
              disabled={!currentPage}
              onClick={() => setPage(currentPage - 1)}
            >
              上一页
            </button>
            <button
              className="btn"
              aria-label="下一页文献"
              disabled={(currentPage + 1) * pageSize >= filtered.length}
              onClick={() => setPage(currentPage + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </section>
      {latest && (
        <details className="panel reference-archive-history">
          <summary>
            <strong>文献包导入记录</strong>
            <span>
              {latest.pdf_count} 篇 · {latest.pages} 页原件
            </span>
          </summary>
          <p>{latest.filename}</p>
          <p className="muted">{latest.warnings?.join(" ")}</p>
          <a
            className="btn"
            href={`/api/v1/projects/${pid}/references/archives/${latest.sha256}/download`}
          >
            <Download size={15} />
            下载原始文献包
          </a>
          <details>
            <summary>
              包内清单与说明（{latest.attachments?.length ?? 0} 份）
            </summary>
            <ul>
              {latest.attachments?.map((name: string) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </details>
        </details>
      )}
      <section className="panel reference-terms" aria-label="中英文术语表">
        <div className="section-heading">
          <h2>
            中英文术语表{" "}
            <span className="count">{terms.data?.length ?? 0}</span>
          </h2>
          <span>摘要、正文与翻译共用</span>
        </div>
        {terms.data?.length ? (
          <div className="reference-term-list">
            {terms.data.map((item) => (
              <article key={item.id}>
                <strong>{item.zh}</strong>
                <ArrowRight size={15} />
                <span>{item.en}</span>
                <div className="actions">
                  <button
                    className="btn"
                    aria-label={"编辑术语：" + item.zh}
                    onClick={() => setTerm(item)}
                  >
                    编辑
                  </button>
                  <button
                    className="btn"
                    aria-label={"删除术语：" + item.zh}
                    onClick={() => {
                      if (confirm(`删除术语“${item.zh}”？已有正文会保留。`))
                        void act(
                          () =>
                            send(
                              `/projects/${pid}/terms/${item.id}`,
                              {},
                              "DELETE",
                            ),
                          "术语已删除",
                        );
                    }}
                  >
                    删除
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">
            还没有登记术语。可添加已确认的中英文译法，供摘要翻译与写作共用。
          </p>
        )}
      </section>
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
  );
}

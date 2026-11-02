import { useCallback, useEffect, useRef, useState } from "react";
import { api, send } from "../client";
import type { Row } from "../workspace/types";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import "./graphWiki.css";
import "./referenceWorkbench.css";
import {
  ReferenceInspector,
  useReferenceTitle,
} from "./ReferenceWorkbenchParts";
import EntitySplitControl from "./EntitySplitControl";
import KnowledgeOperations from "./KnowledgeOperations";

const scopeLabel: Record<string, string> = {
  literature: "研究文献",
  project: "项目实现",
  guidance: "写作指导",
  all: "全部范围",
};
const stateLabel: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  succeeded: "已完成",
  incomplete: "部分完成",
  failed: "失败",
  budget_blocked: "预算不足",
  outcome_unknown: "远端结果待核对",
  cancelled: "已取消",
  candidate: "候选",
  published: "已发布",
  stale: "需复查",
  supported: "支持",
  partial: "部分支持",
  contradicted: "矛盾",
  insufficient: "依据不足",
  not_checked: "尚未核对",
};
const pageLabel: Record<string, string> = {
  document: "文献",
  concept: "概念",
  method: "方法",
  topic: "主题",
  comparison: "比较",
  disagreement: "分歧",
  project: "项目",
  guidance: "指导",
};
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function GraphWikiWorkspace({
  pid,
  tick,
  onSource,
  initialNamespace = "literature",
  namespaceOptions,
  initialTab = "wiki",
  visibleTabs,
  initialQuery = "",
  focusFileId,
}: {
  pid: string;
  tick: number;
  onSource: (fileId: string, page?: number) => void;
  initialNamespace?: string;
  namespaceOptions?: string[];
  initialTab?: string;
  visibleTabs?: string[];
  initialQuery?: string;
  focusFileId?: string;
}) {
  const titleOf = useReferenceTitle();
  const base = `/projects/${pid}/knowledge`;
  const [status, setStatus] = useState<Row>({}),
    [pages, setPages] = useState<Row[]>([]),
    [sections, setSections] = useState<Row[]>([]);
  const [tab, setTab] = useState(initialTab),
    [scope, setScope] = useState(initialNamespace),
    [query, setQuery] = useState(initialQuery),
    [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("literal"),
    [budget, setBudget] = useState(2),
    [preview, setPreview] = useState<Row | null>(null);
  const [page, setPage] = useState<Row | null>(null),
    [blocks, setBlocks] = useState<Row[]>([]),
    [draft, setDraft] = useState<Row | null>(null),
    [editing, setEditing] = useState(false);
  const [graph, setGraph] = useState<Row | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [answer, setAnswer] = useState<Row | null>(null),
    [section, setSection] = useState("");
  const [source, setSource] = useState<Row | null>(null),
    [trace, setTrace] = useState<Row | null>(null),
    [diff, setDiff] = useState<string[]>([]);
  const [checks, setChecks] = useState<Row | null>(null),
    [cases, setCases] = useState<Row[]>([]),
    [comparison, setComparison] = useState<Row | null>(null),
    [caseEdit, setCaseEdit] = useState<Row | null>(null);
  const [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState(""),
    [quote, setQuote] = useState(""),
    [support, setSupport] = useState("not_checked"),
    [compared, setCompared] = useState(false);
  const [title, setTitle] = useState(""),
    [kind, setKind] = useState("topic"),
    [pluginId, setPluginId] = useState(""),
    [pluginMode, setPluginMode] = useState("global"),
    [pluginVectors, setPluginVectors] = useState(false);
  const [pluginReports, setPluginReports] = useState(false);
  const paidBlocked = status.billing_gate?.paid_requests_allowed !== true;
  const [includeOriginals, setIncludeOriginals] = useState(true);
  const [fullIndex, setFullIndex] = useState(false);
  const [retryAcknowledged, setRetryAcknowledged] = useState(false);
  const [sourceOptions, setSourceOptions] = useState<Row[]>([]);
  const inspection =
    source?.observation ??
    (source?.evidence
      ? {
          id: source.evidence.id,
          text: source.evidence.quote_text,
          metadata: {},
          locator: source.evidence.payload?.locator,
          file_id: source.evidence.payload?.file_id,
          evidence: true,
          kind: source.evidence.kind,
        }
      : null);
  const [replies, setReplies] = useState<Row[]>([]),
    [generateCompare, setGenerateCompare] = useState(false),
    [semanticReview, setSemanticReview] = useState<Row | null>(null),
    [mergeTarget, setMergeTarget] = useState("");
  const wikiQuery = useRef(initialQuery);
  const loadVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    const results = await Promise.allSettled([
      api(base + "/status"),
      api(
        base +
          `/wiki?namespace=${scope}` +
          `&query=${encodeURIComponent(wikiQuery.current)}` +
          (focusFileId ? `&file_id=${encodeURIComponent(focusFileId)}` : ""),
      ),
      api(`/projects/${pid}/sections`),
    ]);
    if (version !== loadVersion.current) return;
    if (results[0].status === "fulfilled") setStatus(results[0].value);
    else setError(message(results[0].reason));
    if (results[1].status === "fulfilled")
      setPages(results[1].value.items ?? []);
    if (results[2].status === "fulfilled") setSections(results[2].value ?? []);
  }, [base, pid, scope, focusFileId]);
  useEffect(() => {
    void load();
    return () => {
      loadVersion.current++;
    };
  }, [load, tick]);
  useEffect(() => {
    setGraph(null);
    setSelected([]);
    setAnswer(null);
    setPage(null);
    setBlocks([]);
    setDraft(null);
    setEditing(false);
    setSource(null);
    setTrace(null);
    setDiff([]);
    setPreview(null);
  }, [pid, scope, focusFileId]);
  const running = (status.tasks ?? []).some((t: Row) =>
    ["queued", "running"].includes(t.status),
  );
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [running, load]);
  const perform = async (
    fn: () => Promise<any>,
    success = "已保存",
    refresh = true,
  ) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await fn();
      setNotice(success);
      if (refresh) await load();
      return value;
    } catch (e) {
      setError(message(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  const openPage = async (id: string, version?: string) => {
    const p = await perform(
      () =>
        api(
          base +
            "/wiki/" +
            id +
            (version ? "?version_id=" + encodeURIComponent(version) : ""),
        ),
      "",
    );
    if (p) {
      const visible =
        !p.version && p.versions.length
          ? await api(base + "/wiki/" + id + "?version_id=" + p.versions[0].id)
          : p;
      setPage(visible);
      setBlocks(visible.blocks.map((b: Row) => ({ ...b, key: b.block_key })));
      setDraft(visible.version?.state === "candidate" ? visible : null);
      setEditing(false);
      setDiff([]);
    }
  };
  const showSource = async (ref: Row) => {
    const value = await perform(async () => {
      if (ref.type === "assertion") {
        const claim = await api(base + "/assertions/" + ref.id);
        const origin = claim.sources.find((r: Row) => r.type === "observation");
        const record = claim.sources.find((r: Row) => r.type === "evidence");
        return {
          claim,
          observation: origin
            ? await api(base + "/observations/" + origin.id)
            : null,
          evidence:
            !origin && record
              ? await api(base + "/evidence/" + record.id)
              : null,
        };
      }
      if (ref.type === "observation")
        return { observation: await api(base + "/observations/" + ref.id) };
      if (ref.type === "evidence")
        return { evidence: await api(base + "/evidence/" + ref.id) };
      if (ref.type === "wiki_version") {
        const p = pages.find((p) => p.current_version_id === ref.id);
        if (p) {
          await openPage(p.id, ref.id);
          return null;
        }
      }
      return {
        reference: ref,
        lineage: await api(
          base +
            `/lineage?kind=${encodeURIComponent(ref.type)}&identity=${encodeURIComponent(ref.id)}`,
        ),
      };
    }, "已读取来源");
    if (value) {
      setSource(value);
      setQuote(value.observation?.text ?? value.evidence?.quote_text ?? "");
      setNote("");
      setSupport("not_checked");
      setCompared(false);
    }
  };
  const selectedRefs = () =>
    (graph?.assertions ?? [])
      .filter((a: Row) => selected.includes(a.claim_version_id))
      .map((a: Row) => ({
        type: "assertion",
        id: a.claim_version_id,
        version: a.version_hash ?? a.content_hash,
      }));
  const openEvaluation = async () => {
    setTab("evaluation");
    const value = await perform(
      () => api(base + "/evaluation/cases"),
      "已读取评测任务",
    );
    if (value) setCases(value);
  };

  return (
    <section
      className={`panel knowledge-workspace reference-knowledge-shell ${visibleTabs?.includes("operations") ? "knowledge-management" : ""}`}
      aria-label="图谱与Wiki知识库"
    >
      <header className="section-heading">
        <div>
          <h2>
            {visibleTabs?.includes("operations")
              ? "知识库处理与维护"
              : "图谱与 Wiki"}
          </h2>
          <p>
            {visibleTabs?.includes("operations")
              ? "按任务选择工具，先预览范围，再执行处理。"
              : "按主题阅读，在内容旁查看来源与版本。"}
          </p>
        </div>
        <label className="knowledge-toggle">
          <input
            type="checkbox"
            checked={!!status.enabled}
            disabled={busy || !status.counts}
            onChange={(e) =>
              void perform(
                () =>
                  send(
                    base + "/settings",
                    { enabled: e.target.checked },
                    "PUT",
                  ),
                "写作知识开关已更新",
              )
            }
          />
          用于章节写作
        </label>
      </header>
      <details className="knowledge-system-status">
        <summary>
          <span>知识库状态</span>
          <span>
            {status.counts?.wiki_pages ?? "—"} 个知识页面
            {paidBlocked ? " · 本地功能可用" : " · 已就绪"}
          </span>
        </summary>
        <div className="knowledge-metrics">
          <span>
            <strong>{status.counts?.knowledge_observations ?? "—"}</strong>
            原文读取记录
          </span>
          <span>
            <strong>{status.counts?.kg_entities ?? "—"}</strong>实体
          </span>
          <span>
            <strong>{status.counts?.kg_assertions ?? "—"}</strong>候选论断
          </span>
          <span>
            <strong>{status.counts?.wiki_pages ?? "—"}</strong>知识页面
          </span>
        </div>
        {!!status.semantic_compilation?.verified_at && (
          <p className="knowledge-notice" role="status">
            {status.semantic_compilation.complete
              ? "全量语义编译已完成"
              : "编译后的来源或页面已变化，需复查"}
            ：{status.semantic_compilation.file_count} 篇，模型已处理
            {status.semantic_compilation.model_processed_ranges}/
            {status.semantic_compilation.expected_ranges} 个原文范围。
            语义支持性与人工质量请另看评测结果。
          </p>
        )}
        {status.budget && (
          <p className="knowledge-notice">
            项目可用预算 {Number(status.budget.available).toFixed(3)}{" "}
            {status.budget.currency}；账本包含待核实费用预留，预留不等于已扣费。
          </p>
        )}
        {paidBlocked && status.billing_gate && (
          <p className="knowledge-notice" role="status">
            收费功能暂不可用：{status.billing_gate.reasons.join("；")}
            。本地检索、原文阅读、Wiki编辑和不生成报告的社区计算仍可使用。
          </p>
        )}
      </details>
      {error && (
        <p className="knowledge-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="knowledge-notice">
          {notice}
        </p>
      )}
      <nav className="knowledge-tabs" aria-label="知识库功能">
        {[
          ["wiki", "主题 Wiki"],
          ["graph", "关系探索"],
          ["query", "章节准备"],
          ["build", "构建与更新"],
          ["maintenance", "检查与版本"],
          ["evaluation", "对照评测"],
          ["operations", "维护与迁移"],
        ]
          .filter(([id]) => !visibleTabs || visibleTabs.includes(id))
          .map(([id, label]) => (
            <button
              key={id}
              className={"btn " + (tab === id ? "primary" : "")}
              onClick={() =>
                id === "evaluation" ? void openEvaluation() : setTab(id)
              }
            >
              {label}
            </button>
          ))}
      </nav>
      {["build", "maintenance", "evaluation", "operations"].includes(tab) && (
        <div className="knowledge-maintenance-scope">
          <label>
            处理范围
            <select
              aria-label="知识范围"
              value={scope}
              disabled={busy || editing}
              onChange={(event) => {
                setScope(event.target.value);
                setPreview(null);
              }}
            >
              {Object.entries(scopeLabel)
                .filter(
                  ([id]) => !namespaceOptions || namespaceOptions.includes(id),
                )
                .map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
            </select>
          </label>
          {["build", "maintenance"].includes(tab) && (
            <label>
              定位问题或关键词
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="可选：缩小处理范围"
              />
            </label>
          )}
        </div>
      )}
      {tab !== "wiki" && ["graph", "query"].includes(tab) && (
        <>
          <div className="knowledge-toolbar">
            <label>
              知识范围
              <select
                aria-label="知识范围"
                disabled={busy || editing}
                value={scope}
                onChange={(e) => {
                  setScope(e.target.value);
                  setPreview(null);
                }}
              >
                {Object.entries(scopeLabel)
                  .filter(
                    ([id]) =>
                      !namespaceOptions || namespaceOptions.includes(id),
                  )
                  .map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
              </select>
            </label>
            <label className="knowledge-query">
              问题或关键词
              <input
                disabled={busy}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="例如：哪些方法有适用范围限制？"
              />
            </label>
            {["wiki", "graph", "query"].includes(tab) && (
              <button
                className="btn"
                disabled={busy}
                onClick={() =>
                  void perform(
                    async () => {
                      if (tab === "wiki") {
                        const r = await api(
                          base +
                            "/wiki?namespace=" +
                            scope +
                            "&query=" +
                            encodeURIComponent(query) +
                            (focusFileId
                              ? "&file_id=" + encodeURIComponent(focusFileId)
                              : ""),
                        );
                        wikiQuery.current = query.trim();
                        setPages(r.items ?? []);
                        setPage(null);
                        setEditing(false);
                        return r;
                      }
                      if (tab === "graph") {
                        const r = await send(base + "/graph/search", {
                          query,
                          namespace: scope,
                          file_ids: focusFileId ? [focusFileId] : undefined,
                          depth: 1,
                        });
                        setGraph(r);
                        setSelected([]);
                        return r;
                      }
                      const r = await send(base + "/query", {
                        query,
                        namespace: scope,
                        file_ids: focusFileId ? [focusFileId] : undefined,
                        include_originals: includeOriginals,
                      });
                      setAnswer(r);
                      return r;
                    },
                    "检索完成",
                    false,
                  )
                }
              >
                检索知识
              </button>
            )}
          </div>
        </>
      )}
      {tab === "operations" && (
        <KnowledgeOperations
          pid={pid}
          paidBlocked={paidBlocked}
          selectedClaim={selected[0]}
          namespace={scope}
          fileIds={focusFileId ? [focusFileId] : undefined}
        />
      )}
      {tab === "query" && (
        <label className="knowledge-check">
          <input
            type="checkbox"
            checked={includeOriginals}
            onChange={(e) => setIncludeOriginals(e.target.checked)}
          />
          补充原文检索（按本地向量与重排设置执行，首次可能较慢）
        </label>
      )}
      {tab === "query" && !includeOriginals && (
        <p className="knowledge-notice">
          当前沿已有图谱与Wiki定位原文阅读范围，独立的全文和向量候选未加入本次查询。
        </p>
      )}

      {tab === "wiki" && (
        <div className="research-columns has-inspector wiki-workbench">
          <aside className="research-directory" aria-label="知识页面">
            <div className="research-directory-heading">
              <h3>主题目录</h3>
              <span>{pages.length} 项</span>
            </div>
            <div className="knowledge-toolbar">
              <label>
                知识范围
                <select
                  aria-label="知识范围"
                  disabled={busy || editing}
                  value={scope}
                  onChange={(e) => {
                    setScope(e.target.value);
                    setPreview(null);
                  }}
                >
                  {Object.entries(scopeLabel)
                    .filter(
                      ([id]) =>
                        !namespaceOptions || namespaceOptions.includes(id),
                    )
                    .map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                </select>
              </label>
              <label className="knowledge-query">
                问题或关键词
                <input
                  disabled={busy}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="例如：哪些方法有适用范围限制？"
                />
              </label>
              {["wiki", "graph", "query"].includes(tab) && (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() =>
                    void perform(
                      async () => {
                        if (tab === "wiki") {
                          const r = await api(
                            base +
                              "/wiki?namespace=" +
                              scope +
                              "&query=" +
                              encodeURIComponent(query) +
                              (focusFileId
                                ? "&file_id=" + encodeURIComponent(focusFileId)
                                : ""),
                          );
                          wikiQuery.current = query.trim();
                          setPages(r.items ?? []);
                          setPage(null);
                          setEditing(false);
                          return r;
                        }
                        if (tab === "graph") {
                          const r = await send(base + "/graph/search", {
                            query,
                            namespace: scope,
                            file_ids: focusFileId ? [focusFileId] : undefined,
                            depth: 1,
                          });
                          setGraph(r);
                          setSelected([]);
                          return r;
                        }
                        const r = await send(base + "/query", {
                          query,
                          namespace: scope,
                          file_ids: focusFileId ? [focusFileId] : undefined,
                          include_originals: includeOriginals,
                        });
                        setAnswer(r);
                        return r;
                      },
                      "检索完成",
                      false,
                    )
                  }
                >
                  检索知识
                </button>
              )}
            </div>
            <div className="knowledge-page-list">
              {pages
                .filter((p) => scope === "all" || p.namespace === scope)
                .map((p) => (
                  <button
                    className={
                      "knowledge-page-item " +
                      (p.id === page?.id ? "selected" : "")
                    }
                    key={p.id}
                    onClick={() => void openPage(p.id)}
                  >
                    <small>{pageLabel[p.kind] ?? p.kind}</small>
                    <strong>{titleOf(p.title)}</strong>
                    <span>
                      {p.current === false
                        ? "需复查或待发布"
                        : "查看知识与出处"}
                    </span>
                  </button>
                ))}
              {!pages.length && (
                <p>
                  尚无知识页面。可先在“构建与更新”整理原文，再运行模型编译。
                </p>
              )}
            </div>
          </aside>
          <article className="research-content knowledge-page-detail reference-document">
            {!page ? (
              <div className="knowledge-empty">
                <h3>选择一个主题，查看它的认识和出处。</h3>
                <p>
                  原文摘录、模型综合和人工核对分别记录。候选论断不会因进入 Wiki
                  自动成为已验证事实。
                </p>
              </div>
            ) : (
              <>
                <div className="section-heading">
                  <div>
                    <small>
                      {scopeLabel[page.namespace]} · {pageLabel[page.kind]}
                    </small>
                    <h3>{titleOf(page.title)}</h3>
                    <p>{page.current ? "当前发布版本" : "候选或来源需复查"}</p>
                  </div>
                  <button className="btn" onClick={() => setEditing(!editing)}>
                    {editing ? "结束编辑" : "编辑与备注"}
                  </button>
                </div>
                {diff.length > 0 && (
                  <pre className="knowledge-pre">{diff.join("\n")}</pre>
                )}
                {(editing ? blocks : page.blocks).map(
                  (b: Row, index: number) => (
                    <section
                      className="knowledge-block"
                      key={b.key ?? b.block_key}
                    >
                      <small>
                        {b.author === "human"
                          ? "人工编辑"
                          : b.author === "literal"
                            ? "原文摘录"
                            : "模型整理"}{" "}
                        ·{" "}
                        {b.kind === "inference"
                          ? "综合推断，待核验"
                          : b.kind === "question"
                            ? "待解问题"
                            : b.kind === "note"
                              ? "说明或备注"
                              : "需按论断核对"}
                      </small>
                      {b.metadata?.requires_source_review && (
                        <p className="knowledge-error">
                          保留了旧来源；发布前请重新关联当前原文，或调整为明确的个人备注。
                        </p>
                      )}
                      {editing ? (
                        <>
                          <select
                            aria-label={"第" + (index + 1) + "块类型"}
                            value={b.kind}
                            onChange={(e) =>
                              setBlocks(
                                blocks.map((x, i) =>
                                  i === index
                                    ? { ...x, kind: e.target.value }
                                    : x,
                                ),
                              )
                            }
                          >
                            {[
                              "note",
                              "fact",
                              "comparison",
                              "synthesis",
                              "inference",
                              "question",
                              "excerpt",
                              "limitation",
                            ].map((k) => (
                              <option key={k} value={k}>
                                {
                                  {
                                    note: "说明与备注",
                                    fact: "事实性陈述",
                                    comparison: "对照比较",
                                    synthesis: "综合整理",
                                    inference: "推断（待核验）",
                                    question: "待解问题",
                                    excerpt: "原文摘录",
                                    limitation: "条件与局限",
                                  }[k]
                                }
                              </option>
                            ))}
                          </select>
                          <textarea
                            aria-label={"第" + (index + 1) + "块内容"}
                            rows={5}
                            value={b.text}
                            onChange={(e) =>
                              setBlocks(
                                blocks.map((x, i) =>
                                  i === index
                                    ? { ...x, text: e.target.value }
                                    : x,
                                ),
                              )
                            }
                          />
                          <div className="actions">
                            <button
                              className="btn small"
                              onClick={() =>
                                setBlocks(blocks.filter((_, i) => i !== index))
                              }
                            >
                              移除本块
                            </button>
                            <button
                              className="btn small"
                              onClick={() =>
                                setBlocks(
                                  blocks.map((x, i) =>
                                    i === index ? { ...x, sources: [] } : x,
                                  ),
                                )
                              }
                            >
                              清除本块旧来源
                            </button>
                          </div>
                          <select
                            aria-label={"为第" + (index + 1) + "块添加当前来源"}
                            value=""
                            onChange={(e) => {
                              const a = sourceOptions.find(
                                (a) => a.claim_version_id === e.target.value,
                              );
                              if (a)
                                setBlocks(
                                  blocks.map((x, i) =>
                                    i === index
                                      ? {
                                          ...x,
                                          sources: [
                                            ...(x.sources ?? []),
                                            {
                                              type: "assertion",
                                              id: a.claim_version_id,
                                              version:
                                                a.version_hash ??
                                                a.content_hash,
                                            },
                                          ],
                                        }
                                      : x,
                                  ),
                                );
                            }}
                          >
                            <option value="">选择本页检索到的当前论断</option>
                            {sourceOptions.map((a) => (
                              <option
                                key={a.claim_version_id}
                                value={a.claim_version_id}
                              >
                                {a.proposition.statement.slice(0, 100)}
                              </option>
                            ))}
                          </select>
                        </>
                      ) : (
                        <p className="knowledge-text">{b.text}</p>
                      )}
                      <div className="actions">
                        {b.sources?.map((r: Row, i: number) => (
                          <button
                            className="btn small"
                            key={i}
                            onClick={() => void showSource(r)}
                          >
                            查看出处 {i + 1}
                          </button>
                        ))}
                      </div>
                    </section>
                  ),
                )}
                {editing && (
                  <div className="actions">
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          const r = await send(base + "/graph/search", {
                            query: query || page.title,
                            namespace: page.namespace,
                            depth: 1,
                            edge_limit: 30,
                          });
                          setSourceOptions(r.assertions);
                          return r;
                        }, "已找到当前论断，请逐块选择与文字相符的来源")
                      }
                    >
                      检索本页当前来源
                    </button>
                    <button
                      className="btn"
                      onClick={() =>
                        setBlocks([
                          ...blocks,
                          {
                            key: "note-" + Date.now(),
                            kind: "note",
                            text: "",
                            sources: [],
                          },
                        ])
                      }
                    >
                      新增备注
                    </button>
                    <button
                      className="btn primary"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          const r = await send(
                            base + `/wiki/${page.id}/revisions`,
                            {
                              base_version_id: page.current_version_id,
                              blocks,
                            },
                          );
                          if (r.conflict_id) {
                            setDraft(null);
                            setTab("operations");
                          } else setDraft(r);
                          return r;
                        }, "修订候选已保存，请核对后发布")
                      }
                    >
                      保存修订候选
                    </button>
                  </div>
                )}
                {draft && (
                  <button
                    className="btn primary"
                    disabled={busy}
                    onClick={() =>
                      void perform(async () => {
                        const r = await send(
                          base + `/wiki/${page.id}/publish`,
                          {
                            version_id: draft.version.id,
                            base_version_id: page.current_version_id,
                          },
                        );
                        setPage(r);
                        setDraft(null);
                        setEditing(false);
                        return r;
                      }, "知识页面已发布")
                    }
                  >
                    发布当前修订
                  </button>
                )}
              </>
            )}
          </article>
          <aside className="research-inspector">
            <ReferenceInspector title="当前主题">
              {page ? (
                <>
                  <p>
                    {pageLabel[page.kind]} · {scopeLabel[page.namespace]}
                  </p>
                  <span className={`badge ${page.current ? "green" : ""}`}>
                    {page.current ? "当前发布版本" : "候选或来源需复查"}
                  </span>
                  <dl className="reference-metadata">
                    <div>
                      <dt>内容块</dt>
                      <dd>{page.blocks.length} 段</dd>
                    </div>
                    <div>
                      <dt>历史版本</dt>
                      <dd>{page.versions.length} 个</dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p>从左侧选择主题，正文和出处将显示在中间。</p>
              )}
            </ReferenceInspector>
            {page && (
              <ReferenceInspector title="版本与追溯">
                {" "}
                <div className="actions">
                  <label>
                    历史版本
                    <select
                      value={page.version?.id ?? ""}
                      onChange={(e) => void openPage(page.id, e.target.value)}
                    >
                      {page.versions.map((v: Row) => (
                        <option value={v.id} key={v.id}>
                          {v.created_at.slice(0, 16)} ·{" "}
                          {stateLabel[v.state] ?? v.state}
                        </option>
                      ))}
                    </select>
                  </label>
                  {page.versions.length > 1 && (
                    <button
                      className="btn"
                      onClick={() =>
                        void perform(async () => {
                          const other = page.versions.find(
                            (v: Row) => v.id !== page.version?.id,
                          );
                          const r = await api(
                            base +
                              `/wiki/${page.id}/diff?left=${other.id}&right=${page.version.id}`,
                          );
                          setDiff(r.diff);
                          return r;
                        }, "版本差异已加载")
                      }
                    >
                      比较版本
                    </button>
                  )}
                  <button
                    className="btn"
                    onClick={() =>
                      void perform(async () => {
                        const r = await api(
                          base +
                            `/lineage?kind=wiki_version&identity=${page.version.id}`,
                        );
                        setTrace(r);
                        return r;
                      }, "已读取来源链")
                    }
                  >
                    来源链
                  </button>
                </div>
              </ReferenceInspector>
            )}
            <ReferenceInspector title="阅读提示">
              <p>原文摘录、模型整理和人工备注分别标注。</p>
              <p>点击每段下方的“查看出处”，核对原文、条件与局限。</p>
              <p>编辑后先保存修订候选，再发布当前修订。</p>
            </ReferenceInspector>
          </aside>
        </div>
      )}

      {tab === "graph" && (
        <div>
          {!graph ? (
            <p>输入问题后检索，从候选论断查看关系和原文。</p>
          ) : (
            <>
              <p>
                {graph.nodes.length} 个实体，{graph.assertions.length}{" "}
                条候选论断。
                {graph.truncated
                  ? "已达到本次展开上限，可缩小问题继续检索。"
                  : "本次结果为问题范围内的局部关系。"}
              </p>
              <GraphView
                value={graph}
                onNode={(id) =>
                  void perform(async () => {
                    const r = await api(base + "/entities/" + id);
                    setTrace(r);
                    return r;
                  }, "已读取实体与别名")
                }
              />
              {graph.assertions.map((a: Row) => (
                <article key={a.claim_version_id} className="knowledge-block">
                  <label className="knowledge-check">
                    <input
                      type="checkbox"
                      checked={selected.includes(a.claim_version_id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, a.claim_version_id]
                            : selected.filter(
                                (id) => id !== a.claim_version_id,
                              ),
                        )
                      }
                    />
                    <strong>{a.attribution || "项目候选论断"}</strong>
                    <span>
                      {stateLabel[a.support_state] ?? a.support_state}
                    </span>
                  </label>
                  <p className="knowledge-text">{a.proposition.statement}</p>
                  {a.conditions.length > 0 && (
                    <p>适用条件：{a.conditions.join("；")}</p>
                  )}
                  <button
                    className="btn"
                    onClick={() =>
                      void showSource({
                        type: "assertion",
                        id: a.claim_version_id,
                      })
                    }
                  >
                    原文与支持性核对
                  </button>
                </article>
              ))}
              <div className="knowledge-form">
                <label>
                  综合页面名称
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="例如：方法适用范围比较"
                  />
                </label>
                <label>
                  页面类型
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    {[
                      "topic",
                      "comparison",
                      "disagreement",
                      "concept",
                      "method",
                    ].map((k) => (
                      <option key={k} value={k}>
                        {pageLabel[k]}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="btn primary"
                  disabled={busy || paidBlocked || !selected.length || !title}
                  onClick={() =>
                    void perform(async () => {
                      const p = await send(base + "/wiki", {
                        namespace: scope === "all" ? "literature" : scope,
                        kind,
                        title,
                        slug: "topic-" + Date.now(),
                      });
                      return send(base + `/wiki/${p.id}/synthesize`, {
                        sources: selectedRefs(),
                        budget_limit: budget,
                      });
                    }, "已提交模型综合，结果在任务中心，审阅后发布")
                  }
                >
                  将所选论断综合为 Wiki（计费）
                </button>
                <button
                  className="btn"
                  disabled={busy || paidBlocked || !selected.length}
                  onClick={() =>
                    void perform(
                      () =>
                        send(base + "/assess-with-model", {
                          claim_ids: selected,
                          budget_limit: budget,
                        }),
                      "已提交模型辅助核验",
                    )
                  }
                >
                  AI 辅助核验所选论断（计费）
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "query" && (
        <div>
          <p>
            检索后查看拟读文献。执行阅读计划会保存实际读取范围，随后可在写作工作台生成章节候选。
          </p>
          <label>
            目标章节
            <select
              aria-label="目标章节"
              value={section}
              onChange={(e) => setSection(e.target.value)}
            >
              <option value="">选择章节</option>
              {sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          <div className="actions">
            <button
              className="btn"
              disabled={busy || paidBlocked || !query}
              onClick={() =>
                void perform(
                  () =>
                    send(base + "/query/answer", {
                      query,
                      namespace: scope,
                      file_ids: focusFileId ? [focusFileId] : undefined,
                      budget_limit: budget,
                    }),
                  "已提交有据回答任务，仍需核对支持性",
                )
              }
            >
              生成有据回答（计费）
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const r = await api(base + "/answers");
                  setReplies(r);
                  return r;
                }, "已读取回答记录")
              }
            >
              最近回答
            </button>
          </div>
          {replies.map((r) => (
            <div className="knowledge-plan" key={r.id}>
              <span>{r.manifest.question}</span>
              <button
                className="btn small"
                onClick={() =>
                  void perform(async () => {
                    const a = await api(base + "/answers/" + r.id);
                    setTrace(a);
                    return a;
                  }, "回答已加载")
                }
              >
                查看回答
              </button>
              <button
                className="btn small"
                onClick={() =>
                  void perform(async () => {
                    const saved = await send(
                      base + `/answers/${r.id}/save-wiki`,
                      {
                        namespace: scope === "all" ? "literature" : scope,
                        kind: "topic",
                        title: r.manifest.question,
                        slug: "answer-" + r.id,
                      },
                    );
                    await openPage(saved.page_id);
                    setTab("wiki");
                    return saved;
                  }, "回答已保存为Wiki候选，保留底层原文来源")
                }
              >
                保存为 Wiki 候选
              </button>
            </div>
          ))}
          {answer && (
            <>
              {answer.argument_plan && (
                <details>
                  <summary>论证计划：候选论点、比较维度、反证与缺口</summary>
                  <p>{answer.argument_plan.missing_materials.join("；")}</p>
                  {answer.argument_plan.candidate_arguments.map((a: Row) => (
                    <div className="knowledge-block" key={a.claim_id}>
                      <p>{a.statement}</p>
                      <p>
                        归属：{a.attribution} · 支持性：{a.support_state} ·
                        限定条件：{a.conditions.join("；")}
                      </p>
                      <p>
                        支持来源 {a.support_sources.length} · 反证/限制来源{" "}
                        {a.counter_sources.length} · 原文仍需实际读取
                      </p>
                    </div>
                  ))}
                  <pre className="knowledge-text">
                    {JSON.stringify(
                      answer.argument_plan.comparability_checks,
                      null,
                      2,
                    )}
                  </pre>
                </details>
              )}
              <p>
                任务路线：{answer.route}；拟读 {answer.reading_plan.length}{" "}
                个范围。
                {answer.timings_ms &&
                  `本地检索 ${(Number(answer.timings_ms.total) / 1000).toFixed(2)} 秒；覆盖 ${new Set(answer.reading_plan.map((p: Row) => p.file_id || p.evidence_id)).size} 份原文或记录。`}
              </p>
              {answer.reading_plan.map((p: Row, i: number) => (
                <div className="knowledge-plan" key={i}>
                  <span>
                    文献 {i + 1} · {p.char_end - p.char_start} 字符 · {p.reason}
                  </span>
                  <button
                    className="btn small"
                    onClick={() =>
                      p.file_id
                        ? onSource(p.file_id)
                        : void showSource({
                            type: p.observation_id ? "observation" : "evidence",
                            id: p.observation_id ?? p.evidence_id,
                          })
                    }
                  >
                    查看原件
                  </button>
                </div>
              ))}
              <button
                className="btn primary"
                disabled={busy || !section}
                onClick={() =>
                  void perform(
                    () =>
                      send(base + "/reading-plan", {
                        query,
                        namespace: scope,
                        file_ids: focusFileId ? [focusFileId] : undefined,
                        section_id: section,
                      }),
                    "原文阅读范围已保存；方法、条件与局限仍需核对",
                  )
                }
              >
                执行章节阅读计划
              </button>
            </>
          )}
        </div>
      )}

      {tab === "build" && (
        <div className="knowledge-maintenance-panel">
          <header className="reference-document-heading">
            <span className="research-kicker">构建与更新</span>
            <h3>整理原文，更新知识库</h3>
            <p>选择整理方式，预览会处理的原件和请求数量，再开始构建。</p>
          </header>
          <div className="knowledge-form">
            <label>
              整理方式
              <select
                aria-label="整理方式"
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value);
                  setPreview(null);
                }}
              >
                <option value="literal">本地原文摘录（不调用模型）</option>
                <option value="llm">AI 编译（按项目接口计费）</option>
              </select>
            </label>
            <label>
              本次模型预算（{status.budget?.currency ?? "接口币种"}）
              <input
                type="number"
                min="0"
                step="0.1"
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
            </label>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const p = await send(base + "/builds/preview", {
                    mode,
                    namespace: scope,
                    file_ids: focusFileId ? [focusFileId] : undefined,
                    budget_limit: budget,
                  });
                  setPreview(p);
                  return p;
                }, "构建计划已准备")
              }
            >
              预览构建范围
            </button>
          </div>
          {preview && (
            <div className="knowledge-block">
              <p>
                {preview.file_count} 份原件，预计{" "}
                {preview.estimated_extraction_calls} 次抽取请求。
                {preview.mode === "literal"
                  ? "此方式建立可追溯摘录与关系索引，未完成模型综合。"
                  : "实际费用与完成范围以任务账本为准。"}
              </p>
              <p>{preview.estimate_note}</p>
              <details>
                <summary>查看原件清单</summary>
                {preview.files.map((f: Row) => (
                  <p key={f.id}>
                    {f.name} · {f.units} 个原文单元
                  </p>
                ))}
              </details>
              <button
                className="btn primary"
                disabled={busy || (mode === "llm" && paidBlocked)}
                onClick={() =>
                  void perform(
                    () =>
                      send(base + "/builds", {
                        mode,
                        namespace: scope,
                        file_ids: focusFileId ? [focusFileId] : undefined,
                        budget_limit: budget,
                      }),
                    "知识构建已进入任务中心",
                  )
                }
              >
                开始构建
              </button>
            </div>
          )}
          <details className="research-advanced">
            <summary>高级索引与自动更新设置</summary>
            <label>
              资料变化后的更新方式
              <select
                value={status.refresh_mode ?? "literal"}
                onChange={(e) =>
                  void perform(
                    () =>
                      send(
                        base + "/settings",
                        {
                          enabled: !!status.enabled,
                          refresh_mode: e.target.value,
                          refresh_budget: budget,
                        },
                        "PUT",
                      ),
                    "自动更新方式已保存，AI更新仍受项目与单次预算限制",
                  )
                }
              >
                <option value="literal">本地原文更新</option>
                <option value="llm" disabled={paidBlocked}>
                  AI编译及受影响主题候选更新（计费）
                </option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={retryAcknowledged}
                onChange={(e) => setRetryAcknowledged(e.target.checked)}
              />
              {(graph?.nodes?.length ?? 0) > 18 && (
                <p>
                  关系图仅预览前18个实体；完整的候选论断与出处仍在下方列表中。
                </p>
              )}
              已核对远端记录，知悉重试可能再次计费（仅在续接结果不明的请求时使用）
            </label>
            <label>
              <input
                type="checkbox"
                checked={!!status.auto_refresh}
                onChange={(e) =>
                  void perform(
                    () =>
                      send(
                        base + "/settings",
                        {
                          enabled: !!status.enabled,
                          auto_refresh: e.target.checked,
                        },
                        "PUT",
                      ),
                    "自动更新设置已保存",
                  )
                }
              />
              来源变化后自动更新（默认本地摘录，不额外调用模型）
            </label>
            <p>
              {status.plugin?.installed
                ? "已安装 " + status.plugin.expected_version
                : "尚未安装可选组件"}
              ；计算与模型调用均有独立运行记录。
            </p>
            <div className="knowledge-form">
              <label>
                <input
                  type="checkbox"
                  checked={fullIndex}
                  onChange={(e) => setFullIndex(e.target.checked)}
                />
                构建当前范围的完整索引；未选择时仅构建问题附近的局部索引
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={pluginReports}
                  disabled={paidBlocked}
                  onChange={(e) => setPluginReports(e.target.checked)}
                />
                生成社区报告（调用模型并计费；未选择时只做本地社区计算）
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={pluginVectors}
                  onChange={(e) => setPluginVectors(e.target.checked)}
                />
                同时建立真实本地向量（用于局部与 DRIFT）
              </label>
              <button
                className="btn"
                disabled={
                  busy ||
                  !status.plugin?.installed ||
                  (pluginReports && paidBlocked)
                }
                onClick={() =>
                  void perform(
                    () =>
                      send(base + "/graphrag/build", {
                        namespace: scope,
                        file_ids: focusFileId ? [focusFileId] : undefined,
                        query,
                        reports: pluginReports,
                        embeddings: pluginVectors,
                        full_index: fullIndex,
                        budget_limit: budget,
                      }),
                    "已提交微软社区计算，索引编号在任务结果中",
                  )
                }
              >
                {pluginReports
                  ? "生成微软社区报告（计费）"
                  : "构建本地社区索引（不调用模型）"}
              </button>
            </div>
            <div className="knowledge-form">
              <label>
                已生成的索引编号
                <input
                  value={pluginId}
                  onChange={(e) => setPluginId(e.target.value)}
                />
              </label>
              <label>
                查询方式
                <select
                  value={pluginMode}
                  onChange={(e) => setPluginMode(e.target.value)}
                >
                  <option value="global">Global 全局</option>
                  <option value="local">Local 局部</option>
                  <option value="drift">DRIFT 探索</option>
                </select>
              </label>
              <button
                className="btn"
                disabled={busy || paidBlocked || !pluginId || !query}
                onClick={() =>
                  void perform(
                    () =>
                      send(base + "/graphrag/query", {
                        artifact_id: pluginId,
                        mode: pluginMode,
                        query,
                        budget_limit: budget,
                        acknowledge_possible_duplicate_charge:
                          retryAcknowledged,
                      }),
                    "已提交微软查询，结果在任务中心",
                  )
                }
              >
                运行所选查询
              </button>
              <button
                className="btn"
                disabled={busy || !pluginId || (pluginReports && paidBlocked)}
                onClick={() =>
                  void perform(
                    () =>
                      send(base + "/graphrag/build", {
                        namespace: scope,
                        file_ids: focusFileId ? [focusFileId] : undefined,
                        query,
                        reports: pluginReports,
                        embeddings: pluginVectors,
                        full_index: fullIndex,
                        resume_artifact_id: pluginId,
                        budget_limit: budget,
                        acknowledge_possible_duplicate_charge:
                          retryAcknowledged,
                      }),
                    "已提交续建，来源和配置需与原索引一致",
                  )
                }
              >
                续建所填索引
              </button>
            </div>
          </details>
          <div className="knowledge-task-list">
            {(status.tasks ?? []).map((t: Row) => (
              <div key={t.id} className="knowledge-plan">
                <span>
                  {t.phase || "知识任务"} · {stateLabel[t.status] ?? t.status} ·{" "}
                  {t.progress}%
                </span>
                {([
                  "failed",
                  "incomplete",
                  "budget_blocked",
                  "cancelled",
                ].includes(t.status) ||
                  (retryAcknowledged && t.status === "outcome_unknown")) &&
                  t.kind === "graph_wiki_build" && (
                    <button
                      className="btn small"
                      disabled={busy || (t.mode === "llm" && paidBlocked)}
                      onClick={() =>
                        void perform(
                          () =>
                            send(base + `/builds/${t.id}/resume`, {
                              budget_limit: budget,
                              acknowledge_possible_duplicate_charge:
                                retryAcknowledged,
                            }),
                          "已提交续接，复用有效完成批次",
                        )
                      }
                    >
                      续接
                    </button>
                  )}
                {["queued", "running"].includes(t.status) && (
                  <button
                    className="btn small"
                    onClick={() =>
                      void perform(
                        () => send("/tasks/" + t.id + "/cancel"),
                        "已请求取消后续步骤",
                      )
                    }
                  >
                    取消
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "maintenance" && (
        <div className="knowledge-maintenance-panel">
          <header className="reference-document-heading">
            <span className="research-kicker">检查与版本</span>
            <h3>检查来源，保留可回退的版本</h3>
            <p>查看断链与过期内容，检查后再发布知识快照或导出追溯包。</p>
          </header>
          <button
            className="btn"
            disabled={busy || paidBlocked}
            onClick={() =>
              void perform(
                () =>
                  send(base + "/checks", {
                    semantic: true,
                    namespace: scope,
                    file_ids: focusFileId ? [focusFileId] : undefined,
                    query,
                    limit: 8,
                    budget_limit: budget,
                  }),
                "已提交有界语义疑点检查；模型仅提出回读线索",
              )
            }
          >
            AI语义疑点检查（计费）
          </button>
          <div className="actions">
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const r = await send(base + "/checks");
                  setChecks(r);
                  return r;
                }, "知识检查完成")
              }
            >
              检查出处、断链与过期
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(
                  () => send(base + "/releases", {}),
                  "知识版本已发布",
                )
              }
            >
              发布知识快照
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(
                  () => send(base + "/exports", {}),
                  "知识导出已进入任务中心",
                )
              }
            >
              导出 Markdown 与追溯包
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const r = await api(base + "/releases");
                  setTrace({ releases: r });
                  return r;
                }, "知识版本已读取")
              }
            >
              历史知识版本
            </button>
          </div>
          {checks && (
            <>
              <p>发现 {checks.count} 个待处理项。语义分歧提示仍需核对原文。</p>
              {checks.findings.map((f: Row, i: number) => (
                <div className="knowledge-plan" key={i}>
                  <span>{f.message}</span>
                  {f.page_id && (
                    <button
                      className="btn small"
                      onClick={() => {
                        setTab("wiki");
                        void openPage(f.page_id);
                      }}
                    >
                      查看页面
                    </button>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {tab === "evaluation" && (
        <div className="knowledge-maintenance-panel">
          <header className="reference-document-heading">
            <span className="research-kicker">对照评测</span>
            <h3>用实际问题检查知识质量</h3>
            <p>
              先准备问题与原文依据，再比较检索结果。自动评测与人工核对分别记录。
            </p>
          </header>
          <p>
            80
            个问题草稿需要按真实写作任务改写和标注。召回结果不代表论断已经获得支持。
          </p>
          <label>
            <input
              type="checkbox"
              checked={generateCompare}
              disabled={paidBlocked && !generateCompare}
              onChange={(e) => setGenerateCompare(e.target.checked)}
            />
            生成四组回答供盲审（调用模型并计费）
          </label>
          <div className="actions">
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const r = await send(base + "/evaluation/cases/seed");
                  setCases(r.cases);
                  return r;
                }, "问题草稿已建立，尚未代填人工标准")
              }
            >
              建立 80 个任务草稿
            </button>
            <button
              className="btn"
              disabled={
                busy ||
                (generateCompare && paidBlocked) ||
                !cases.some((c) => c.state === "labelled")
              }
              onClick={() =>
                void perform(
                  () =>
                    send(base + "/evaluation/comparisons", {
                      case_ids: cases
                        .filter((c) => c.state === "labelled")
                        .map((c) => c.id),
                      generate_answers: generateCompare,
                      budget_limit: budget,
                    }),
                  "已提交四组对照；结果在任务中心",
                )
              }
            >
              运行四组对照
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const r = await api(base + "/evaluation/comparisons");
                  const b = r[0]
                    ? await api(
                        base + `/evaluation/comparisons/${r[0].id}/blind`,
                      )
                    : null;
                  setComparison(b);
                  return b;
                }, "已读取隐去检索组合的对照结果")
              }
            >
              查看最近对照
            </button>
          </div>
          <div className="knowledge-case-list">
            {cases.map((c) => (
              <button
                className="knowledge-page-item"
                key={c.id}
                onClick={() =>
                  setCaseEdit({
                    ...c,
                    gold: JSON.parse(c.gold_json),
                    compared_original: false,
                    real_task_confirmed: false,
                  })
                }
              >
                <small>
                  {c.state === "labelled" ? "已人工标注" : "待改写与标注"} ·{" "}
                  {c.category}
                </small>
                <span>{c.question}</span>
              </button>
            ))}
          </div>
          {comparison && (
            <>
              {comparison.cases?.map((c: Row) => (
                <article className="knowledge-block" key={c.case_id}>
                  <h3>{c.question}</h3>
                  {c.groups.map((g: Row) => (
                    <section className="knowledge-block" key={g.label}>
                      <strong>{g.label}</strong>
                      {g.answer?.blocks?.map((b: Row, i: number) => (
                        <p key={i} className="knowledge-text">
                          {b.text}
                        </p>
                      ))}
                      <p>读取范围：{g.unit_ids.length} 个原文单元</p>
                      {!g.answer?.blocks?.length && (
                        <p>本组只有检索定位，尚无实际回答可供逐句盲审。</p>
                      )}
                      <button
                        className="btn small"
                        disabled={
                          busy ||
                          comparison.revealed ||
                          !g.answer?.blocks?.length
                        }
                        onClick={() =>
                          setSemanticReview({
                            case_id: c.case_id,
                            blind_group: g.label,
                            supported_claims: 0,
                            checkable_claims: 0,
                            qualifiers_preserved: 0,
                            qualifiers_required: 0,
                            wrong_citations: 0,
                            unsupported_expansions: 0,
                            compared_original: false,
                          })
                        }
                      >
                        记录本组人工判断
                      </button>
                    </section>
                  ))}
                </article>
              ))}
              <button
                className="btn"
                onClick={() =>
                  void perform(async () => {
                    const r = await api(
                      base +
                        `/evaluation/comparisons/${comparison.id}/blind?reveal=true`,
                    );
                    setTrace(r);
                    setComparison({ ...comparison, revealed: true });
                    return r;
                  }, "对照组合已揭示")
                }
              >
                揭示组合与检索指标
              </button>
            </>
          )}
        </div>
      )}

      {source && (
        <WorkspaceDialog
          wide
          title="论断与原文核对"
          onClose={() => setSource(null)}
        >
          {source.claim && (
            <>
              <h3>{source.claim.proposition.statement}</h3>
              <p>
                支持性：
                {stateLabel[source.claim.support_state] ??
                  source.claim.support_state}
              </p>
              <p>来源归属：{source.claim.attribution}</p>
            </>
          )}
          {inspection && (
            <>
              {inspection.kind && (
                <p>
                  证据类型：
                  {(
                    {
                      code_observation: "代码观察",
                      test_definition: "测试定义",
                      test_run: "测试执行记录",
                      deployment: "部署记录",
                      data_result: "登记数据结果",
                      author_statement: "作者说明",
                      literature: "文献原文",
                      guidance_observation: "指导资料观察",
                    } as Record<string, string>
                  )[inspection.kind] ?? inspection.kind}
                </p>
              )}
              <p className="knowledge-text">{inspection.text}</p>
              <p>
                原文或记录范围：{inspection.metadata?.char_start ?? 0}—
                {inspection.metadata?.char_end ?? inspection.text.length}
                ；读取不等于语义已核验。
              </p>
              {inspection.file_id && (
                <button
                  className="btn"
                  onClick={() =>
                    onSource(inspection.file_id, inspection.locator?.page)
                  }
                >
                  对照原件
                </button>
              )}
            </>
          )}
          {source.claim && inspection && (
            <div className="knowledge-form vertical">
              <label>
                核对人
                <input
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                />
              </label>
              <label>
                逐字原文引句
                <textarea
                  value={quote}
                  onChange={(e) => setQuote(e.target.value)}
                  rows={4}
                />
              </label>
              <label>
                支持性
                <select
                  value={support}
                  onChange={(e) => setSupport(e.target.value)}
                >
                  {[
                    "not_checked",
                    "supported",
                    "partial",
                    "contradicted",
                    "insufficient",
                  ].map((s) => (
                    <option key={s} value={s}>
                      {stateLabel[s]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                判断理由
                <textarea
                  rows={3}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={compared}
                  onChange={(e) => setCompared(e.target.checked)}
                />
                已对照原件、适用条件与结论范围
              </label>
              <button
                className="btn primary"
                disabled={busy || !reviewer || !note || !compared}
                onClick={() =>
                  void perform(
                    () =>
                      send(
                        base +
                          `/assertions/${source.claim.claim_version_id}/assessments`,
                        {
                          observation_id: inspection.evidence
                            ? undefined
                            : inspection.id,
                          evidence_id: inspection.evidence
                            ? inspection.id
                            : undefined,
                          quote,
                          support,
                          reviewer,
                          note,
                          compared_original: compared,
                        },
                      ),
                    "人工支持性记录已保存，未修改原文核对状态",
                  )
                }
              >
                保存本论断的核对
              </button>
            </div>
          )}
          {!inspection && (
            <pre className="knowledge-pre">
              {JSON.stringify(source, null, 2)}
            </pre>
          )}
        </WorkspaceDialog>
      )}
      {trace && (
        <WorkspaceDialog
          wide
          title="来源与版本记录"
          onClose={() => setTrace(null)}
        >
          <pre className="knowledge-pre">{JSON.stringify(trace, null, 2)}</pre>
          {trace.name && trace.current_version_id && (
            <div className="knowledge-form vertical">
              <EntitySplitControl
                base={base}
                entity={trace}
                busy={busy}
                run={perform}
                onResult={setTrace}
              />
              <label>
                要保留的同类实体
                <select
                  value={mergeTarget}
                  onChange={(e) => setMergeTarget(e.target.value)}
                >
                  <option value="">选择实体</option>
                  {(graph?.nodes ?? [])
                    .filter(
                      (n: Row) => n.id !== trace.id && n.kind === trace.kind,
                    )
                    .map((n: Row) => (
                      <option key={n.id} value={n.id}>
                        {n.name}
                      </option>
                    ))}
                </select>
              </label>
              <button
                className="btn"
                disabled={!mergeTarget || busy}
                onClick={() =>
                  void perform(async () => {
                    const r = await send(base + "/entities/merge-proposals", {
                      source_id: trace.id,
                      target_id: mergeTarget,
                    });
                    setTrace(r);
                    return r;
                  }, "请比较两实体来源并填写归并理由")
                }
              >
                比较归并候选
              </button>
            </div>
          )}
          {trace.source && trace.target && (
            <div className="knowledge-form vertical">
              <label>
                核对人
                <input
                  value={reviewer}
                  onChange={(e) => setReviewer(e.target.value)}
                />
              </label>
              <label>
                归并理由
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                />
              </label>
              <button
                className="btn primary"
                disabled={busy || !reviewer || !note}
                onClick={() =>
                  void perform(async () => {
                    const r = await send(base + "/entities/merge-proposals", {
                      apply: true,
                      source_id: trace.source.id,
                      target_id: trace.target.id,
                      expected_source_version: trace.expected_source_version,
                      expected_target_version: trace.expected_target_version,
                      reviewer,
                      note,
                    });
                    setTrace(r);
                    return r;
                  }, "归并决策已保存，相关知识进入复查")
                }
              >
                保存归并决策
              </button>
            </div>
          )}
          {trace.releases?.map((r: Row) => (
            <button
              className="btn"
              key={r.id}
              onClick={() =>
                void perform(
                  () => send(base + `/releases/${r.id}/activate`),
                  "已切换知识发布指针，未改写论文正文",
                )
              }
            >
              切换到 {r.created_at}
            </button>
          ))}
        </WorkspaceDialog>
      )}
      {caseEdit && (
        <WorkspaceDialog
          title="评测问题与人工标准"
          onClose={() => setCaseEdit(null)}
        >
          <div className="knowledge-form vertical">
            <label>
              可接受的项目证据编号（项目实现任务可填，逗号分隔）
              <textarea
                value={(caseEdit.gold.evidence_ids ?? []).join(",")}
                onChange={(e) =>
                  setCaseEdit({
                    ...caseEdit,
                    gold: {
                      ...caseEdit.gold,
                      evidence_ids: e.target.value
                        .split(",")
                        .map((x) => x.trim())
                        .filter(Boolean),
                    },
                  })
                }
              />
            </label>
            <label>
              真实问题
              <textarea
                rows={4}
                value={caseEdit.question}
                onChange={(e) =>
                  setCaseEdit({ ...caseEdit, question: e.target.value })
                }
              />
            </label>
            <label>
              可接受的原文单元编号（逗号分隔）
              <textarea
                value={(caseEdit.gold.unit_ids ?? []).join(",")}
                onChange={(e) =>
                  setCaseEdit({
                    ...caseEdit,
                    gold: {
                      ...caseEdit.gold,
                      unit_ids: e.target.value
                        .split(",")
                        .map((x) => x.trim())
                        .filter(Boolean),
                    },
                  })
                }
              />
            </label>
            <label>
              核对人
              <input
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
              />
            </label>
            <label className="knowledge-check">
              <input
                type="checkbox"
                checked={!!caseEdit.real_task_confirmed}
                onChange={(e) =>
                  setCaseEdit({
                    ...caseEdit,
                    real_task_confirmed: e.target.checked,
                  })
                }
              />
              我已将草稿改写为具体的真实写作任务
            </label>
            <label className="knowledge-check">
              <input
                type="checkbox"
                checked={!!caseEdit.compared_original}
                onChange={(e) =>
                  setCaseEdit({
                    ...caseEdit,
                    compared_original: e.target.checked,
                  })
                }
              />
              我已实际对照原件，核对可接受证据、条件及不能推出的结论
            </label>
            <button
              className="btn primary"
              disabled={
                busy ||
                !reviewer ||
                !caseEdit.compared_original ||
                !caseEdit.real_task_confirmed ||
                !(
                  caseEdit.gold.unit_ids?.length ||
                  caseEdit.gold.evidence_ids?.length
                )
              }
              onClick={() =>
                void perform(async () => {
                  const r = await send(
                    base + "/evaluation/cases/" + caseEdit.id,
                    {
                      question: caseEdit.question,
                      gold: caseEdit.gold,
                      state: "labelled",
                      reviewer,
                      compared_original: caseEdit.compared_original,
                      real_task_confirmed: caseEdit.real_task_confirmed,
                    },
                    "PUT",
                  );
                  setCases(cases.map((c) => (c.id === r.id ? r : c)));
                  setCaseEdit(null);
                  return r;
                }, "人工标准已保存")
              }
            >
              保存人工标准
            </button>
          </div>
        </WorkspaceDialog>
      )}
      {semanticReview && (
        <WorkspaceDialog
          title="人工语义评估"
          onClose={() => setSemanticReview(null)}
        >
          <div className="knowledge-form vertical">
            {Object.entries({
              supported_claims: "获得支持的论断数",
              checkable_claims: "需要核验的论断总数",
              qualifiers_preserved: "保留的限定条件数",
              qualifiers_required: "应保留的限定条件总数",
              wrong_citations: "错误引用数",
              unsupported_expansions: "无依据扩写数",
              comparisons_valid: "有效比较数",
              comparisons_required: "应核验比较总数",
              contradictions_identified: "正确识别矛盾数",
              contradictions_required: "应识别矛盾总数",
              user_revision_count: "用户修订次数",
              completion_seconds: "实际完成秒数",
            }).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min="0"
                  value={semanticReview[key] ?? 0}
                  onChange={(e) =>
                    setSemanticReview({
                      ...semanticReview,
                      [key]: Number(e.target.value),
                    })
                  }
                />
              </label>
            ))}
            <label>
              核对人
              <input
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
              />
            </label>
            <label>
              判断理由
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <label className="knowledge-check">
              <input
                type="checkbox"
                checked={!!semanticReview.compared_original}
                onChange={(e) =>
                  setSemanticReview({
                    ...semanticReview,
                    compared_original: e.target.checked,
                  })
                }
              />
              我已逐句对照原件；本次评审人与金标准标注人不同，且尚未查看组别映射
            </label>
            <button
              className="btn primary"
              disabled={
                busy || !reviewer || !note || !semanticReview.compared_original
              }
              onClick={() =>
                void perform(async () => {
                  const r = await send(
                    base + `/evaluation/comparisons/${comparison!.id}/reviews`,
                    { ...semanticReview, reviewer, note },
                  );
                  setSemanticReview(null);
                  return r;
                }, "人工语义记录已保存")
              }
            >
              保存本组评估
            </button>
          </div>
        </WorkspaceDialog>
      )}
    </section>
  );
}

function GraphView({
  value,
  onNode,
}: {
  value: Row;
  onNode: (id: string) => void;
}) {
  const nodes: Row[] = (value.nodes ?? []).slice(0, 18);
  const positions = new Map(
    nodes.map((n, i) => [
      n.id,
      {
        x: 320 + 245 * Math.cos((2 * Math.PI * i) / Math.max(1, nodes.length)),
        y: 160 + 120 * Math.sin((2 * Math.PI * i) / Math.max(1, nodes.length)),
      },
    ]),
  );
  return (
    <svg
      className="knowledge-graph"
      viewBox="0 0 640 320"
      role="img"
      aria-label="当前问题的局部知识关系"
    >
      {(value.edges ?? []).map((e: Row) => {
        const a = positions.get(e.source),
          b = positions.get(e.target);
        return a && b ? (
          <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
        ) : null;
      })}
      {nodes.map((n) => {
        const p = positions.get(n.id)!;
        return (
          <g
            key={n.id}
            transform={`translate(${p.x},${p.y})`}
            onClick={() => onNode(n.id)}
            tabIndex={0}
            role="button"
            aria-label={"查看实体 " + n.name}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") onNode(n.id);
            }}
          >
            <title>{n.name}</title>
            <circle r="9" />
            <text y="25" textAnchor="middle">
              {n.name.length > 6 ? n.name.slice(0, 6) + "…" : n.name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

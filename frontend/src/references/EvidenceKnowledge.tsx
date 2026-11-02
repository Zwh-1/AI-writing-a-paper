import { useEffect, useState } from "react";
import { apiUrl, api, send } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import type { Row } from "../workspace/types";
import "./evidenceKnowledge.css";
import {
  ReferenceSurface,
  ReferenceInspector,
  ResearchColumns,
  ResearchEmpty,
  useReferenceTitle,
} from "./ReferenceWorkbenchParts";

const label: Record<string, string> = {
  pending: "待核对",
  reviewed: "原文已核对",
  excluded: "已排除",
  stale: "已过期",
  disputed: "有争议",
  boundary_inferred: "段落边界由解析器推断",
  possible_continuation: "可能存在跨段或跨页续接",
  table_header_and_notes_need_review: "需核对表头、数据与表注",
  replacement_characters: "存在无法识别的字符",
  transcript_needs_original_comparison: "识别文字需要对照原件",
  manual_transcription_needs_review: "人工修订仍待核对",
  reading_order_needs_review: "需核对原页阅读顺序",
  teaching_scope_needs_review: "需保留教学主题、正反例与适用范围",
  supported: "支持",
  partial: "部分支持",
  contradicted: "矛盾",
  unsupported: "不支持",
  unknown: "待判断",
};
const location = (unit: Row) =>
  unit.locator?.physical_page
    ? `原件第 ${unit.locator.page} 页`
    : unit.locator?.paragraph
      ? `文档第 ${unit.locator.paragraph} 个块`
      : `资料块 ${unit.locator?.page ?? ""}`;

export default function EvidenceKnowledgePanel({
  pid,
  tick,
  onSource,
  onImported,
  initialFileId,
  onFileChange,
}: {
  pid: string;
  tick: number;
  onSource: (file: Row, page: number) => void;
  onImported?: () => void;
  initialFileId?: string;
  onFileChange?: (id: string) => void;
}) {
  const titleOf = useReferenceTitle();
  const base = `/projects/${pid}/evidence-knowledge`;
  const [refresh, setRefresh] = useState(0),
    [state, setState] = useState("all"),
    [fileId, setFileId] = useState(initialFileId || ""),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0);
  const overview = useWorkspaceResource<Row>(base, tick + refresh);
  useEffect(() => {
    setFileId(initialFileId || "");
    setOffset(0);
    setSearchResult(null);
    setSelected(null);
  }, [initialFileId]);
  const units = useWorkspaceResource<Row>(
    `${base}/units?state=${state}&file_id=${fileId}&offset=${offset}&query=${encodeURIComponent(query)}`,
    tick + refresh,
  );
  const sections = useWorkspaceResource<Row[]>(
    `/projects/${pid}/sections`,
    tick,
  );
  const citations = useWorkspaceResource<Row[]>(
    `${base}/citations`,
    tick + refresh,
  );
  const versions = useWorkspaceResource<Row[]>(
    fileId ? `${base}/documents/${fileId}/versions` : "",
    tick + refresh,
  );
  const [selected, setSelected] = useState<Row | null>(null),
    [dialog, setDialog] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [strategy, setStrategy] = useState("paragraphs"),
    [job, setJob] = useState<Row | null>(null),
    [searchResult, setSearchResult] = useState<Row | null>(null),
    [collection, setCollection] = useState("all");
  const [sid, setSid] = useState(""),
    [selection, setSelection] = useState<string[]>([]),
    [selectionLoading, setSelectionLoading] = useState(false);
  const [citationReview, setCitationReview] = useState<Row | null>(null);
  const [comparison, setComparison] = useState<Row | null>(null);
  const reload = () => setRefresh((v) => v + 1);
  const run = async (action: () => Promise<unknown>, message = "已保存") => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      reload();
      setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    setOffset(0);
  }, [state, fileId, query]);
  useEffect(() => {
    if (overview.data?.task && !job) setJob(overview.data.task);
  }, [overview.data?.task, job]);
  useEffect(() => {
    setSelected(null);
    setSearchResult(null);
    setJob(null);
    setSid("");
    setSelection([]);
  }, [pid]);
  useEffect(() => {
    if (!sid) {
      setSelection([]);
      setSelectionLoading(false);
      return;
    }
    const controller = new AbortController();
    setSelectionLoading(true);
    void api(`${base}/sections/${sid}/selection`, { signal: controller.signal })
      .then((r) => {
        if (!controller.signal.aborted) setSelection(r.unit_ids);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setSelectionLoading(false);
      });
    return () => controller.abort();
  }, [base, sid]);
  useEffect(() => {
    if (!job?.id || !["queued", "running"].includes(job.status)) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api(`/tasks/${job.id}`)
        .then((next) => {
          if (!cancelled) {
            setJob(next);
            if (!["queued", "running"].includes(next.status)) reload();
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e.message);
        });
    }, 1800);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.status]);
  const open = (uid: string) =>
    run(async () => {
      setSelected(await api(`${base}/units/${uid}`));
    }, "已读取原文单元");
  const build = (fid = fileId) =>
    run(async () => {
      setJob(
        await send(`${base}/build`, { file_id: fid || undefined, strategy }),
      );
    }, "结构解析已排队，在本机处理");
  const results = searchResult?.items ?? units.data?.items ?? [];
  const files: Row[] = overview.data?.files ?? [];
  return (
    <section className="ek-panel evidence-workbench" aria-label="证据知识库">
      <div className="ek-heading">
        <div>
          <h2>原文与证据核对</h2>
          <p>定位原文 · 阅读上下文 · 关联章节证据</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => setDialog("import")}>
            批量导入原件
          </button>
          <button className="btn" onClick={() => setDialog("evaluation")}>
            对照评测
          </button>
        </div>
      </div>
      {(error || overview.error || units.error) && (
        <p role="alert" className="error-text">
          {error || overview.error || units.error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <ResearchColumns
        directory={
          <>
            <div className="research-directory-heading">
              <h2>证据目录</h2>
            </div>
            <div className="ek-toolbar">
              <label>
                原件
                <select
                  aria-label="证据原件筛选"
                  value={fileId}
                  onChange={(e) => {
                    setFileId(e.target.value);
                    onFileChange?.(e.target.value);
                    setSearchResult(null);
                  }}
                >
                  <option value="">全部研究文献</option>
                  {files.map((f) => (
                    <option key={f.id} value={f.id}>
                      {titleOf(f)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <form
              className="ek-toolbar ek-search-form"
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  setSearchResult(
                    await send(`${base}/search`, {
                      query,
                      collection,
                      file_ids: fileId ? [fileId] : undefined,
                      limit: 20,
                    }),
                  );
                }, "检索完成；结果仅用于定位原文");
              }}
            >
              <label className="ek-grow">
                检索问题
                <input
                  aria-label="检索证据知识库"
                  value={query}
                  maxLength={5000}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setSearchResult(null);
                  }}
                  placeholder="输入具体问题或原文词语"
                />
              </label>
              <button className="btn" disabled={busy || !query.trim()}>
                检索原文
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setQuery("");
                  setSearchResult(null);
                }}
              >
                清除检索
              </button>
            </form>
            <details className="research-filters">
              <summary>更多筛选</summary>{" "}
              <label>
                核对状态
                <select
                  value={state}
                  onChange={(e) => {
                    setState(e.target.value);
                    setSearchResult(null);
                  }}
                >
                  <option value="all">全部</option>
                  {["pending", "reviewed", "disputed", "excluded", "stale"].map(
                    (s) => (
                      <option key={s} value={s}>
                        {label[s]}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                检索范围
                <select
                  value={collection}
                  onChange={(e) => {
                    setCollection(e.target.value);
                    setSearchResult(null);
                  }}
                >
                  <option value="all">所有可用原文</option>
                  <option value="reviewed">仅已核对且无争议</option>
                </select>
              </label>
            </details>
            {searchResult && (
              <div role="status">
                <p>
                  {searchResult.unit_pipeline?.rerank?.status === "completed"
                    ? "已完成相关性重排"
                    : "重排未执行或未完成，当前显示全文与融合排序"}
                  ；排序分数不代表论断正确。
                </p>
                {searchResult.warnings?.map((warning: string) => (
                  <p className="muted" key={warning}>
                    {warning}
                  </p>
                ))}
              </div>
            )}
            <div className="research-directory-heading">
              <h3>原文单元</h3>
              <span>
                {searchResult ? results.length : (units.data?.total ?? 0)} 项
              </span>
            </div>
            <div className="ek-unit-list" aria-label="证据单元列表">
              {results.map((row: Row) => {
                const uid =
                  row.knowledge_unit_id || (row.parse_id ? row.id : null);
                return (
                  <article
                    key={row.id}
                    className={`ek-unit ${uid === selected?.id ? "is-selected" : ""}`}
                  >
                    <button
                      className="research-directory-item"
                      disabled={!uid || busy}
                      aria-pressed={uid === selected?.id}
                      onClick={() => uid && void open(uid)}
                      aria-label={`读取与核对原文：${titleOf(row)} · ${location(row)}`}
                    >
                      <small>
                        {location(row)} · {label[row.review_state] || "待核对"}
                      </small>
                      <strong>{titleOf(row)}</strong>
                      <p className="ek-directory-excerpt">
                        {row.text?.slice(0, 130)}
                      </p>
                    </button>
                    {sid && uid && (
                      <label className="ek-directory-selection">
                        <input
                          type="checkbox"
                          checked={selection.includes(uid)}
                          disabled={selectionLoading || busy}
                          onChange={(e) => {
                            const checked = e.currentTarget.checked;
                            setSelection((ids) =>
                              checked
                                ? [...new Set([...ids, uid])]
                                : ids.filter((id) => id !== uid),
                            );
                          }}
                        />
                        关联当前章节
                      </label>
                    )}
                  </article>
                );
              })}
            </div>
            {!results.length && !units.loading && (
              <p className="research-directory-empty">
                暂无匹配单元，试试其他关键词或原件。
              </p>
            )}
            {!searchResult && (
              <div className="ek-pagination">
                <span>共 {units.data?.total ?? 0} 个单元</span>
                <button
                  className="btn"
                  disabled={offset === 0}
                  onClick={() => setOffset((v) => Math.max(0, v - 20))}
                >
                  上一页
                </button>
                <button
                  className="btn"
                  disabled={offset + 20 >= (units.data?.total ?? 0)}
                  onClick={() => setOffset((v) => v + 20)}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        }
        inspector={
          <>
            <ReferenceInspector title="当前证据">
              {selected ? (
                <>
                  <p>{titleOf(selected)}</p>
                  <dl className="reference-metadata">
                    <div>
                      <dt>原文位置</dt>
                      <dd>{location(selected)}</dd>
                    </div>
                    <div>
                      <dt>核对状态</dt>
                      <dd>{label[selected.review_state]}</dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p>选择左侧单元后，在中间读取原文和上下文。</p>
              )}
            </ReferenceInspector>
            <ReferenceInspector title="章节证据">
              {" "}
              <details className="ek-selection">
                <summary>为章节选择证据</summary>
                <label>
                  章节
                  <select
                    value={sid}
                    onChange={(e) => {
                      setSelectionLoading(!!e.target.value);
                      setSid(e.target.value);
                    }}
                  >
                    <option value="">请选择章节</option>
                    {sections.data
                      ?.filter((s) => s.state !== "archived")
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.title}
                        </option>
                      ))}
                  </select>
                </label>
                <p>
                  {selectionLoading
                    ? "正在读取章节证据…"
                    : `已选 ${selection.length} 个单元。关联证据不会自动将其标为支持正文。`}
                </p>
                <button
                  className="btn"
                  disabled={!sid || busy || selectionLoading}
                  onClick={() =>
                    void run(
                      () =>
                        send(
                          `${base}/sections/${sid}/selection`,
                          { unit_ids: selection },
                          "PUT",
                        ),
                      "章节证据已保存",
                    )
                  }
                >
                  保存章节证据
                </button>
              </details>
            </ReferenceInspector>
            <ReferenceInspector title="解析与分段">
              <details className="research-advanced">
                <summary>解析与分段工具</summary>{" "}
                <label>
                  分段方案
                  <select
                    value={strategy}
                    onChange={(e) => setStrategy(e.target.value)}
                  >
                    <option value="paragraphs">保留段落候选</option>
                    <option value="page">保留整页上下文</option>
                  </select>
                </label>
                <button
                  className="btn"
                  disabled={
                    busy ||
                    (!!job && ["queued", "running"].includes(job.status))
                  }
                  onClick={() => void build()}
                >
                  构建结构化版本
                </button>
              </details>{" "}
              {job && (
                <div role="status" className="ek-job">
                  {job.phase || "结构解析任务"} · {job.status} ·{" "}
                  {job.progress ?? 0}%{job.error && <p>{job.error}</p>}
                  {(() => {
                    const r =
                      typeof job.result === "string"
                        ? JSON.parse(job.result || "{}")
                        : job.result;
                    return r?.failures?.map((f: Row) => (
                      <p key={f.file_id}>
                        {titleOf(f)}：{f.error}{" "}
                        <button
                          className="btn"
                          onClick={() => void build(f.file_id)}
                        >
                          重试该文件
                        </button>
                      </p>
                    ));
                  })()}
                </div>
              )}
              {files.some((f) => f.status === "failed") && (
                <details>
                  <summary>解析失败的原件</summary>
                  {files
                    .filter((f) => f.status === "failed")
                    .map((f) => (
                      <p key={f.id}>
                        {titleOf(f)}{" "}
                        <button
                          className="btn"
                          onClick={() =>
                            void run(async () => {
                              setJob(await send(`/files/${f.id}/reparse`));
                            }, "解析重试已排队")
                          }
                        >
                          重新解析
                        </button>
                      </p>
                    ))}
                </details>
              )}
              {fileId && (
                <details className="ek-versions">
                  <summary>
                    解析与分段版本（{versions.data?.length ?? 0}）
                  </summary>
                  {versions.data?.map((v) => (
                    <div className="ek-version" key={v.id}>
                      <span>
                        {v.engine.startsWith("manual:")
                          ? "人工修订"
                          : v.engine === "legacy"
                            ? "旧版解析"
                            : "本地结构解析"}{" "}
                        · {v.strategy === "page" ? "整页" : "段落"} ·{" "}
                        {new Date(v.created_at).toLocaleString()} ·{" "}
                        {v.id === v.active_parse_id ? "当前使用" : "历史候选"}{" "}
                        {v.current_input ? "" : "原件内容已变化"}
                      </span>
                      <button
                        className="btn"
                        disabled={
                          busy || !v.current_input || v.id === v.active_parse_id
                        }
                        onClick={() =>
                          void run(
                            () => send(`${base}/versions/${v.id}/activate`),
                            "已切换版本，相关引用需要复查",
                          )
                        }
                      >
                        使用此版本
                      </button>
                      <button
                        className="btn"
                        disabled={busy || !v.active_parse_id}
                        onClick={() =>
                          void run(async () => {
                            setComparison(
                              await api(
                                `${base}/versions/${v.id}/compare?other=${v.active_parse_id}`,
                              ),
                            );
                          }, "已读取两个版本，未自动选择语义正确的分段")
                        }
                      >
                        比较当前版本
                      </button>
                    </div>
                  ))}
                </details>
              )}
            </ReferenceInspector>
            <ReferenceInspector title="核对进度">
              <details>
                <summary>查看统计与索引状态</summary>{" "}
                <div className="ek-metrics" aria-label="证据核对统计">
                  {[
                    ["total", "原文单元"],
                    ["pending", "待核对"],
                    ["reviewed", "原文已核对"],
                    ["disputed", "有争议"],
                    ["stale", "已过期"],
                  ].map(([key, text]) => (
                    <span key={key}>
                      {text} <strong>{overview.data?.[key] ?? "—"}</strong>
                    </span>
                  ))}
                  <span>
                    本地向量定位窗口{" "}
                    <strong>
                      {overview.data?.indexed_windows ?? "—"} /{" "}
                      {overview.data?.search_windows ?? "—"}
                    </strong>
                  </span>
                </div>
              </details>
            </ReferenceInspector>
            <ReferenceInspector title="正文引用">
              {" "}
              <details className="ek-citations">
                <summary>
                  正文引用与原文对应（{citations.data?.length ?? 0}）
                </summary>
                {citations.data?.map((c) => (
                  <article key={c.id}>
                    <h3>
                      {c.section_title} · {c.reference_snapshot?.title}
                    </h3>
                    <p>{c.claim}</p>
                    <p>
                      {c.effective_support === "stale"
                        ? "来源已过期或存在争议，需要重新核对"
                        : c.support === "unknown"
                          ? "论断支持性待核对"
                          : c.support.startsWith("model_")
                            ? `模型辅助判定：${label[c.support.slice(6)] || c.support}，待人工核对`
                            : label[c.support] || c.support}
                    </p>
                    {c.units.map((u: Row) => (
                      <button
                        className="btn"
                        key={u.id}
                        onClick={() => void open(u.id)}
                      >
                        {location(u)} · 查看原文与适用条件
                        {u.current ? "" : " · 已过期"}
                      </button>
                    ))}
                    {c.document_readings?.map((r: Row) => (
                      <details key={r.reading_id}>
                        <summary>
                          采用时的阅读问题与范围
                          {r.current ? "" : "（已变化，需重新核对）"}
                        </summary>
                        <p>{r.read_scope.question}</p>
                        <p>已读文字不自动证明当前论断。</p>
                        {r.read_scope.passages.map((p: Row) => (
                          <pre className="ek-preview" key={p.id}>
                            {p.text}
                          </pre>
                        ))}
                      </details>
                    ))}
                    {!c.units.length && !c.document_readings?.length && (
                      <p>
                        旧版引用关联 {c.source_ids.length}{" "}
                        个原文片段，可在文献库查看。
                      </p>
                    )}
                    <button
                      className="btn"
                      onClick={() =>
                        void run(async () => {
                          setJob(await send(`${base}/citations/${c.id}/check`));
                        }, "引用核验已排队，将使用已配置的模型并计入预算")
                      }
                    >
                      AI辅助核验引用
                    </button>
                    {(c.units.length > 0 ||
                      c.document_readings?.length > 0) && (
                      <button
                        className="btn"
                        disabled={c.effective_support === "stale"}
                        onClick={() => setCitationReview(c)}
                      >
                        人工核对这条引用
                      </button>
                    )}
                  </article>
                ))}
              </details>
            </ReferenceInspector>
          </>
        }
      >
        {selected && (
          <UnitDialog
            key={selected.id}
            pid={pid}
            item={selected}
            embedded
            onClose={() => setSelected(null)}
            onChanged={(next) => {
              setSelected(next);
              reload();
            }}
            onSource={(f, page) => {
              setSelected(null);
              onSource(f, page);
            }}
          />
        )}
        {!selected && (
          <ResearchEmpty title="选择一段原文，开始核对">
            在左侧按原件和关键词定位内容。选中单元后，可在这里查看原页、完整文字及相邻上下文，并保存核对记录。
          </ResearchEmpty>
        )}
      </ResearchColumns>
      {citationReview && (
        <CitationReviewDialog
          pid={pid}
          citation={citationReview}
          onClose={() => setCitationReview(null)}
          onDone={() => {
            setCitationReview(null);
            reload();
          }}
        />
      )}
      {comparison && (
        <WorkspaceDialog
          title="分段版本对照"
          wide
          onClose={() => setComparison(null)}
        >
          <p>
            左侧为所选版本，右侧为当前版本。这里展示原文与边界差异，不自动裁定含义。
          </p>
          <div className="ek-review-grid">
            {[comparison.left, comparison.right].map((v: Row, index) => (
              <section key={index}>
                <h3>
                  {index === 0 ? "所选版本" : "当前版本"} ·{" "}
                  {v.strategy === "page" ? "整页" : "段落"}
                </h3>
                {v.units.map((u: Row) => (
                  <article className="ek-unit" key={u.id}>
                    <p>
                      {location(u)} · {u.locator.char_start}—
                      {u.locator.char_end}
                    </p>
                    <p className="ek-preview">{u.preview}</p>
                    <button
                      className="btn"
                      onClick={() => {
                        setComparison(null);
                        void open(u.id);
                      }}
                    >
                      读取该版本原文
                    </button>
                  </article>
                ))}
              </section>
            ))}
          </div>
        </WorkspaceDialog>
      )}
      {dialog === "import" && (
        <ImportDialog
          pid={pid}
          onClose={() => setDialog("")}
          onDone={() => {
            setDialog("");
            reload();
            onImported?.();
          }}
        />
      )}
      {dialog === "evaluation" && (
        <EvaluationDialog pid={pid} onClose={() => setDialog("")} />
      )}
    </section>
  );
}

function UnitDialog({
  pid,
  item,
  onClose,
  onChanged,
  onSource,
  embedded = false,
}: {
  pid: string;
  item: Row;
  onClose: () => void;
  onChanged: (r: Row) => void;
  onSource: (f: Row, p: number) => void;
  embedded?: boolean;
}) {
  const titleOf = useReferenceTitle();
  const [stage, setStage] = useState<"text" | "review">("text");
  const base = `/projects/${pid}/evidence-knowledge`,
    path = `${base}/units/${item.id}`;
  const [text, setText] = useState(item.text),
    [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState(""),
    [compared, setCompared] = useState(false),
    [ack, setAck] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [context, setContext] = useState<Row | null>(null),
    [before, setBefore] = useState(1),
    [after, setAfter] = useState(1),
    [kind, setKind] = useState("boundary"),
    [claim, setClaim] = useState(""),
    [quote, setQuote] = useState(""),
    [conditions, setConditions] = useState(""),
    [support, setSupport] = useState("unknown");
  const [rangeStart, setRangeStart] = useState(0),
    [rangeEnd, setRangeEnd] = useState(Math.min(1000, item.text.length));
  const read = (partial = false) =>
    send(`${path}/read`, {
      before,
      after,
      ...(partial ? { start: rangeStart, end: rangeEnd } : {}),
    }).then(setContext);
  useEffect(() => {
    let active = true;
    void send(`${path}/read`, { before: 1, after: 1 })
      .then((r) => {
        if (active) setContext(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [path]);
  const action = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const identity = { reviewer, note, expected_hash: item.content_hash };
  const update = (route: string, body: Row) =>
    action(async () =>
      onChanged(await send(path + route, { ...identity, ...body })),
    );
  return (
    <ReferenceSurface
      title="原文与证据核对"
      onClose={onClose}
      embedded={embedded}
      className="ek-inline-review"
      busy={busy}
    >
      <h3 className="reading-current-title">{titleOf(item)}</h3>
      <p className="reference-filename">
        {location(item)} · {label[item.review_state]}
        {item.current ? "" : " · 该版本已过期"}
      </p>
      <p className="muted">
        原文核对只确认文字和位置；是否支持某个论断需另外判断。
      </p>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div
        className="research-view-switch"
        role="group"
        aria-label="证据核对步骤"
      >
        <button
          className="btn"
          aria-pressed={stage === "text"}
          onClick={() => setStage("text")}
        >
          1. 原文与上下文
        </button>
        <button
          className="btn"
          aria-pressed={stage === "review"}
          onClick={() => setStage("review")}
        >
          2. 核对与修订
        </button>
      </div>
      <div className="ek-review-grid" hidden={stage !== "text"}>
        <section>
          <h3>原始文献</h3>
          {item.extension === ".pdf" && item.page_id ? (
            <img
              className="ek-original"
              alt={`${item.name}原件第${item.locator.page}页`}
              src={apiUrl(`/pages/${item.page_id}/preview`)}
            />
          ) : (
            <p>该格式没有固定PDF页码，请打开原件核对段落、表格与阅读顺序。</p>
          )}
          <div className="actions">
            <a className="btn" href={apiUrl(`/files/${item.file_id}/download`)}>
              下载原件
            </a>
            <button
              className="btn"
              onClick={() =>
                onSource(
                  {
                    id: item.file_id,
                    name: item.name,
                    extension: item.extension,
                  },
                  item.locator.page || 1,
                )
              }
            >
              打开原文查看器
            </button>
          </div>
        </section>
        <section>
          <h3>当前原文单元</h3>
          <textarea
            aria-label="原文单元转录"
            className="ek-transcript"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <h3>相邻上下文</h3>
          <div className="ek-toolbar">
            <label>
              前方单元
              <input
                type="number"
                min={0}
                max={3}
                value={before}
                onChange={(e) => setBefore(Number(e.target.value))}
              />
            </label>
            <label>
              后方单元
              <input
                type="number"
                min={0}
                max={3}
                value={after}
                onChange={(e) => setAfter(Number(e.target.value))}
              />
            </label>
            <button className="btn" onClick={() => void action(() => read())}>
              读取上下文
            </button>
          </div>
          {context?.status === "needs_narrower_range" ? (
            <div>
              <p role="status">
                所需原文超过读取预算，当前没有提供截断文本。请缩小范围或分步读取。
              </p>
              <div className="ek-toolbar">
                <label>
                  起始字符
                  <input
                    type="number"
                    min={0}
                    value={rangeStart}
                    onChange={(e) => setRangeStart(Number(e.target.value))}
                  />
                </label>
                <label>
                  结束字符
                  <input
                    type="number"
                    min={1}
                    max={item.text.length}
                    value={rangeEnd}
                    onChange={(e) => setRangeEnd(Number(e.target.value))}
                  />
                </label>
                <button
                  className="btn"
                  onClick={() => void action(() => read(true))}
                >
                  读取明确区间
                </button>
              </div>
            </div>
          ) : (
            <div className="ek-context">
              {context && !context.complete && (
                <p>当前只读取了明确区间，不能视为完整语义单元。</p>
              )}
              {context?.passages?.map((p: Row) => (
                <div key={p.id}>
                  <small>
                    {p.id === item.id ? "当前单元" : "相邻单元"} ·{" "}
                    {p.locator?.physical_page
                      ? `原件第${p.locator.page}页`
                      : "文档位置"}
                  </small>
                  <pre>{p.text}</pre>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      {stage === "text" && (
        <button className="btn primary" onClick={() => setStage("review")}>
          继续核对与修订
        </button>
      )}
      <div hidden={stage !== "review"}>
        <section className="ek-review-form">
          <h3>核对记录</h3>
          <label>
            核对人
            <input
              aria-label="核对人"
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              maxLength={200}
            />
          </label>
          <label>
            核对依据与处理说明
            <textarea
              aria-label="核对说明"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={6000}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={compared}
              onChange={(e) => setCompared(e.target.checked)}
            />
            已对照原始文件及必要上下文
          </label>
          <fieldset>
            <legend>需逐项处理的问题</legend>
            {item.issues.map((issue: string) => (
              <label key={issue}>
                <input
                  type="checkbox"
                  checked={ack.includes(issue)}
                  onChange={(e) =>
                    setAck((v) =>
                      e.target.checked
                        ? [...v, issue]
                        : v.filter((x) => x !== issue),
                    )
                  }
                />
                {label[issue] || issue}
              </label>
            ))}
          </fieldset>
          <div className="actions">
            <button
              className="btn primary"
              disabled={
                busy ||
                !item.current ||
                !compared ||
                ack.length !== item.issues.length ||
                !note.trim() ||
                !reviewer.trim()
              }
              onClick={() =>
                void update("/review", {
                  state: "reviewed",
                  compared_original: compared,
                  acknowledged_issues: ack,
                })
              }
            >
              确认原文已核对
            </button>
            <button
              className="btn"
              disabled={busy || !item.current}
              onClick={() => void update("/review", { state: "pending" })}
            >
              设为待核对
            </button>
            <button
              className="btn"
              disabled={busy || !item.current}
              onClick={() => void update("/review", { state: "excluded" })}
            >
              排除此单元
            </button>
            <button
              className="btn"
              disabled={busy || !item.current || text === item.text}
              onClick={() => void update("/revise", { text })}
            >
              将修订另存为新版本
            </button>
            <button
              className="btn"
              disabled={busy || !item.current || context?.status !== "read"}
              onClick={() =>
                void update("/revise", {
                  boundary: true,
                  before,
                  after,
                  ...(!context?.complete
                    ? { start: rangeStart, end: rangeEnd }
                    : {}),
                })
              }
            >
              将当前阅读范围另存为边界版本
            </button>
          </div>
        </section>
        <details open={item.disputes.some((d: Row) => d.state === "open")}>
          <summary>段落与含义争议（{item.disputes.length}）</summary>
          {item.disputes.map((d: Row) => (
            <article key={d.id}>
              <p>
                {d.note} · {d.state === "open" ? "待处理" : "已记录处理"}
              </p>
              {d.resolution && <p>{d.resolution}</p>}
              {d.state === "open" && (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void update(`/disputes/${d.id}/resolve`, {})}
                >
                  用当前核对说明记录处理
                </button>
              )}
            </article>
          ))}
          <label>
            争议类别
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {[
                ["boundary", "段落边界"],
                ["ocr", "文字识别"],
                ["reading_order", "阅读顺序"],
                ["table", "表格关系"],
                ["interpretation", "含义解释"],
                ["other", "其他"],
              ].map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn"
            disabled={busy}
            onClick={() => void update("/disputes", { kind })}
          >
            按当前说明登记争议
          </button>
        </details>
        <details>
          <summary>具体论断的支持性核对</summary>
          <label>
            待核对论断
            <textarea
              aria-label="待核对论断"
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
            />
          </label>
          <label>
            逐字原文引句
            <textarea
              aria-label="逐字原文引句"
              value={quote}
              onChange={(e) => setQuote(e.target.value)}
            />
          </label>
          <label>
            适用条件（每行一条）
            <textarea
              value={conditions}
              onChange={(e) => setConditions(e.target.value)}
            />
          </label>
          <label>
            支持性
            <select
              aria-label="支持性"
              value={support}
              onChange={(e) => setSupport(e.target.value)}
            >
              {[
                "unknown",
                "supported",
                "partial",
                "contradicted",
                "unsupported",
              ].map((s) => (
                <option key={s} value={s}>
                  {label[s]}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn"
            disabled={busy || !claim.trim() || !quote.trim()}
            onClick={() =>
              void action(async () => {
                await send(path + "/claims", {
                  ...identity,
                  claim,
                  quote,
                  conditions: conditions.split("\n").filter(Boolean),
                  support,
                });
                onChanged(await api(path));
              })
            }
          >
            保存该论断的人工判断
          </button>
          {item.claim_checks.map((c: Row) => (
            <blockquote key={c.id}>
              <p>
                {c.claim} · {label[c.effective_support] || label[c.support]} ·{" "}
                {c.reviewer}
              </p>
              <p>{c.quote}</p>
            </blockquote>
          ))}
        </details>
      </div>
      <details className="research-advanced">
        <summary>来源与版本信息</summary>
        <p>
          解析来源：{item.origin}；原件哈希：{item.file_hash}；解析版本：
          {item.parse_id}
        </p>
        <pre>{JSON.stringify(item.locator, null, 2)}</pre>
      </details>
    </ReferenceSurface>
  );
}

function CitationReviewDialog({
  pid,
  citation,
  onClose,
  onDone,
}: {
  pid: string;
  citation: Row;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState(""),
    [support, setSupport] = useState("unknown"),
    [quotes, setQuotes] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <WorkspaceDialog
      title="人工核对正文引用"
      onClose={onClose}
      wide
      busy={busy}
    >
      <p>正文论断：{citation.claim}</p>
      <p>
        逐项对照关联原文，并说明限定条件。这里只记录当前正文版本的这条引用。
      </p>
      {error && <p role="alert">{error}</p>}
      {citation.document_readings?.map((r: Row) => (
        <section key={r.reading_id}>
          <h3>采用时的阅读问题：{r.read_scope.question}</h3>
          {r.read_scope.passages.map((p: Row) => (
            <pre className="ek-preview" key={p.id}>
              {p.text}
            </pre>
          ))}
          <label>
            已读范围的逐字引句
            <textarea
              aria-label={`阅读记录${r.reading_id}引用引句`}
              value={quotes[r.reading_id] || ""}
              onChange={(e) =>
                setQuotes((v) => ({ ...v, [r.reading_id]: e.target.value }))
              }
            />
          </label>
        </section>
      ))}
      {citation.units.map((u: Row) => (
        <section key={u.id}>
          <h3>
            {u.name} · {location(u)} · {label[u.review_state]}
            {u.current ? "" : " · 已过期"}
          </h3>
          <pre className="ek-preview">{u.text}</pre>
          <label>
            逐字引句
            <textarea
              aria-label={`${u.name}引用引句`}
              value={quotes[u.id] || ""}
              onChange={(e) =>
                setQuotes((v) => ({ ...v, [u.id]: e.target.value }))
              }
            />
          </label>
        </section>
      ))}
      <label>
        核对人
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
      </label>
      <label>
        核对依据、条件与限制
        <textarea value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <label>
        支持性
        <select
          aria-label="支持性"
          value={support}
          onChange={(e) => setSupport(e.target.value)}
        >
          {[
            "unknown",
            "supported",
            "partial",
            "contradicted",
            "unsupported",
          ].map((s) => (
            <option key={s} value={s}>
              {label[s]}
            </option>
          ))}
        </select>
      </label>
      <button
        className="btn primary"
        disabled={
          busy ||
          !note.trim() ||
          !reviewer.trim() ||
          citation.units.some((u: Row) => !quotes[u.id]?.trim()) ||
          citation.document_readings?.some(
            (r: Row) => !quotes[r.reading_id]?.trim(),
          )
        }
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await send(
              `/projects/${pid}/evidence-knowledge/citations/${citation.id}/human-review`,
              {
                expected_revision_id: citation.revision_id,
                claim: citation.claim,
                reviewer,
                note,
                support,
                reading_checks:
                  citation.document_readings?.map((r: Row) => ({
                    reading_id: r.reading_id,
                    expected_version: r.adopted_version,
                    quote: quotes[r.reading_id],
                  })) || [],
                checks: citation.units.map((u: Row) => ({
                  unit_id: u.id,
                  expected_hash: u.content_hash,
                  quote: quotes[u.id],
                  conditions: note.split("\n").filter(Boolean),
                })),
              },
            );
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        保存当前引用的人工判断
      </button>
    </WorkspaceDialog>
  );
}

function ImportDialog({
  pid,
  onClose,
  onDone,
}: {
  pid: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [preview, setPreview] = useState<Row | null>(null),
    [items, setItems] = useState<Row[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const action = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <WorkspaceDialog
      title="批量导入研究原件"
      onClose={onClose}
      wide
      busy={busy}
    >
      <p>
        预览并编辑书目后再导入。原文解析在本机执行；已有人工书目不会被重复导入覆盖。
      </p>
      {error && <p role="alert">{error}</p>}
      <label>
        选择文献文件
        <input
          aria-label="批量选择研究原件"
          type="file"
          multiple
          accept=".pdf,.docx,.pptx,.txt,.md,.csv,.xlsx"
          disabled={busy}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            void action(async () => {
              const body = new FormData();
              files.forEach((f) => body.append("files", f));
              const data = await api(
                `/projects/${pid}/evidence-knowledge/imports/preview`,
                { method: "POST", body },
              );
              setPreview(data);
              setItems(
                data.items.map((r: Row) => ({ ...r, include: !r.error })),
              );
            });
          }}
        />
      </label>
      <div className="ek-import-rows">
        {items.map((r, index) => (
          <article key={r.index}>
            <label>
              <input
                type="checkbox"
                checked={r.include}
                disabled={!!r.error}
                onChange={(e) =>
                  setItems((v) =>
                    v.map((x, i) =>
                      i === index ? { ...x, include: e.target.checked } : x,
                    ),
                  )
                }
              />
              {r.name}
            </label>
            {r.error && <p role="alert">{r.error}</p>}
            {r.duplicate && <p>原件已存在，导入将复用原件并保留已登记书目。</p>}
            {!r.error &&
              [
                ["title", "题名"],
                ["authors", "作者（逗号分隔）"],
                ["year", "年份"],
              ].map(([key, title]) => (
                <label key={key}>
                  {title}
                  <input
                    aria-label={`${r.name} ${title}`}
                    value={
                      key === "authors"
                        ? r.metadata.authors.join("，")
                        : r.metadata[key]
                    }
                    onChange={(e) =>
                      setItems((v) =>
                        v.map((x, i) =>
                          i === index
                            ? {
                                ...x,
                                metadata: {
                                  ...x.metadata,
                                  [key]:
                                    key === "authors"
                                      ? e.target.value
                                          .split(/[,，]/)
                                          .filter(Boolean)
                                      : e.target.value,
                                },
                              }
                            : x,
                        ),
                      )
                    }
                  />
                </label>
              ))}
          </article>
        ))}
      </div>
      <button
        className="btn primary"
        disabled={busy || !preview || !items.some((r) => r.include)}
        onClick={() =>
          void action(async () => {
            await send(
              `/projects/${pid}/evidence-knowledge/imports/${preview!.id}/commit`,
              {
                items: items.map((r) => ({
                  index: r.index,
                  include: r.include,
                  metadata: r.metadata,
                })),
              },
            );
            onDone();
          })
        }
      >
        确认导入选中文献
      </button>
    </WorkspaceDialog>
  );
}

function EvaluationDialog({
  pid,
  onClose,
}: {
  pid: string;
  onClose: () => void;
}) {
  const base = `/projects/${pid}/evidence-knowledge`;
  const [cases, setCases] = useState("[]"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState<Row | null>(null),
    [judgments, setJudgments] = useState("[]"),
    [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState("");
  const action = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const history = useWorkspaceResource<Row[]>(
    `${base}/evaluations`,
    result?.id ? 1 : 0,
  );
  return (
    <WorkspaceDialog
      title="解析与检索对照评测"
      onClose={onClose}
      wide
      busy={busy}
    >
      <p>
        使用同一组问题比较旧版原文检索与结构单元检索。页码召回、单元召回与人工语义判断分别记录。
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        className="btn"
        disabled={busy}
        onClick={() =>
          void action(async () => {
            const data = await send(`${base}/evaluation-samples`);
            setCases(JSON.stringify(data.cases, null, 2));
          })
        }
      >
        生成最多20条原文定位草稿
      </button>
      <p className="muted">
        自动草稿需要改成实际写作问题并核对标准原文，不视为人工验收。
      </p>
      <label>
        问题与标准原文单元 JSON
        <textarea
          aria-label="评测问题 JSON"
          className="ek-json"
          value={cases}
          onChange={(e) => setCases(e.target.value)}
        />
      </label>
      <button
        className="btn primary"
        disabled={busy}
        onClick={() =>
          void action(async () => {
            const data = await send(`${base}/evaluations`, {
              cases: JSON.parse(cases),
            });
            setResult(data);
            setJudgments(
              JSON.stringify(
                data.cases.map(() => ({ support: "unknown", note: "" })),
                null,
                2,
              ),
            );
          })
        }
      >
        运行本地对照评测
      </button>
      {result && (
        <section>
          <h3>检索结果</h3>
          <div className="ek-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>方案</th>
                  <th>原页 Recall</th>
                  <th>原页 MRR</th>
                  <th>单元 Recall</th>
                  <th>平均耗时</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.summary).map(([stage, value]) => {
                  const v = value as Row;
                  return (
                    <tr key={stage}>
                      <td>{stage === "originals" ? "旧版原文" : "结构单元"}</td>
                      <td>{v.page_recall.toFixed(3)}</td>
                      <td>{v.page_mrr.toFixed(3)}</td>
                      <td>{v.unit_recall.toFixed(3)}</td>
                      <td>{v.milliseconds.toFixed(0)}ms</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p>这些数值只衡量原文定位，不证明论断或引用正确。</p>
          <details>
            <summary>逐条结果</summary>
            <pre className="ek-json">
              {JSON.stringify(result.cases, null, 2)}
            </pre>
          </details>
          <label>
            核对人
            <input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
            />
          </label>
          <label>
            核对说明
            <textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <label>
            逐条人工判断 JSON（correct / partial / incorrect / unknown）
            <textarea
              className="ek-json"
              value={judgments}
              onChange={(e) => setJudgments(e.target.value)}
            />
          </label>
          <button
            className="btn"
            disabled={busy}
            onClick={() =>
              void action(async () => {
                const reviewed = await send(
                  `${base}/evaluations/${result.id}/human-review`,
                  { reviewer, note, judgments: JSON.parse(judgments) },
                );
                setResult({ ...result, ...reviewed });
              })
            }
          >
            保存人工语义核对
          </button>
          {result.human_review && (
            <p>
              已保存 {result.human_review.reviewer} 的人工记录；
              {result.human_acceptance
                ? "本组逐条判为正确"
                : "仍有未通过或待判断项"}
              。
            </p>
          )}
        </section>
      )}
      <details>
        <summary>历史评测</summary>
        {history.data?.map((r) => (
          <button
            className="btn"
            key={r.id}
            onClick={() => {
              setResult({ id: r.id, ...r.result });
              setCases(JSON.stringify(r.cases, null, 2));
              setJudgments(
                JSON.stringify(
                  r.result.cases.map((c: Row) => ({
                    support: c.human_judgment?.support || "unknown",
                    note: c.human_judgment?.note || "",
                  })),
                  null,
                  2,
                ),
              );
            }}
          >
            {new Date(r.created_at).toLocaleString()} · {r.result.cases.length}
            条
          </button>
        ))}
      </details>
    </WorkspaceDialog>
  );
}

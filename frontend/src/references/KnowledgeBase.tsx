import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Database,
  FileText,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";
import { api, send } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import {
  ResearchColumns,
  ResearchEmpty,
  ReferenceInspector,
  useReferenceTitle,
} from "./ReferenceWorkbenchParts";
import type { Row, WorkspaceAction } from "../workspace/types";

type Resource = {
  data: Row | null;
  error: string;
  loading: boolean;
  load: () => Promise<void>;
};
type SourceAction = (file: Row, page: number) => void;

function KnowledgeHit({
  item,
  files,
  onSource,
}: {
  item: Row;
  files: Row[];
  onSource: SourceAction;
}) {
  const original = files.find((file) => file.id === item.file_id);
  return (
    <article className="knowledge-hit" data-source-id={item.id}>
      <div className="knowledge-hit-heading">
        <div>
          <h3>{item.reference_title || item.name}</h3>
          <small>
            {item.reference_year ? item.reference_year + " · " : ""}
            {item.page_number ? `第 ${item.page_number} 页` : "原文片段"} ·{" "}
            {item.review_state === "reviewed" ? "原页已核对" : "原页待核对"}
          </small>
          {item.rerank_rank && (
            <small className="knowledge-rank-trace">
              RRF 第 {item.fusion_rank} 名 → 重排第 {item.rerank_rank} 名
            </small>
          )}
        </div>
        <button
          className="btn"
          disabled={!original}
          onClick={() => original && onSource(original, item.page_number || 1)}
        >
          <FileText size={15} />
          查看原文位置
        </button>
      </div>
      <blockquote>{item.text}</blockquote>
    </article>
  );
}

export function KnowledgeBasePanel({
  pid,
  knowledge,
  files,
  act,
  onSource,
  fileId,
}: {
  pid: string;
  knowledge: Resource;
  files: Row[];
  act: WorkspaceAction;
  onSource: SourceAction;
  fileId?: string;
}) {
  const titleOf = useReferenceTitle();
  const [selectedHit, setSelectedHit] = useState("");
  const [query, setQuery] = useState(""),
    [result, setResult] = useState<Row | null>(null),
    [searchedQuery, setSearchedQuery] = useState(""),
    [searching, setSearching] = useState(false),
    [updating, setUpdating] = useState(false),
    [error, setError] = useState("");
  const request = useRef<AbortController | null>(null),
    lock = useRef(false);
  const active = knowledge.data?.task;
  useEffect(() => {
    if (!active?.id) return;
    const timer = window.setInterval(() => void knowledge.load(), 3500);
    return () => window.clearInterval(timer);
  }, [active?.id, knowledge.load]);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    request.current?.abort();
    lock.current = false;
    setSearching(false);
    setResult(null);
    setSearchedQuery("");
  }, [fileId, pid]);
  return (
    <section className="reference-knowledge" aria-label="文献知识库">
      <div className="knowledge-heading">
        <div>
          <h2>
            <Database size={20} />
            文献知识库
          </h2>
          <p>原文持久保存，按片段检索；写作引用可回到具体文献与页码。</p>
        </div>
        <button
          className="btn"
          disabled={updating || !!active || !knowledge.data?.documents}
          onClick={async () => {
            if (updating) return;
            setUpdating(true);
            try {
              await act(
                () => send(`/projects/${pid}/knowledge-base/index`),
                "知识库更新已排队，在本机处理，不调用付费接口",
              );
              await knowledge.load();
            } finally {
              setUpdating(false);
            }
          }}
        >
          {updating || active ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <RefreshCw size={16} />
          )}
          {active ? "索引处理中" : "更新知识库"}
        </button>
      </div>
      {active && (
        <p className="knowledge-progress" role="status">
          <LoaderCircle size={15} className="spin" />
          {active.phase || "知识库索引正在排队"}
        </p>
      )}
      {knowledge.error && (
        <div className="knowledge-error" role="alert">
          <p>{knowledge.error}</p>
          <button className="btn" onClick={() => void knowledge.load()}>
            重新加载知识库
          </button>
        </div>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <ResearchColumns
        className="fulltext-workbench"
        directory={
          <>
            <div className="research-directory-heading">
              <h3>原文检索</h3>
              <span>{result?.items.length ?? 0} 条结果</span>
            </div>
            <form
              className="knowledge-search"
              onSubmit={async (event) => {
                event.preventDefault();
                if (lock.current || !query.trim()) return;
                lock.current = true;
                setSearching(true);
                setError("");
                request.current?.abort();
                const controller = new AbortController();
                request.current = controller;
                try {
                  const found = await api(
                    `/projects/${pid}/knowledge-base/search`,
                    {
                      method: "POST",
                      body: JSON.stringify({
                        query: query.trim(),
                        limit: 10,
                        file_ids: fileId ? [fileId] : undefined,
                      }),
                      signal: controller.signal,
                    },
                  );
                  setSelectedHit("");
                  setResult(found);
                  setSearchedQuery(query.trim());
                } catch (failure) {
                  if (!controller.signal.aborted)
                    setError((failure as Error).message);
                } finally {
                  lock.current = false;
                  if (!controller.signal.aborted) setSearching(false);
                }
              }}
            >
              <label>
                <Search size={18} />
                <input
                  aria-label="检索文献知识库"
                  placeholder="检索原文内容，例如：灵魂绑定代币如何用于学历验证？"
                  value={query}
                  maxLength={5000}
                  disabled={searching}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
              <button
                type="submit"
                className="btn primary"
                disabled={searching || !query.trim()}
              >
                {searching ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <Search size={16} />
                )}
                {searching ? "正在检索…" : "检索知识库"}
              </button>
            </form>

            <p className="research-directory-help">
              输入问题或文献中的术语，定位相关原文。
            </p>
            <div className="fulltext-result-directory">
              {result?.items.map((item: Row, index: number) => (
                <button
                  key={item.id}
                  className="research-directory-item"
                  aria-pressed={
                    item.id === (selectedHit || result.items[0]?.id)
                  }
                  onClick={() => setSelectedHit(item.id)}
                >
                  <small>
                    {index + 1} · 第 {item.page_number || "—"} 页
                  </small>
                  <strong>{item.reference_title || titleOf(item)}</strong>
                  <p className="ek-directory-excerpt">
                    {item.text?.slice(0, 120)}
                  </p>
                </button>
              ))}
            </div>
            {result && !result.items.length && (
              <p className="research-directory-empty">
                没有找到匹配的片段，试试其他关键词。
              </p>
            )}
          </>
        }
        inspector={
          <>
            <ReferenceInspector title="当前检索">
              <p>{searchedQuery || "尚未提交检索问题"}</p>
              {result && (
                <p>
                  找到 {result.items.length}{" "}
                  个原文片段。选择左侧结果阅读完整内容。
                </p>
              )}
              <p>检索排序用于定位，论断是否成立还需要对照原文。</p>
            </ReferenceInspector>
            <ReferenceInspector title="知识库状态">
              <details>
                <summary>查看索引与检索过程</summary>{" "}
                <div className="knowledge-metrics" aria-label="知识库索引统计">
                  <span>
                    原文 <strong>{knowledge.data?.documents ?? "—"}</strong> 篇
                  </span>
                  <span>
                    知识片段{" "}
                    <strong>
                      {knowledge.data?.chunks?.toLocaleString("zh-CN") ?? "—"}
                    </strong>
                  </span>
                  <span>
                    向量索引{" "}
                    <strong>
                      {knowledge.data?.vectors?.toLocaleString("zh-CN") ?? "—"}
                    </strong>{" "}
                    / {knowledge.data?.chunks?.toLocaleString("zh-CN") ?? "—"}
                  </span>
                  <span className="knowledge-mode">
                    {knowledge.data?.mode === "hybrid"
                      ? "BM25 + 向量 · RRF 融合"
                      : "BM25 全文检索"}
                  </span>
                  {knowledge.data?.rerank_enabled && (
                    <span className="knowledge-mode">
                      {knowledge.data?.reranker_ready
                        ? "Cross-Encoder 重排已就绪"
                        : "重排模型待安装"}
                    </span>
                  )}
                </div>
                {result && (
                  <>
                    {result.pipeline && (
                      <details className="knowledge-pipeline-details">
                        <summary>查看检索过程</summary>
                        <div className="knowledge-pipeline-stages">
                          <span>
                            BM25 召回{" "}
                            <strong>
                              {result.pipeline.bm25?.candidates ?? 0}
                            </strong>
                          </span>
                          <span>
                            向量召回{" "}
                            <strong>
                              {result.pipeline.dense?.candidates ?? 0}
                            </strong>
                          </span>
                          <span>
                            RRF 候选{" "}
                            <strong>
                              {result.pipeline.rrf?.candidates ?? 0}
                            </strong>
                          </span>
                          <span>
                            Cross-Encoder 重排{" "}
                            <strong>
                              {result.pipeline.rerank?.type ===
                                "cross_encoder" &&
                              result.pipeline.rerank?.status === "completed"
                                ? result.pipeline.rerank.scored
                                : 0}
                            </strong>
                          </span>
                        </div>
                        {result.timings_ms?.total != null && (
                          <small>
                            本次检索耗时{" "}
                            {(result.timings_ms.total / 1000).toFixed(2)} 秒
                          </small>
                        )}
                      </details>
                    )}
                    {result.warnings?.map((warning: string) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </>
                )}
              </details>
            </ReferenceInspector>
          </>
        }
      >
        {result?.items.length ? (
          <div className="reference-document">
            <span className="research-kicker">原文片段</span>
            <KnowledgeHit
              item={
                result.items.find((item: Row) => item.id === selectedHit) ||
                result.items[0]
              }
              files={files}
              onSource={onSource}
            />
          </div>
        ) : (
          <ResearchEmpty title="从一个问题定位原文">
            在左侧输入关键词，检索结果会保留文献题名、页码和回查入口。
          </ResearchEmpty>
        )}
      </ResearchColumns>
    </section>
  );
}

export function KnowledgeDocumentDialog({
  pid,
  file,
  tick,
  files,
  onClose,
  onSource,
}: {
  pid: string;
  file: Row;
  tick: number;
  files: Row[];
  onClose: () => void;
  onSource: SourceAction;
}) {
  const [offset, setOffset] = useState(0);
  const resource = useWorkspaceResource<Row>(
    `/projects/${pid}/knowledge-base/documents/${file.id}/chunks?offset=${offset}&limit=20`,
    tick,
  );
  return (
    <WorkspaceDialog
      title="文献知识片段"
      wide
      onClose={onClose}
      closeLabel="关闭知识片段"
    >
      <p className="knowledge-document-name">{file.name}</p>
      {resource.error ? (
        <div role="alert">
          <p className="error-text">{resource.error}</p>
          <button className="btn" onClick={() => void resource.load()}>
            重新加载片段
          </button>
        </div>
      ) : resource.loading && !resource.data ? (
        <p role="status">
          <LoaderCircle size={18} className="spin" />
          正在加载知识片段…
        </p>
      ) : (
        <>
          <p className="muted">
            共 {resource.data?.total ?? 0} 个片段，保留原文页码与来源。
          </p>
          <div className="knowledge-document-chunks">
            {resource.data?.items.map((item: Row) => (
              <KnowledgeHit
                key={item.id}
                item={item}
                files={files}
                onSource={onSource}
              />
            ))}
          </div>
          {!resource.data?.total && (
            <p className="muted mt">
              原文尚未建立知识片段，请更新知识库或等待解析完成。
            </p>
          )}
          <div className="reference-dialog-actions">
            <button
              className="btn"
              disabled={offset === 0 || resource.loading}
              onClick={() => setOffset(offset - 20)}
            >
              上一页片段
            </button>
            <button
              className="btn"
              disabled={
                !resource.data ||
                offset + 20 >= resource.data.total ||
                resource.loading
              }
              onClick={() => setOffset(offset + 20)}
            >
              下一页片段
            </button>
          </div>
        </>
      )}
    </WorkspaceDialog>
  );
}

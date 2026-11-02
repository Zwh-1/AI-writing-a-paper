import { apiUrl } from "../client";
import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { checkerLabels, ruleScopeLabel, stateLabels } from "../RuleEditor";
import type { Row } from "../workspace/types";
import ImageCoveragePanel from "../references/ImageCoveragePanel";

export default function RuleSourceDialog({
  source,
  onClose,
  onOriginal,
}: {
  source: Row;
  onClose: () => void;
  onOriginal: (file: Row) => void;
}) {
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const snapshot = useMemo(() => {
    if (!source.package) return { rules: [] as Row[], error: "" };
    try {
      const rules =
        typeof source.package.rules === "string"
          ? JSON.parse(source.package.rules)
          : source.package.rules;
      if (!Array.isArray(rules)) throw new Error();
      return { rules: rules as Row[], error: "" };
    } catch {
      return {
        rules: [] as Row[],
        error: "生效快照无法读取，请重新加载后核对。",
      };
    }
  }, [source.package]);
  const results = snapshot.rules.filter((rule) =>
    `${rule.statement} ${ruleScopeLabel(rule)}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(results.length / 20)),
    current = Math.min(page, pages);
  const title = source.package ? "生效规则快照" : "规则原文依据";
  return (
    <WorkspaceDialog
      title={title}
      onClose={onClose}
      closeLabel="关闭原文依据"
      wide
    >
      {source.package ? (
        <>
          <div className="rule-snapshot-intro">
            <h3>规则包 V{source.package.version}</h3>
            <p className="muted">
              发布于{" "}
              {new Date(source.package.created_at).toLocaleString("zh-CN")} ·
              以下为发布时保存的固定快照。
            </p>
          </div>
          <label className="workspace-inline-search">
            <Search size={15} />
            <input
              aria-label="搜索生效规则快照"
              placeholder="在生效快照中查找条款"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
            />
          </label>
          {snapshot.error ? (
            <p className="error-text" role="alert">
              {snapshot.error}
            </p>
          ) : (
            <>
              {results.slice((current - 1) * 20, current * 20).map((rule) => (
                <div className="rule-snapshot-row" key={rule.id}>
                  <p>{rule.statement}</p>
                  <small>
                    {stateLabels[rule.modality]} ·{" "}
                    {ruleScopeLabel(rule)} ·{" "}
                    {checkerLabels[rule.checker]}
                  </small>
                </div>
              ))}
              {!results.length && <p className="muted">没有匹配的生效条款。</p>}
              <div className="workspace-pagination">
                <span>
                  共 {results.length} 条 · 第 {current}/{pages} 页
                </span>
                <div className="actions">
                  <button
                    className="btn"
                    aria-label="上一页生效快照"
                    disabled={current === 1}
                    onClick={() => setPage(current - 1)}
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <button
                    className="btn"
                    aria-label="下一页生效快照"
                    disabled={current === pages}
                    onClick={() => setPage(current + 1)}
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            </>
          )}
        </>
      ) : (
        <>
          <h3>{source.rule.statement}</h3>
          {source.source ? (
            <>
              <p className="muted mb">
                {source.file.name} · 第 {source.page?.page_number ?? "?"} 页 ·{" "}
                {source.reading_context?.requires_original_comparison
                  ? "候选原文待核对"
                  : source.rule.source_observation
                  ? "已对照原图复验 · 助手复验，非人工确认"
                  : source.page?.review_state === "reviewed"
                  ? "原文已核对"
                  : source.page?.review_state === "not_applicable"
                    ? "此页已排除"
                    : "原文待核对"}
              </p>
              {source.rule.source_observation && (
                <p className="muted mb">
                  {source.rule.source_observation.label}。{source.rule.source_observation.scope_limited && "以下仅展示已核对摘录，图中其余文字请打开原页查看。"}
                </p>
              )}
              <h4>候选提取时保存的来源</h4>
              <pre className="rule-source-text">{source.source.text}</pre>
              {source.reading_context && (
                <section aria-label="候选完整来源上下文">
                  <h4>完整来源上下文</h4>
                  <p>{source.reading_context.scope_note}</p>
                  {source.reading_context.raw_observations.length > 0 && (
                    <p>
                      原候选来源 {source.reading_context.legacy_source_characters} 字 ·
                      完整保存观察 {source.reading_context.full_observation_characters} 字
                    </p>
                  )}
                  {source.reading_context.source_issues.includes("legacy_source_is_not_complete_image_observation") && (
                    <p role="status">旧来源只包含部分或另一份转录；请核对下方完整观察。{source.rule.state === "candidate" ? "候选仍保持待核验状态。" : "整图观察须单独核对。"}</p>
                  )}
                  {source.reading_context.native_context.map((part: Row, index: number) => (
                    <details key={`native-${index}`}>
                      <summary>原页文字与备注</summary>
                      <pre className="rule-source-text">{part.text}{part.notes ? `\n\n[原页备注]\n${part.notes}` : ""}</pre>
                    </details>
                  ))}
                  {source.reading_context.raw_observations.map((observation: Row, index: number) => (
                    <details key={`raw-${index}`} open>
                      <summary>完整保存的OCR观察 · 尚未核对</summary>
                      <pre className="rule-source-text">{observation.text}</pre>
                    </details>
                  ))}
                  {source.reading_context.verified_passages.map((passage: Row) => (
                    <details key={passage.source_id}>
                      <summary>{passage.coverage === "complete_image_text" ? "整图校对文字" : passage.coverage === "selected_complete_passages" ? "独立校对摘录" : "历史转录 · 覆盖范围未核清"}</summary>
                      <pre className="rule-source-text">{passage.text}</pre>
                    </details>
                  ))}
                </section>
              )}
              <ImageCoveragePanel pid={source.file.project_id} fid={source.file.id} page={source.page?.page_number} />
              <div className="actions">
                <button
                  className="btn primary"
                  onClick={() =>
                    onOriginal({
                      ...source.file,
                      initialPage: source.page?.page_number ?? 1,
                    })
                  }
                >
                  打开原页核对
                </button>
                <a
                  className="btn"
                  href={apiUrl(`/files/${source.file.id}/download`)}
                >
                  下载原文件
                </a>
              </div>
            </>
          ) : (
            <p>此条为用户手工新增，尚未关联材料来源。</p>
          )}
        </>
      )}
    </WorkspaceDialog>
  );
}

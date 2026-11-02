import { useEffect, useId, useState } from "react";
import { apiUrl, api } from "./client";
import { ruleReviewDetails } from "./ruleReviewDetails";

type Row = Record<string, any>;
type Bucket = "accepted" | "reference" | "uncertain" | "unreviewed";
const groups: { key: Bucket; label: string; count: string }[] = [
  { key: "accepted", label: "本次通过", count: "accepted_count" },
  { key: "reference", label: "参考", count: "reference_count" },
  { key: "uncertain", label: "不确定", count: "uncertain_count" },
  { key: "unreviewed", label: "未审核", count: "unreviewed_count" },
];

export default function RuleTaskReview({ projectId, taskId, review, tick, onSource }: {
  projectId: string;
  taskId: string;
  review: Row;
  tick: string;
  onSource?: (rule: Row) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Bucket | "all">("all");
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    let stream: EventSource | undefined;
    const controller = new AbortController();
    setError("");
    setData(null);
    api(`/projects/${projectId}/rules`, { signal: controller.signal })
      .then(value => {
        if (!alive) return;
        const events: Row[] = [];
        const update = () => setData(ruleReviewDetails(projectId, value.rules ?? [], events));
        stream = new EventSource(apiUrl(`/tasks/${taskId}/events`));
        stream.addEventListener("progress", event => {
          if (!alive) return;
          try {
            const item = JSON.parse((event as MessageEvent).data);
            if (Array.isArray(item.rule_ids) || (typeof item.rule_id === "string" && typeof item.state === "string")) {
              events.push(item);
              update();
            }
          } catch {
            setError("审核记录格式异常，请重新加载详情");
            stream?.close();
          }
        });
        stream.addEventListener("done", () => { if (alive) update(); stream?.close(); });
        stream.onerror = () => {
          if (alive) setError("审核记录暂时无法加载，请重试");
          stream?.close();
        };
      })
      .catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; controller.abort(); stream?.close(); };
  }, [projectId, taskId, tick, open, retry]);
  const show = (key: Bucket | "all") => { setFilter(key); setOpen(true); };
  const items: Row[] = (data?.items ?? []).filter((item: Row) => filter === "all" || item.bucket === filter);
  return <div className="rule-task-review">
    {review.accepted_count !== undefined && <div className="rule-review-counts" aria-label="本次审核分类">
      {groups.map(group => <button type="button" key={group.key} className="rule-review-count" aria-controls={id} aria-expanded={open && filter === group.key} onClick={() => show(group.key)}>
        {group.label} {review[group.count] ?? 0} 条
      </button>)}
      <span>复用判定 {review.reused_decisions ?? 0} 条</span>
    </div>}
    <button type="button" className="btn" aria-controls={id} aria-expanded={open} onClick={() => open ? setOpen(false) : show("all")}>
      {open ? "收起审核详情" : "查看审核详情"}
    </button>
    {open && <section id={id} className="rule-review-details" aria-label="审核条款详情">
      <div className="rule-review-filters" aria-label="筛选审核条款">
        <button type="button" className="btn" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>全部{data ? `（${data.items.length}）` : ""}</button>
        {groups.map(group => <button type="button" className="btn" key={group.key} aria-pressed={filter === group.key} onClick={() => setFilter(group.key)}>{group.label}{data ? `（${data.counts[group.key]}）` : ""}</button>)}
      </div>
      {error ? <div role="alert"><p>{error}</p><button type="button" className="btn" onClick={() => setRetry(n => n + 1)}>重新加载详情</button></div> : !data ? <p role="status">正在加载审核条款…</p> : <>
        <p className="muted">{data.note}</p>
        {data.scope_saved && items.length === 0 && <p>本次任务没有此类条款。</p>}
        {items.map(item => <article className="rule-review-item" key={item.rule_id}>
          <strong>{groups.find(group => group.key === item.bucket)?.label} · {item.rule?.statement ?? "该条款当前已不存在"}</strong>
          <p>{item.reason}</p>
          {item.rule?.source_id && <>
            <p>{item.rule.source_file} · 第 {item.rule.page_number ?? "?"} 页</p>
            {onSource && item.rule && <button type="button" className="btn" onClick={() => onSource(item.rule)}>打开原文核对</button>}
          </>}
          {!item.rule?.source_id && item.rule && <p>此条款当前未关联可查看的原文。</p>}
        </article>)}
      </>}
    </section>}
  </div>;
}

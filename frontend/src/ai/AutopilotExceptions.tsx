import { useMemo, useState } from "react";
import { ArrowUpRight, CheckCircle2, Search } from "lucide-react";
import { useAIAutopilot, type AIDomain } from "./useAIAutopilot";
import { WorkspaceEmpty } from "../workspace/WorkspaceChrome";

const states: Record<string, string> = {
  unknown: "远端结果待核实",
  conflict: "来源存在差异",
  uncertain: "依据尚不充分",
  blocked: "等待补充依据",
  stale: "内容已变化",
  pending: "等待 AI 处理",
};
export default function AutopilotExceptions({
  scope,
  onRule,
  onMaterial,
  onOtherDomain,
}: {
  scope?: AIDomain;
  onRule?: (id: string) => void;
  onMaterial?: (id: string) => void;
  onOtherDomain?: () => void;
}) {
  const { data } = useAIAutopilot();
  const [kind, setKind] = useState(
      scope === "materials" ? "material" : scope === "rules" ? "rule" : "all",
    ),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const matches = useMemo(
    () =>
      (data?.exceptions ?? []).filter(
        (item) =>
          (kind === "all" || item.kind === kind) &&
          `${item.label} ${item.reason}`
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
      ),
    [data?.exceptions, kind, query],
  );
  if (!data) return null;
  const pages = Math.max(1, Math.ceil(matches.length / 10)),
    current = Math.min(page, pages);
  return (
    <details className="autopilot-exceptions">
      <summary>
        处理例外（{data.exception_count} 项）
        <span>查看原因与原文，人工仅处理必要例外</span>
      </summary>
      <div className="autopilot-exception-toolbar">
        <div
          className="autopilot-exception-filters"
          aria-label="按例外类型筛选"
        >
          {[
            ["all", "全部"],
            ["material", "资料"],
            ["rule", "规则"],
          ].map(([key, label]) => (
            <button
              key={key}
              aria-pressed={kind === key}
              onClick={() => {
                setKind(key);
                setPage(1);
              }}
            >
              {label}
              <small>
                {key === "all"
                  ? data.exception_count
                  : data.exceptions.filter((item) => item.kind === key).length}
              </small>
            </button>
          ))}
        </div>
        <label className="workspace-inline-search">
          <Search size={15} />
          <input
            aria-label="搜索处理例外"
            placeholder="搜索条目或原因"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
      {matches.length ? (
        <>
          {matches.slice((current - 1) * 10, current * 10).map((item) => {
            const open = item.kind === "rule" ? onRule : onMaterial;
            return (
              <article key={`${item.kind}:${item.id}`}>
                <div>
                  <span className="autopilot-exception-state">
                    {states[item.state] ?? "需要核对"}
                  </span>
                  <strong>{item.label}</strong>
                  <p>{item.reason || "请打开条目核对当前原文与处理记录。"}</p>
                </div>
                {open ? (
                  <button className="btn" onClick={() => open(item.id)}>
                    {item.kind === "rule" ? "核对条款" : "打开资料"}
                    <ArrowUpRight size={12} />
                  </button>
                ) : (
                  onOtherDomain && (
                    <button className="btn" onClick={onOtherDomain}>
                      {item.kind === "rule" ? "前往论文规则" : "前往资料中心"}
                      <ArrowUpRight size={12} />
                    </button>
                  )
                )}
              </article>
            );
          })}
          <div className="workspace-pagination">
            <span>
              {matches.length} 项 · 第 {current}/{pages} 页
            </span>
            <div className="actions">
              <button
                className="btn"
                aria-label="上一页处理例外"
                disabled={current === 1}
                onClick={() => setPage(current - 1)}
              >
                上一页
              </button>
              <button
                className="btn"
                aria-label="下一页处理例外"
                disabled={current === pages}
                onClick={() => setPage(current + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        </>
      ) : (
        <WorkspaceEmpty
          title={query ? "没有匹配的例外" : "此类条目暂无处理例外"}
          description={
            query
              ? "试试其他关键词，或切换例外类型。"
              : "AI 会继续处理可完成的内容，必要例外会显示在这里。"
          }
        >
          <CheckCircle2 size={24} />
        </WorkspaceEmpty>
      )}
    </details>
  );
}

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
  needs_full_source: "原图文字待核对",
};
const purposes: Record<string, string> = {
  conditional: "条件参考",
  knowledge: "知识讲解",
  example: "教学示例",
  counterexample: "教学反例",
  heading: "标题",
  noise: "界面或装饰信息",
};
export default function AutopilotExceptions({
  scope,
  onRule,
  onRuleSource,
  onMaterial,
  onOtherDomain,
}: {
  scope?: AIDomain;
  onRule?: (id: string) => void;
  onRuleSource?: (id: string) => void;
  onMaterial?: (id: string) => void;
  onOtherDomain?: () => void;
}) {
  const { data } = useAIAutopilot();
  const [kind, setKind] = useState(scope === "materials" ? "material" : "all"),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1);
  const matches = useMemo(
    () =>
      (data?.exceptions ?? []).filter(
        (item) =>
          (kind === "all" ||
            (kind === "material"
              ? item.kind === "material"
              : item.kind === "rule" &&
                (item.issue_kind || "rule_decision") === kind)) &&
          `${item.label} ${item.reason} ${item.source_file || ""} ${purposes[item.purpose_hint || ""] || ""}`
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
        <span>文字识别与规则判断分别核对，未核验内容继续保留</span>
      </summary>
      <div className="autopilot-exception-toolbar">
        <div
          className="autopilot-exception-filters"
          aria-label="按例外类型筛选"
        >
          {[
            ["all", "全部"],
            ["material", "资料归类"],
            ["source_text", "文字识别"],
            ["rule_decision", "规则判断"],
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
                  : data.exceptions.filter((item) =>
                      key === "material"
                        ? item.kind === "material"
                        : item.kind === "rule" &&
                          (item.issue_kind || "rule_decision") === key,
                    ).length}
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
            const reading = item.issue_kind === "source_text";
            const open =
              item.kind === "rule"
                ? reading
                  ? onRuleSource || onRule
                  : onRule
                : onMaterial;
            return (
              <article
                key={`${item.kind}:${item.id}`}
                data-exception-id={item.id}
              >
                <div>
                  <span className="autopilot-exception-state">
                    {item.content_status === "source_cropped"
                      ? "原件图片裁断"
                      : (states[item.state] ?? "需要核对")}
                  </span>
                  {item.purpose_hint && (
                    <small className="autopilot-purpose-hint">
                      历史用途提示：
                      {purposes[item.purpose_hint] || item.purpose_hint} ·
                      内容仍待核验
                    </small>
                  )}
                  <strong>
                    {reading && item.source_file
                      ? `${item.source_file} · 第${item.source_page || "?"}页`
                      : item.label}
                  </strong>
                  <p>{item.reason || "请打开条目核对当前原文与处理记录。"}</p>
                  {reading && (
                    <>
                      <details>
                        <summary>查看本条未核验OCR原文</summary>
                        <pre className="autopilot-reading-text">
                          {item.label}
                        </pre>
                      </details>
                      {item.supporting_excerpt && (
                        <details>
                          <summary>查看并列校对摘录（未覆盖整条候选）</summary>
                          <pre className="autopilot-reading-text">
                            {item.supporting_excerpt.text}
                          </pre>
                        </details>
                      )}
                    </>
                  )}
                </div>
                {open ? (
                  <button className="btn" onClick={() => open(item.id)}>
                    {item.kind === "rule"
                      ? reading && onRuleSource
                        ? "查看原图与识别内容"
                        : "核对条款"
                      : "打开资料"}
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
              : "当前没有此类待处理项；其他分类和原图识别覆盖情况仍需分别查看。"
          }
        >
          <CheckCircle2 size={24} />
        </WorkspaceEmpty>
      )}
    </details>
  );
}

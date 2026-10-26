import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  ShieldCheck,
} from "lucide-react";
import { summarizeRuleRecords } from "./rules/ruleRecordSummary";
type Row = Record<string, any>;
export default function RulePublication({
  data,
  busy,
  onPublish,
  onSnapshot,
  onBlocker,
  onPage,
  onRecords,
}: {
  data: Row;
  busy: boolean;
  onPublish: () => void;
  onSnapshot: () => void;
  onBlocker: (blocker: Row) => void;
  onPage: (page: Row) => void;
  onRecords: (filter: string) => void;
}) {
  const ready = data.readiness;
  const summary = summarizeRuleRecords(data.rules);
  return (
    <section
      className="panel mb rule-publication"
      id="rule-publication"
      aria-label="发布前检查"
    >
      <div className="section-heading">
        <div>
          <h2>发布前检查</h2>
          <p className="muted">检查适用要求与原文，发布后供后续写作使用。</p>
        </div>
        <span
          className={`rules-status-pill ${ready.can_publish ? "is-ready" : "is-pending"}`}
        >
          {ready.can_publish ? "可以发布" : `${ready.blockers.length} 项待处理`}
        </span>
      </div>
      <div className="rule-package-status">
        <ShieldCheck size={23} />
        <div>
          <strong>
            {data.package
              ? `当前生效：规则包 V${data.package.version}`
              : "还没有生效规则包"}
          </strong>
          <p>
            {data.package
              ? ready.dirty
                ? "有尚未发布的修改；当前写作仍加载已发布版本。"
                : `当前已确认的要求与生效版本一致，共 ${ready.package_rule_count} 条。`
              : "核对资料页、确认适用要求后即可发布。"}
          </p>
        </div>
        {data.package && (
          <button className="btn" onClick={onSnapshot}>
            查看生效快照
          </button>
        )}
      </div>
      <div className="rules-publication-counts">
        <span>
          本次纳入 <strong>{summary.publishable}</strong> 条 （强制{" "}
          {summary.mandatory} · 建议 {summary.recommended}）
        </span>
        <span>
          待核对资料 <strong>{ready.pending_pages.length}</strong> 页
        </span>
        <span>
          待确认 / 分类 <strong>{summary.candidate}</strong> 条
        </span>
      </div>
      <div className="rules-publication-scope">
        <p>
          全部 {summary.total} 条记录中，其余{" "}
          {summary.total - summary.publishable} 条
          保留供查阅，不纳入本次规则包。
        </p>
        <div>
          {[
            ["reference_only", "仅供参考", summary.reference_only],
            ["rejected", "已归并 / 排除", summary.rejected],
            ["candidate", "待确认 / 分类", summary.candidate],
          ].map(([filter, label, count]) => (
            <button
              key={filter}
              className="text-button"
              onClick={() => onRecords(String(filter))}
            >
              {label} <strong>{count}</strong> 条
              <ArrowUpRight size={12} />
            </button>
          ))}
        </div>
      </div>
      {ready.blockers.map((blocker: Row) => (
        <div className="rule-blocker" key={blocker.code}>
          <AlertTriangle size={16} />
          <span>{blocker.message}</span>
          <button className="text-button" onClick={() => onBlocker(blocker)}>
            {["RULE_CONFLICT", "RULE_AUTOMATIC_CONFLICTS"].includes(
              blocker.code,
            )
              ? "处理冲突"
              : blocker.code === "RULE_DUPLICATES"
                ? "处理重复"
                : blocker.code === "RULE_CONFIRMATION_REQUIRED"
                  ? "查看待确认"
                  : blocker.code === "RULE_SOURCE_INACTIVE"
                    ? "复核条款"
                    : blocker.code === "RULE_MACHINE_REVIEW_STALE"
                      ? "更新自动审核"
                      : "核对资料"}
            <ArrowUpRight size={13} />
          </button>
        </div>
      ))}
      {ready.pending_pages.length > 0 && (
        <details className="rule-advanced">
          <summary>查看待核对页面（{ready.pending_pages.length}）</summary>
          <div className="rule-pending-pages">
            {ready.pending_pages.map((page: Row) => (
              <button
                key={page.id}
                className="text-button"
                onClick={() => onPage(page)}
              >
                {page.name} · 第{page.page_number}页<ArrowUpRight size={13} />
              </button>
            ))}
          </div>
        </details>
      )}
      {ready.can_publish && (
        <p className="rule-ready">
          <CheckCircle2 size={16} />
          发布条件已满足。待确认条款及正反例不进入规则包。
        </p>
      )}
      <div className="rules-publish-action">
        <button
          className="btn primary"
          disabled={busy || !ready.can_publish}
          onClick={onPublish}
        >
          <ShieldCheck size={16} />
          {busy ? "正在发布…" : "发布规则包"}
        </button>
        <small>
          {ready.dirty
            ? "发布后更新写作使用的规则版本。"
            : "每次发布保留独立的规则快照。"}
        </small>
      </div>
    </section>
  );
}

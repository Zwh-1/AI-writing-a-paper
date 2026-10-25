import { ArrowUpRight, ShieldCheck } from "lucide-react";
import type { Row } from "../workspace/types";
import type { RulesView } from "./useRulesWorkspace";

export default function RulesOverview({
  data,
  navigate,
  onCandidates,
  onAll,
  onSnapshot,
}: {
  data: Row | null;
  navigate: (view: RulesView) => void;
  onCandidates: () => void;
  onAll: () => void;
  onSnapshot: () => void;
}) {
  const ready = data?.readiness;
  return (
    <section className="rules-overview" aria-label="规则概况与发布状态">
      <div className="rules-version-summary">
        <span className="rules-version-icon">
          <ShieldCheck size={22} />
        </span>
        <div>
          <span className="rules-overline">当前写作使用</span>
          <strong>
            {data?.package
              ? `规则包 V${data.package.version}`
              : data
                ? "尚未发布规则包"
                : "正在读取规则包…"}
          </strong>
          <p>
            {data?.package
              ? `${ready.package_rule_count} 条生效要求${ready.dirty ? " · 有未发布修改" : " · 已保存版本快照"}`
              : "AI 整理并检查适用要求后，自动更新规则包。"}
          </p>
        </div>
        <div className="rules-version-actions">
          {ready && (
            <button
              className={`rules-status-pill ${ready.can_publish ? "is-ready" : "is-pending"}`}
              onClick={() => navigate("publication")}
            >
              {ready.can_publish
                ? ready.dirty
                  ? "可发布更新"
                  : "发布条件就绪"
                : `${ready.blockers.length} 项发布待办`}
              <ArrowUpRight size={12} />
            </button>
          )}
          {data?.package && (
            <button className="text-button" onClick={onSnapshot}>
              查看生效快照
              <ArrowUpRight size={12} />
            </button>
          )}
        </div>
      </div>
      <div className="rules-metrics" aria-label="规则概况">
        <button disabled={!data} onClick={onAll}>
          <span>全部条款</span>
          <strong>{data?.rules.length ?? "—"}</strong>
          <small>
            查找与管理
            <ArrowUpRight size={12} />
          </small>
        </button>
        <button disabled={!data} onClick={onCandidates}>
          <span>待确认</span>
          <strong>{ready?.counts.candidate ?? "—"}</strong>
          <small>
            查看仍需核验的候选
            <ArrowUpRight size={12} />
          </small>
        </button>
        <button disabled={!data} onClick={() => navigate("issues")}>
          <span>重复与冲突</span>
          <strong>
            {data ? data.duplicates.length + data.conflicts.length : "—"}
            <em>组</em>
          </strong>
          <small>
            保留差异与来源
            <ArrowUpRight size={12} />
          </small>
        </button>
        <button disabled={!data} onClick={() => navigate("publication")}>
          <span>本次可发布</span>
          <strong>{ready?.publishable_count ?? "—"}</strong>
          <small>
            检查写作使用范围
            <ArrowUpRight size={12} />
          </small>
        </button>
      </div>
    </section>
  );
}

import { useEffect, useState } from "react";
import { send } from "./client";
import { scopeLabels, stateLabels } from "./RuleEditor";
import type { Row } from "./workspace/types";
import "./duplicateWorkflow.css";
import DuplicateReviewRecord from "./DuplicateReviewRecord";
import { adoptionStateLabel, adoptionPurposeLabel, reviewPurposeLabel } from "./duplicateWorkflowLabels";

export default function DuplicateGroupWorkflow({ pid, group, allRules, busy, onSource, onEdit, run, comparisonOnly = false }: {
  pid: string; group: Row; allRules: Row[]; busy: string; onSource: (rule: Row) => void; onEdit: (rule: Row) => void;
  run: (key: string, fn: () => Promise<any>, message: string) => Promise<any>; comparisonOnly?: boolean;
}) {
  const initial = group.canonical_id || group.rules.find((r: Row) => r.state === "confirmed")?.id || group.rules[0].id;
  const [canonical, setCanonical] = useState(initial), [proof, setProof] = useState<Row | null>(null);
  const [checking, setChecking] = useState(false), [error, setError] = useState("");
  useEffect(() => { setProof(null); setError(""); }, [canonical, group.id, group.rules]);
  const check = async () => {
    setChecking(true); setError(""); setProof(null);
    try { setProof(await send(`/projects/${pid}/rules/duplicates/review`, { canonical_id: canonical, duplicate_ids: group.rules.filter((r: Row) => r.id !== canonical).map((r: Row) => r.id) })); }
    catch (e) { setError((e as Error).message); }
    finally { setChecking(false); }
  };
  const adopt = async () => {
    if (!proof?.ready || comparisonOnly) return;
    const result = await run(`duplicate-adopt-${proof.id}`, () => send(`/projects/${pid}/rules/duplicates/adopt`, { review_id: proof.id, expected_fingerprint: proof.fingerprint }), "重复提取记录已归并，保留记录的状态与用途保持，全部来源可追溯");
    if (!result) setProof(null);
  };
  const preview = proof?.adoption_preview;
  return (
    <div className="duplicate-three-parts" data-group-id={group.id}>
      <section aria-label="重复资料来源">
        <h4>1. 来源</h4>
        <p>分别保留每次提取的位置、原页和图像语境。</p>
        <div className="rule-duplicate-members">
          {group.rules.map((rule: Row) => <div key={rule.id}>
            <label><input type="radio" name={`duplicate-canonical-${group.id}`} aria-label={`保留${stateLabels[rule.state]}条款，${rule.source_file || "用户新增规则"}第${rule.page_number || "?"}页`} checked={canonical === rule.id} disabled={!!busy || checking} onChange={() => setCanonical(rule.id)} />
              <span>{adoptionStateLabel(rule.state)} · {reviewPurposeLabel(rule.comparison_context?.purpose, rule.modality)} · {scopeLabels[rule.scope] || rule.scope}</span></label>
            <p>{rule.statement}</p>
            <div className="actions">
              {rule.source_id ? <button className="text-button" onClick={() => onSource(rule)}>{rule.source_file} · 第{rule.page_number || "?"}页</button> : <small>用户新增记录</small>}
              <button className="text-button" onClick={() => onEdit(rule)}>核对并编辑此条</button>
            </div>
          </div>)}
        </div>
        {proof && <details className="rule-advanced"><summary>完整来源清单（{proof.sources.length} 条原始记录／{proof.adoption_preview.distinct_source_occurrences} 个来源位置）</summary>
          {proof.sources.map((source: Row, index: number) => <div key={`${source.rule_id}:${index}`}>
            <p>{source.file_name || "用户新增记录"} · {source.page ? `第${source.page}页` : "无固定页码"}{source.historical_merged_origin ? " · 经历史归并保留" : ""}</p>
            <p>{source.full_scope ? "条款来源范围可核对" : "来源范围仍需核对"}{source.locator?.grade ? ` · ${source.locator.grade}级` : ""}{source.locator?.score ? ` · ${source.locator.score}` : ""}</p>
            {source.issues.map((issue: string) => <p className="rule-warning" key={issue}>{issue}</p>)}
            {source.origin_changed && <p className="rule-warning">历史归并记录后来发生了改动，需要重新核对原关联。</p>}
            {source.historical_review_id && <DuplicateReviewRecord pid={pid} reviewId={source.historical_review_id} />}
            <details><summary>来源原文（{source.text?.length || 0}字）</summary><pre className="rule-source-text">{source.text}</pre></details>
            <button className="text-button" onClick={() => onSource(allRules.find(r => r.id === source.rule_id) || { id: source.rule_id, statement: source.text })}>打开这条原始记录</button>
          </div>)}
        </details>}
      </section>
      <section aria-label="重复资料审核">
        <h4>2. 审核</h4>
        <p>程序逐项检查来源版本、原句、用途、条件及既有审核依据；程序复核不代表人工逐字校验。</p>
        <button className="btn" disabled={!!busy || checking} onClick={() => void check()}>{checking ? "正在核对来源与审核依据…" : "复核当前来源与条款"}</button>
        {error && <p role="alert">{error}</p>}
        {proof && <div role="status"><p>{proof.ready ? "程序复核通过" : "复核未通过，继续保留独立记录"}</p>
          {proof.checks.map((item: Row) => <p key={item.key} className={item.passed ? "rule-ready" : "rule-warning"}>{item.passed ? "通过：" : "待核对："}{item.message}</p>)}
        </div>}
      </section>
      <section aria-label="重复资料采用">
        <h4>3. 采用</h4>
        {preview ? <p>保留所选记录：{adoptionStateLabel(preview.canonical_state_after)} · {adoptionPurposeLabel(proof!)}。归并 {preview.duplicate_ids.length} 条提取记录，保留 {preview.retained_rule_occurrences} 条原始记录、{preview.distinct_source_occurrences} 个来源位置。适用范围和条件保持原值。</p> : <p>复核通过后显示采用前结果。归并本身不新增生效要求。</p>}
        {comparisonOnly && <p>当前仅属于文字或来源重合；请先分别核对完整内容与用途。</p>}
        <button className="btn primary" disabled={!!busy || checking || !proof?.ready || comparisonOnly} onClick={() => void adopt()}>采用复核结果并归并</button>
        <p className="muted">来源、条款或审核依据发生变化时需要重新复核。采用结果及原始记录可以恢复。</p>
      </section>
    </div>
  );
}

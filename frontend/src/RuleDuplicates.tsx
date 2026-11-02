import { useState } from "react";
import { CopyCheck, RotateCcw } from "lucide-react";
import { send } from "./client";
import DuplicateGroupWorkflow from "./DuplicateGroupWorkflow";
import DuplicateReviewRecord from "./DuplicateReviewRecord";
type Row = Record<string, any>;

export default function RuleDuplicates({ pid, data, busy, run, onEdit, onSource, onScan }: {
  pid: string; data: Row; busy: string;
  run: (key: string, fn: () => Promise<any>, message: string) => Promise<any>;
  onEdit: (rule: Row) => void; onSource: (rule: Row) => void; onScan: () => Promise<void>;
}) {
  const [scanning, setScanning] = useState(false);
  const groups: Row[] = data.duplicates ?? [], history: Row[] = data.duplicate_merges ?? [], overlaps: Row[] = data.text_overlaps ?? [];
  return (
    <section className="panel mb rule-duplicates" id="rule-duplicates" aria-label="规则重复检查">
      <div className="section-heading">
        <h2><CopyCheck size={18} />重复资料：来源、审核、采用</h2>
        <button className="btn" disabled={!!busy || scanning} onClick={async () => {
          setScanning(true); try { await onScan(); } finally { setScanning(false); }
        }}>{scanning ? "正在检查…" : "重新检查重复"}</button>
      </div>
      <p className="muted mb">先确认每条来源和完整语境，再逐项复核，最后预览并采用归并结果。只有当前来源与审核依据均有效的记录可以归并。</p>
      <div className="rule-duplicate-summary" role="status">
        {groups.length ? <>发现 <strong>{groups.length}</strong> 组符合比较条件的重复候选，共 <strong>{data.duplicate_extra_count}</strong> 条额外提取记录。采用前仍须复核当前版本。</> : "当前没有符合来源和审核条件的重复候选。"}
      </div>
      <div className="rule-duplicate-groups">
        {groups.map(group => <article className="rule-duplicate-group" key={group.id}>
          <div className="rule-duplicate-title"><strong>{group.rules.length} 条重复候选 · {group.kind === "exact" ? "文字完全相同" : "版式规范化后一致"}</strong></div>
          <DuplicateGroupWorkflow pid={pid} group={group} allRules={data.rules} busy={busy} onEdit={onEdit} onSource={onSource} run={run} />
        </article>)}
      </div>
      {overlaps.length > 0 && <details className="rule-advanced">
        <summary>文字或来源重合，需保留独立对象（{overlaps.length} 组）</summary>
        <p>共用文字、标题或图像尚不足以支持归并。来源和审核问题通过后，重新检查分组。</p>
        {overlaps.map(group => <details key={group.id} className="rule-duplicate-group">
          <summary>{group.rules.length} 条相关记录 · 保留各自来源</summary>
          {group.reasons.map((reason: string) => <p key={reason} className="rule-warning">{reason}</p>)}
          <DuplicateGroupWorkflow pid={pid} group={group} allRules={data.rules} busy={busy} onEdit={onEdit} onSource={onSource} run={run} comparisonOnly />
        </details>)}
      </details>}
      {history.length > 0 && <details className="rule-advanced">
        <summary>最近采用与恢复记录（{history.length}）</summary>
        {history.map(item => <div className="rule-merge-history" key={item.id}>
          <div className="grow"><p>{item.canonical_statement}</p>
            <small>{item.count} 条 · {new Date(item.created_at).toLocaleString()} · {item.restored ? "已恢复" : "已归并，原始记录保留"}</small>
            <p className="muted">{item.review_id ? `程序复核记录已保存，保留 ${item.retained_occurrences} 条原始记录、${item.retained_source_positions} 个来源位置` : "历史归并记录，未记录本轮逐项复核"}</p>
            {item.review_id && <DuplicateReviewRecord pid={pid} reviewId={item.review_id} />}
          </div>
          {!item.restored && <button className="btn" disabled={!!busy} onClick={() => run(`restore-${item.id}`, () => send(`/projects/${pid}/rules/duplicates/${item.id}/restore`, {}), "已恢复归并前的记录；再次采用需要重新复核")}><RotateCcw size={13} />恢复本次归并</button>}
        </div>)}
      </details>}
    </section>
  );
}

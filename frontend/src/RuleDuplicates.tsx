import { useState } from "react";
import { ArrowUpRight, CopyCheck, RotateCcw } from "lucide-react";
import { send } from "./client";
import { scopeLabels, stateLabels } from "./RuleEditor";
type Row = Record<string, any>;
export default function RuleDuplicates({
  pid,
  data,
  busy,
  run,
  onEdit,
  onSource,
  onScan,
}: {
  pid: string;
  data: Row;
  busy: string;
  run: (key: string, fn: () => Promise<any>, message: string) => Promise<any>;
  onEdit: (rule: Row) => void;
  onSource: (rule: Row) => void;
  onScan: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [scanning, setScanning] = useState(false);
  const groups: Row[] = data.duplicates ?? [];
  const history: Row[] = data.duplicate_merges ?? [];
  return (
    <section
      className="panel mb rule-duplicates"
      id="rule-duplicates"
      aria-label="规则重复检查"
    >
      <div className="section-heading">
        <h2>
          <CopyCheck size={18} />
          先检查重复条款
        </h2>
        <button
          className="btn"
          disabled={!!busy || scanning}
          onClick={async () => {
            setScanning(true);
            try {
              await onScan();
            } finally {
              setScanning(false);
            }
          }}
        >
          {scanning ? "正在检查…" : "重新检查重复"}
        </button>
      </div>
      <p className="muted mb">
        已按条款内容、适用范围和执行配置自动查重。数值不同、附加要求不同的条款保留为差异，不会直接归并。
      </p>
      <div className="rule-duplicate-summary" role="status">
        {groups.length ? (
          <>
            发现 <strong>{groups.length}</strong> 组重复，共{" "}
            <strong>{data.duplicate_extra_count}</strong>{" "}
            条多余记录。先选定保留条款，再逐组归并；已确认条款优先保留。
          </>
        ) : (
          "当前未发现内容与执行配置相同的重复条款。"
        )}
      </div>
      {groups.length > 0 && (
        <div className="rule-duplicate-groups">
          {groups.map((group) => {
            const canonical = group.rules.some(
              (r: Row) => r.id === selected[group.id],
            )
              ? selected[group.id]
              : group.canonical_id;
            return (
              <article className="rule-duplicate-group" key={group.id}>
                <div className="rule-duplicate-title">
                  <strong>
                    {group.rules.length} 条重复 ·{" "}
                    {group.kind === "exact" ? "文字完全相同" : "仅版式不同"}
                  </strong>
                  <small>
                    {scopeLabels[group.rules[0].scope] ?? group.rules[0].scope}
                  </small>
                </div>
                <p>{group.rules[0].statement}</p>
                {group.review_difference && (
                  <p className="rule-warning">
                    确认状态或性质不同。归并保留所选条款的状态、性质和执行配置。
                  </p>
                )}
                <div className="rule-duplicate-members">
                  {group.rules.map((r: Row) => (
                    <div key={r.id}>
                      <label>
                        <input
                          type="radio"
                          name={"canonical-" + group.id}
                          aria-label={
                            "保留" +
                            stateLabels[r.state] +
                            "条款，" +
                            (r.source_file ?? "用户新增规则") +
                            (r.page_number ? "第" + r.page_number + "页" : "")
                          }
                          value={r.id}
                          checked={canonical === r.id}
                          onChange={() =>
                            setSelected((v) => ({ ...v, [group.id]: r.id }))
                          }
                        />
                        <span>
                          {stateLabels[r.state]} · {stateLabels[r.modality]}
                          {canonical === r.id ? " · 保留此条" : ""}
                        </span>
                      </label>
                      <div className="rule-duplicate-source">
                        {r.source_id ? (
                          <button
                            className="text-button"
                            onClick={() => onSource(r)}
                          >
                            {r.source_file ?? "来源文件"} · 第
                            {r.page_number ?? "?"}页<ArrowUpRight size={12} />
                          </button>
                        ) : (
                          <small>用户新增规则</small>
                        )}
                        <button
                          className="text-button"
                          onClick={() => onEdit(r)}
                        >
                          核对条款
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <button
                  className="btn primary"
                  disabled={!!busy}
                  onClick={() =>
                    run(
                      "merge-" + group.id,
                      () =>
                        send("/projects/" + pid + "/rules/duplicates/merge", {
                          canonical_id: canonical,
                          duplicate_ids: group.rules
                            .filter((r: Row) => r.id !== canonical)
                            .map((r: Row) => r.id),
                        }),
                      "重复条款已归并，原始记录与全部出处保留，可在归并记录中恢复",
                    )
                  }
                >
                  {busy === "merge-" + group.id
                    ? "正在归并…"
                    : "保留所选，归并其余 " + (group.rules.length - 1) + " 条"}
                </button>
              </article>
            );
          })}
        </div>
      )}
      {history.length > 0 && (
        <details className="rule-advanced">
          <summary>最近归并记录（{history.length}）</summary>
          {history.map((item) => (
            <div className="rule-merge-history" key={item.id}>
              <div className="grow">
                <p>{item.canonical_statement}</p>
                <small>
                  {item.count} 条 · {new Date(item.created_at).toLocaleString()}{" "}
                  · {item.restored ? "已恢复" : "出处与原始记录已保留"}
                </small>
              </div>
              {!item.restored && (
                <button
                  className="btn"
                  disabled={!!busy}
                  onClick={() =>
                    run(
                      "restore-" + item.id,
                      () =>
                        send(
                          "/projects/" +
                            pid +
                            "/rules/duplicates/" +
                            item.id +
                            "/restore",
                          {},
                        ),
                      "已恢复归并前的条款状态与采用依据",
                    )
                  }
                >
                  <RotateCcw size={13} />
                  恢复本次归并
                </button>
              )}
            </div>
          ))}
        </details>
      )}
    </section>
  );
}

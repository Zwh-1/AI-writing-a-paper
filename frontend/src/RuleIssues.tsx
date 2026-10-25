import { CheckCircle2 } from "lucide-react";
import RuleDuplicates from "./RuleDuplicates";
import { checkerLabels, scopeLabels, stateLabels } from "./RuleEditor";
import { send } from "./client";
type Row = Record<string, any>;
export default function RuleIssues({
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
  return (
    <>
      <div className="rules-section-intro">
        <h2>逐组核对重复与冲突</h2>
        <p>保留适用的条款及全部出处，不同数值与条件分别处理。</p>
      </div>
      <RuleDuplicates
        pid={pid}
        data={data}
        busy={busy}
        run={run}
        onEdit={onEdit}
        onSource={onSource}
        onScan={onScan}
      />
      <section
        className="panel mb rule-conflicts"
        id="rule-conflicts"
        aria-label="规则冲突"
      >
        <div className="section-heading">
          <h2>不同取值与冲突处理</h2>
          <span>{data.conflicts.length} 组需核对</span>
        </div>
        {data.conflicts.length > 0 ? (
          <>
            <p className="muted mb">
              对照出处，编辑正确取值或排除不适用条款；仅填写采用依据不会解除强制规则冲突。
            </p>
            {data.conflicts.map((group: Row) => (
              <details
                className="rule-conflict-group"
                key={group.key + group.scope}
                open={data.conflicts.length === 1}
              >
                <summary>
                  <strong>
                    {scopeLabels[group.scope] ?? group.scope} ·{" "}
                    {checkerLabels[group.key] ??
                      (group.key === "word_count" ? "字数要求" : group.key)}
                  </strong>
                  <span>{group.rules.length} 条不同要求</span>
                </summary>
                {group.rules.map((rule: Row) => (
                  <div className="rule-conflict-row" key={rule.id}>
                    <div className="grow">
                      <p>{rule.statement}</p>
                      {rule.duplicate_count > 1 && (
                        <small>
                          相同条款 {rule.duplicate_count}{" "}
                          条，已合并展示；可先归并重复记录。
                        </small>
                      )}
                      <small>
                        {stateLabels[rule.state]} · {stateLabels[rule.modality]}{" "}
                        · 参数 {rule.value}
                      </small>
                    </div>
                    <div className="actions">
                      <button
                        className="btn"
                        disabled={!!busy}
                        onClick={() =>
                          run(
                            "resolve-conflict",
                            () =>
                              send(`/projects/${pid}/rules/resolve-conflict`, {
                                rule_id: rule.id,
                              }),
                            "已采用所选条款并排除组内其他冲突要求",
                          )
                        }
                      >
                        采用此条并解决整组
                      </button>
                      <button
                        className="btn"
                        onClick={() =>
                          onEdit(
                            data.rules.find(
                              (item: Row) => item.id === rule.id,
                            ) ?? rule,
                          )
                        }
                      >
                        核对此条
                      </button>
                    </div>
                  </div>
                ))}
              </details>
            ))}
          </>
        ) : (
          <p className="rule-ready">
            <CheckCircle2 size={16} />
            当前没有需要处理的取值冲突。
          </p>
        )}
      </section>
    </>
  );
}

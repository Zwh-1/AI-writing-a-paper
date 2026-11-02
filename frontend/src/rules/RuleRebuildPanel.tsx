import { useEffect, useRef, useState } from "react";
import { api, send } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import { useSourceBillingGate } from "../workspace/SourceWorkspace";
import type { Row } from "../workspace/types";

export default function RuleRebuildPanel({
  pid,
  tick,
  onCompleted,
  onSnapshot,
}: {
  pid: string;
  tick: number;
  onCompleted: () => void;
  onSnapshot: (value: Row) => void;
}) {
  const base = `/projects/${pid}/rules/rebuild`;
  const gate = useSourceBillingGate();
  const state = useWorkspaceResource<Row>(base, tick),
    history = useWorkspaceResource<Row[]>(base + "/history", tick);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const completed = useRef("");
  const callback = useRef(onCompleted);
  callback.current = onCompleted;
  const task = state.data?.task,
    result = state.data?.result,
    generation = state.data?.generation,
    scopeReview = state.data?.scope_review;
  const running = task && ["queued", "running"].includes(task.status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void state.load(), 5000);
    return () => clearInterval(timer);
  }, [running, state.load]);
  useEffect(() => {
    if (task?.status === "succeeded" && completed.current !== task.id) {
      completed.current = task.id;
      callback.current();
      void history.load();
    }
  }, [task?.id, task?.status, history.load]);
  const start = async (resume = false) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await send(base, {
        auto_publish: true,
        ...(resume ? { resume_task_id: task?.id } : {}),
      });
      await state.load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel" aria-label="按原件重新生成规则">
      <div className="section-heading">
        <div>
          <h2>按原件重新生成规则</h2>
          <p>
            重新阅读全部规范资料的完整原页、教学单元与评分条件。全部批次完成后切换新代次，旧规则及规则包快照保留。
          </p>
        </div>
      </div>
      <p className="muted">
        使用已配置的模型并计入项目预算。处理过程中继续保留当前生效快照；读取覆盖不代表人工语义审核通过。
      </p>
      {(error || state.error) && <p role="alert">{error || state.error}</p>}
      {state.data?.current_package && (
        <p aria-label="当前生效规则包">
          当前生效规则包 V{state.data.current_package.version} ·{" "}
          {state.data.current_package.rule_count} 条建议与要求
        </p>
      )}
      {scopeReview && scopeReview.reviewed_records > 0 && (
        <p>
          本轮适用范围检查覆盖 {scopeReview.reviewed_records} 条 · 补入建议{" "}
          {scopeReview.added_guidance} 条 · 移出生效包{" "}
          {scopeReview.removed_from_package} 条。
        </p>
      )}
      <div className="actions">
        <button
          className="btn"
          disabled={busy || running || gate.blocked}
          onClick={() => void start()}
        >
          从原件重新生成全部规则（计费）
        </button>
        {task &&
          ["incomplete", "budget_blocked", "failed"].includes(task.status) && (
            <button
              className="btn"
              disabled={busy || gate.blocked}
              onClick={() => void start(true)}
            >
              续接已保存批次
            </button>
          )}
        <button className="btn" onClick={() => void state.load()}>
          刷新重建进度
        </button>
      </div>
      {task && (
        <div role="status">
          <p>{task.phase || task.status}</p>
          <p>
            上次整批重建已处理 {result?.processed_batches ?? 0}/
            {result?.total_batches ?? "—"} 批 · 规范资料{" "}
            {result?.total_files ?? "—"} 份
          </p>
          {generation?.rule_package && (
            <p>
              该次重建生成规则包 V{generation.rule_package.version}，
              {generation.rule_package.rule_count} 条生效要求；保留{" "}
              {generation.archived_rule_count} 条历史记录。
            </p>
          )}
          {result?.publication_blockers?.map((v: Row, i: number) => (
            <p key={i}>{v.message}</p>
          ))}
          {result?.unresolved_batches?.map((v: Row, i: number) => (
            <p key={i}>
              {v.name}：{v.reason}
            </p>
          ))}
        </div>
      )}
      <details>
        <summary>历史规则包（{history.data?.length ?? 0}）</summary>
        <div className="actions">
          {history.data?.map((item) => (
            <button
              className="btn"
              key={item.id}
              onClick={() =>
                void api(base + "/history/" + item.id)
                  .then(onSnapshot)
                  .catch((e) => setError(e.message))
              }
            >
              查看 V{item.version} · {item.rule_count} 条
            </button>
          ))}
        </div>
      </details>
    </section>
  );
}

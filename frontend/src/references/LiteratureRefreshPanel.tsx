import { useEffect, useRef, useState } from "react";
import { api, send } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row } from "../workspace/types";
import { useSourceBillingGate } from "../workspace/SourceWorkspace";

export default function LiteratureRefreshPanel({
  pid,
  tick,
  onSummary,
  onCompleted,
}: {
  pid: string;
  tick: number;
  onSummary: (file: Row) => void;
  onCompleted: () => void;
}) {
  const gate = useSourceBillingGate();
  const base = `/projects/${pid}/literature-refresh`,
    state = useWorkspaceResource<Row>(base, tick);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const callback = useRef(onCompleted);
  callback.current = onCompleted;
  const completed = useRef("");
  const task = state.data?.task,
    running = task && ["queued", "running"].includes(task.status);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void state.load(), 5000);
    return () => clearInterval(timer);
  }, [running, state.load]);
  useEffect(() => {
    if (task?.status === "succeeded" && completed.current !== task.id) {
      completed.current = task.id;
      callback.current();
    }
  }, [task?.id, task?.status]);
  const start = async (resume = false) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await send(base, resume ? { resume_task_id: task?.id } : {});
      await state.load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="panel literature-refresh-panel"
      aria-label="文献内容更新"
    >
      <div className="section-heading">
        <div>
          <h2>文献内容更新</h2>
          <p>
            逐篇重新读取原文，更新研究问题、方法、适用条件、结果与局限。总结用于导读，具体论断回到原页核对。
          </p>
        </div>
      </div>
      <p role="status">
        本轮已更新{" "}
        <strong>
          {state.data?.updated ?? 0}/{state.data?.total ?? "—"}
        </strong>{" "}
        篇内容总结{running ? " · 正在生成" : ""}。此处统计实际内容版本。
      </p>
      {task && <p className="muted">{task.phase}</p>}
      {(error || state.error) && <p role="alert">{error || state.error}</p>}
      <div className="actions">
        <button
          className="btn"
          disabled={busy || running || gate.blocked}
          onClick={() => void start()}
        >
          重新生成全部文献总结（计费）
        </button>
        {task &&
          ["incomplete", "budget_blocked", "failed"].includes(task.status) && (
            <button
              className="btn"
              disabled={busy || gate.blocked}
              onClick={() => void start(true)}
            >
              续接文献更新
            </button>
          )}
        <button className="btn" onClick={() => void state.load()}>
          刷新内容进度
        </button>
      </div>
      <p className="muted">
        使用已配置的独立资料总结接口，受项目可用预算约束；已保存内容可逐篇查看，旧总结保留历史备份。
      </p>
      <details>
        <summary>逐篇内容与更新时间</summary>
        <div className="literature-refresh-files">
          {state.data?.files.map((file: Row) => (
            <article key={file.file_id}>
              <div>
                <strong>{file.name}</strong>
                <p>
                  {file.updated ? "本轮内容已更新" : "等待本轮内容更新"} ·{" "}
                  {file.summary_at
                    ? new Date(file.summary_at).toLocaleString("zh-CN")
                    : "尚无总结"}
                </p>
              </div>
              <button
                className="btn"
                onClick={() => onSummary({ ...file, id: file.file_id })}
              >
                查看文献总结
              </button>
            </article>
          ))}
        </div>
      </details>
      {state.data?.result?.unresolved?.map((item: Row) => (
        <p role="alert" key={item.file_id}>
          {item.name}：{item.reason}
        </p>
      ))}
    </section>
  );
}

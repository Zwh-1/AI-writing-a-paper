import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle } from "lucide-react";
import { api, send } from "./client";

type Row = Record<string, any>;
type Props = {
  pid: string;
  tick: number;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
  onRefresh?: () => void;
};

export default function ProjectOriginalReview({
  pid,
  tick,
  act,
  onRefresh,
}: Props) {
  const [state, setState] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const version = useRef(0);
  const inFlight = useRef(false);
  const path = "/projects/" + pid + "/materials/review";

  useEffect(() => {
    let active = true;
    const current = ++version.current;
    api(path)
      .then((result) => {
        if (active && current === version.current) setState(result);
      })
      .catch((failure) => {
        if (active && current === version.current) setError(failure.message);
      });
    return () => {
      active = false;
    };
  }, [path, tick]);

  const confirm = async () => {
    if (inFlight.current || !state?.can_confirm) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await act(async () => {
        try {
          return await send(path, { confirm: true, snapshot: state.snapshot });
        } catch (failure) {
          setError((failure as Error).message);
          throw failure;
        }
      }, "当前项目全部文件原文核对已完成；AI 总结仍需另行审阅");
      if (result) {
        ++version.current;
        setState(result);
      } else {
        const current = ++version.current;
        const updated = await api(path);
        if (current === version.current) setState(updated);
        onRefresh?.();
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <div
      className="project-original-review"
      data-testid="project-original-review"
      aria-label="当前项目全部文件原文核对"
    >
      <div className="project-original-review-heading">
        <div>
          <strong>全部文件原文核对</strong>
          {state && (
            <p>
              当前项目 {state.file_count} 份在用资料 · 已核对{" "}
              {state.reviewed_pages}/{state.total_pages} 页 · 已排除{" "}
              {state.excluded_pages} 页
            </p>
          )}
        </div>
        {state?.complete ? (
          <span className="badge green project-review-complete" role="status">
            <Check size={15} />
            当前项目全部文件原文已核对
          </span>
        ) : (
          <button
            type="button"
            className="btn primary"
            disabled={busy || !state?.can_confirm}
            onClick={confirm}
          >
            {busy ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <Check size={15} />
            )}
            {busy ? "正在保存全部文件核对…" : "确认当前项目全部文件原文已核对"}
          </button>
        )}
      </div>
      {state && !state.complete && (
        <p className="project-original-review-caption">
          待确认 {state.pending_files} 份文件、{state.pending_pages}{" "}
          页原文；已排除页面保持原状态。此操作仅记录原文核对，不代表 AI
          总结已审核。
        </p>
      )}
      {!!state?.blocked_files.length && (
        <details>
          <summary>
            {state.blocked_files.length}{" "}
            份资料尚未就绪，请先处理后再确认全部文件
          </summary>
          <ul>
            {state.blocked_files.map((file: Row) => (
              <li key={file.file_id}>
                {file.name}：{file.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!state && !error && (
        <p className="muted" role="status">
          正在读取本次核对的文件与页数…
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

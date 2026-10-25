import { useRef, useState } from "react";
import { LoaderCircle, Upload } from "lucide-react";
import { api } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { materialRoles, type WorkspaceAction } from "../workspace/types";

export default function MaterialUploadDialog({
  pid,
  act,
  onClose,
}: {
  pid: string;
  act: WorkspaceAction;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const lock = useRef(false);
  return (
    <WorkspaceDialog title="添加项目资料" onClose={onClose} busy={busy}>
      <form
        className="material-upload-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (lock.current) return;
          lock.current = true;
          setBusy(true);
          setError("");
          const form = new FormData(event.currentTarget);
          try {
            const result = await act(
              () =>
                api(`/projects/${pid}/files`, { method: "POST", body: form }),
              "文件已保存，正在后台解析",
            );
            if (result) onClose();
            else setError("文件尚未导入，请检查提示后重试。");
          } finally {
            lock.current = false;
            setBusy(false);
          }
        }}
      >
        <label className="material-upload-file">
          <Upload size={27} />
          <strong>选择需要整理的原件</strong>
          <span>导入后解析原文，AI 按托管设置继续总结和归类。</span>
          <input
            aria-label="选择文件"
            type="file"
            name="file"
            required
            accept=".pptx,.pdf,.docx,.txt,.md,.csv,.xlsx,.bib,.ris"
            disabled={busy}
          />
        </label>
        <details className="workspace-details">
          <summary>
            <strong>用途与使用权限</strong><span>按需调整</span>
          </summary>
          <label className="field">
            <span>资料用途</span>
            <select name="role" defaultValue="rules" disabled={busy}>
              {Object.entries(materialRoles).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>模型使用范围</span>
            <select
              name="egress"
              defaultValue="selected_content"
              disabled={busy}
            >
              <option value="selected_content">允许按任务发送选定片段</option>
              <option value="local_only">仅在本地使用</option>
              <option value="allowed">允许按任务使用</option>
            </select>
          </label>
        </details>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle className="spin" size={15} />
            ) : (
              <Upload size={15} />
            )}{" "}
            {busy ? "正在导入…" : "导入并解析"}
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

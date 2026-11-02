import { useEffect, useState } from "react";
import { send } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row } from "../workspace/types";
import { kindLabels, usageLabels } from "./documentReadingLabels";

export default function MaterialUsagesDialog({
  pid,
  onClose,
  initialFileId,
}: {
  pid: string;
  onClose: () => void;
  initialFileId?: string;
}) {
  const base = `/projects/${pid}/document-reading`;
  const [tick, setTick] = useState(0),
    [selected, setSelected] = useState<Row | null>(null),
    [values, setValues] = useState<string[]>([]),
    [kind, setKind] = useState("unclassified"),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  const files = useWorkspaceResource<Row[]>(`${base}/materials`, tick);
  useEffect(() => {
    if (!initialFileId || selected || !files.data) return;
    const item = files.data.find((f) => f.file_id === initialFileId);
    if (item) {
      setSelected(item);
      setValues(item.usages || []);
      setKind(item.source_kind || "unclassified");
    }
  }, [initialFileId, files.data]);
  const primary = selected
    ? (
        {
          research: "research",
          example: "example",
          rules: "writing_guidance",
        } as Record<string, string>
      )[selected.primary_role]
    : undefined;
  return (
    <WorkspaceDialog
      title="资料类型与多用途"
      onClose={onClose}
      wide
      busy={busy}
    >
      <p>
        同一原件可同时用于研究引用和写作示例，不复制原件、书目或来源编号。资料类型与用途不代表内容已经核对。
      </p>
      {(error || files.error) && <p role="alert">{error || files.error}</p>}
      {saved && (
        <p role="status">
          用途已保存。原件和历史引用保留，相关依据需按本次用途重新核对。
        </p>
      )}
      <label>
        选择原件
        <select
          aria-label="多用途原件"
          value={selected?.file_id || ""}
          onChange={(e) => {
            const item =
              files.data?.find((f) => f.file_id === e.target.value) || null;
            setSelected(item);
            setValues(item?.usages || []);
            setKind(item?.source_kind || "unclassified");
            setNote("");
            setSaved(false);
          }}
        >
          <option value="">请选择</option>
          {files.data
            ?.filter((f) =>
              [
                ".pdf",
                ".docx",
                ".pptx",
                ".txt",
                ".md",
                ".csv",
                ".xlsx",
              ].includes(f.extension),
            )
            .map((f) => (
              <option key={f.file_id} value={f.file_id}>
                {f.name}
              </option>
            ))}
        </select>
      </label>
      {selected && (
        <div className="dr-assessment">
          <label>
            资料类型
            <select
              aria-label="资料类型"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {Object.entries(kindLabels).map(([key, text]) => (
                <option key={key} value={key}>
                  {text}
                </option>
              ))}
            </select>
          </label>
          <fieldset>
            <legend>本项目中的用途</legend>
            {Object.entries(usageLabels).map(([key, text]) => (
              <label className="dr-check" key={key}>
                <input
                  type="checkbox"
                  checked={values.includes(key)}
                  disabled={key === primary}
                  onChange={(e) =>
                    setValues((old) =>
                      e.target.checked
                        ? [...new Set([...old, key])]
                        : old.filter((v) => v !== key),
                    )
                  }
                />
                {text}
                {key === primary ? "（保留主用途）" : ""}
              </label>
            ))}
          </fieldset>
          <label>
            设置依据
            <textarea
              aria-label="用途调整说明"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="说明资料的真实类型及用途，课程正反例不能证明项目成果"
            />
          </label>
          <button
            className="btn primary"
            disabled={busy || !note.trim()}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await send(
                  `${base}/documents/${selected.file_id}/usages`,
                  { usages: values, source_kind: kind, note },
                  "PUT",
                );
                setTick((v) => v + 1);
                setNote("");
                setSaved(true);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            保存资料类型与用途
          </button>
        </div>
      )}
    </WorkspaceDialog>
  );
}

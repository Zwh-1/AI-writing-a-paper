import { useEffect, useState } from "react";
import { api } from "../client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import type { Row } from "../workspace/types";
import ReadingDialog from "./DocumentReadingDialog";
import MaterialUsagesDialog from "./MaterialUsagesDialog";
import {
  kindLabels,
  usageLabels,
  semanticLabels,
} from "./documentReadingLabels";
import "./documentReading.css";

export default function DocumentReadingPanel({
  pid,
  tick,
  onSource,
  onChanged,
}: {
  pid: string;
  tick: number;
  onSource: (file: Row, page: number) => void;
  onChanged?: () => void;
}) {
  const base = `/projects/${pid}/document-reading`;
  const [intent, setIntent] = useState("research"),
    [query, setQuery] = useState(""),
    [refresh, setRefresh] = useState(0),
    [selected, setSelected] = useState<Row | null>(null),
    [reading, setReading] = useState<Row | null>(null),
    [editing, setEditing] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const catalog = useWorkspaceResource<Row>(
    `${base}/documents?intent=${intent}&query=${encodeURIComponent(query)}`,
    tick + refresh,
  );
  const sessions = useWorkspaceResource<Row[]>(
    `${base}/sessions`,
    tick + refresh,
  );
  const sections = useWorkspaceResource<Row[]>(
    `/projects/${pid}/sections`,
    tick,
  );
  useEffect(() => {
    setSelected(null);
    setReading(null);
    setQuery("");
  }, [pid, intent]);
  const reload = () => {
    setRefresh((v) => v + 1);
    onChanged?.();
  };
  const resume = async (item: Row) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await api(`${base}/sessions/${item.id}`);
      setReading(value);
      setSelected({
        file_id: item.file_id,
        name: item.name,
        extension: "",
        material_use: item.material_use,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel dr-panel" aria-label="文献与资料阅读">
      <div className="dr-heading">
        <div>
          <h2>文献与资料阅读</h2>
          <p>先选择原件，围绕问题阅读，再核对论断。检索命中只用于定位。</p>
        </div>
        <button className="btn" onClick={() => setEditing(true)}>
          资料类型与多用途
        </button>
      </div>
      <div className="actions" role="group" aria-label="阅读用途">
        <button
          className={`btn ${intent === "research" ? "primary" : ""}`}
          aria-pressed={intent === "research"}
          onClick={() => setIntent("research")}
        >
          研究文献
        </button>
        <button
          className={`btn ${intent === "guidance" ? "primary" : ""}`}
          aria-pressed={intent === "guidance"}
          onClick={() => setIntent("guidance")}
        >
          写作资料与示例
        </button>
      </div>
      <p className="dr-boundary">
        已读范围不等于语义依据充分。教学正例、反例和评分条件不能证明你的项目已经实现或测试通过。
      </p>
      <label className="dr-search">
        按题名或书目查找
        <input
          aria-label="查找阅读文献"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxLength={5000}
        />
      </label>
      {(error || catalog.error || sessions.error) && (
        <p role="alert" className="error-text">
          {error || catalog.error || sessions.error}
        </p>
      )}
      <div className="dr-documents">
        <p>
          当前用途共 {catalog.data?.total ?? "—"}{" "}
          份原件；同一原件可以有多种用途。
        </p>
        {catalog.data?.documents.map((file: Row) => (
          <article key={file.file_id}>
            <div>
              <h3>{file.references?.[0]?.metadata?.title || file.name}</h3>
              <p>{file.name}</p>
              <small>
                {kindLabels[file.material_use.source_kind]} · {file.pages}{" "}
                个原页或资料块 ·{" "}
                {file.usages.map((v: string) => usageLabels[v]).join("、")}
              </small>
            </div>
            <button
              className="btn"
              disabled={busy || file.status !== "ready"}
              onClick={() => {
                setSelected(file);
                setReading(null);
              }}
            >
              按问题阅读
            </button>
          </article>
        ))}
      </div>
      <details>
        <summary>已保存的阅读记录（{sessions.data?.length ?? 0}）</summary>
        {sessions.data?.map((item) => (
          <article className="dr-history" key={item.id}>
            <div>
              <strong>{item.name}</strong>
              <p>{item.question}</p>
              <small>
                {item.state === "stale"
                  ? "原文或任务已变化"
                  : semanticLabels[item.semantic_context_status]}
              </small>
            </div>
            <button
              className="btn"
              disabled={busy}
              onClick={() => void resume(item)}
            >
              继续阅读与核对
            </button>
          </article>
        ))}
      </details>
      {selected && (
        <ReadingDialog
          key={reading?.id || selected.file_id}
          pid={pid}
          file={selected}
          existing={reading}
          sections={sections.data ?? []}
          intent={reading?.intent || intent}
          onClose={() => {
            setSelected(null);
            setReading(null);
            reload();
          }}
          onSource={onSource}
        />
      )}
      {editing && (
        <MaterialUsagesDialog
          pid={pid}
          onClose={() => {
            setEditing(false);
            reload();
          }}
        />
      )}
    </section>
  );
}

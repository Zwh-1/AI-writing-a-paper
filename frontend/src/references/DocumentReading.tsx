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
import {
  ResearchColumns,
  ResearchEmpty,
  ReferenceInspector,
  useReferenceTitle,
} from "./ReferenceWorkbenchParts";
import { Search } from "lucide-react";

export default function DocumentReadingPanel({
  pid,
  tick,
  onSource,
  onChanged,
  initialFileId,
}: {
  pid: string;
  tick: number;
  onSource: (file: Row, page: number) => void;
  onChanged?: () => void;
  initialFileId?: string;
}) {
  const titleOf = useReferenceTitle();
  const base = `/projects/${pid}/document-reading`;
  const [intent, setIntent] = useState("research"),
    [query, setQuery] = useState(""),
    [refresh, setRefresh] = useState(0),
    [focused, setFocused] = useState<Row | null>(null),
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
    setFocused(null);
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
      setSelected(
        catalog.data?.documents.find(
          (file: Row) => file.file_id === item.file_id,
        ) || {
          file_id: item.file_id,
          name: item.name,
          extension: item.extension || "",
          material_use: item.material_use,
        },
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const documents: Row[] = (catalog.data?.documents ?? []).filter(
    (file: Row) => !initialFileId || file.file_id === initialFileId,
  );
  const active =
    selected ||
    documents.find((file) => file.file_id === focused?.file_id) ||
    documents[0];
  const history = (sessions.data ?? []).filter(
    (item) => !active || item.file_id === active.file_id,
  );
  return (
    <section className="dr-panel reading-workbench" aria-label="文献与资料阅读">
      {(error || catalog.error || sessions.error) && (
        <p role="alert" className="error-text">
          {error || catalog.error || sessions.error}
        </p>
      )}
      <ResearchColumns
        directory={
          <>
            <div className="research-directory-heading">
              <h2>阅读目录</h2>
              <span>{documents.length} 篇</span>
            </div>
            <div
              className="research-view-switch"
              role="group"
              aria-label="阅读用途"
            >
              <button
                className="btn"
                aria-pressed={intent === "research"}
                onClick={() => setIntent("research")}
              >
                研究文献
              </button>
              <button
                className="btn"
                aria-pressed={intent === "guidance"}
                onClick={() => setIntent("guidance")}
              >
                写作资料与示例
              </button>
            </div>
            <label className="research-search">
              <Search size={16} />
              <input
                aria-label="查找阅读文献"
                placeholder="按题名或书目查找"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                maxLength={5000}
              />
            </label>
            <div className="dr-documents">
              {documents.map((file) => (
                <article
                  key={file.file_id}
                  className={
                    active?.file_id === file.file_id ? "is-selected" : ""
                  }
                >
                  <button
                    className="research-directory-item"
                    aria-pressed={active?.file_id === file.file_id}
                    onClick={() => {
                      setFocused(file);
                      setSelected(null);
                      setReading(null);
                    }}
                  >
                    <small>
                      {kindLabels[file.material_use.source_kind]} · {file.pages}{" "}
                      页 / 块
                    </small>
                    <strong>
                      {file.references?.[0]?.metadata?.title || titleOf(file)}
                    </strong>
                    <span>
                      {file.usages
                        .map((v: string) => usageLabels[v])
                        .join(" · ")}
                    </span>
                  </button>
                </article>
              ))}
              {!documents.length && (
                <p className="research-directory-empty">
                  {catalog.loading ? "正在读取文献…" : "没有符合条件的原件。"}
                </p>
              )}
            </div>
            <button
              className="btn research-directory-footer"
              onClick={() => setEditing(true)}
            >
              资料类型与多用途
            </button>
          </>
        }
        inspector={
          <>
            <ReferenceInspector title="阅读记录">
              <p>问题、已读范围和核对记录会自动保存。</p>
              <details open className="reading-history">
                <summary>
                  已保存的阅读记录（
                  {sessions.loading && !sessions.data
                    ? "读取中"
                    : history.length}
                  ）
                </summary>
                {history.map((item) => (
                  <article className="dr-history" key={item.id}>
                    <strong>{item.question}</strong>
                    <small>
                      {item.state === "stale"
                        ? "原文或任务已变化"
                        : semanticLabels[item.semantic_context_status]}
                    </small>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() => void resume(item)}
                    >
                      继续阅读与核对
                    </button>
                  </article>
                ))}
                {!history.length && (
                  <p role={sessions.loading ? "status" : undefined}>
                    {sessions.loading
                      ? "正在读取阅读记录…"
                      : "当前文献尚无阅读记录。"}
                  </p>
                )}
              </details>
            </ReferenceInspector>
            <ReferenceInspector title="阅读顺序">
              <ol className="research-steps">
                <li>提出本次阅读的问题</li>
                <li>选择并读取原文范围</li>
                <li>记录方法、条件和局限</li>
                <li>核对引句与论断支持性</li>
              </ol>
            </ReferenceInspector>
            <ReferenceInspector title="原件信息">
              {active ? (
                <>
                  <p className="reference-filename">{active.name}</p>
                  <button
                    className="btn"
                    onClick={() =>
                      onSource({ ...active, id: active.file_id }, 1)
                    }
                  >
                    查看完整原件
                  </button>
                </>
              ) : (
                <p>先从左侧选择文献。</p>
              )}
            </ReferenceInspector>
          </>
        }
      >
        {selected ? (
          <ReadingDialog
            key={reading?.id || selected.file_id}
            pid={pid}
            file={selected}
            existing={reading}
            sections={sections.data ?? []}
            intent={reading?.intent || intent}
            embedded
            onSaved={() => void sessions.load()}
            onClose={() => {
              setSelected(null);
              setReading(null);
              reload();
            }}
            onSource={onSource}
          />
        ) : active ? (
          <article className="reference-document">
            <header className="reference-document-heading">
              <span className="research-kicker">问题阅读</span>
              <h2>
                {active.references?.[0]?.metadata?.title || titleOf(active)}
              </h2>
              <p>
                {kindLabels[active.material_use.source_kind]} · {active.pages}{" "}
                个原页或资料块
              </p>
            </header>
            <section className="reference-reading-section">
              <h3>这次阅读，你想弄清什么？</h3>
              <p>
                围绕一个具体问题阅读这篇文献，保留相关原文、适用条件和局限。
              </p>
              <div className="research-question-example">
                <small>问题示例</small>
                <p>
                  这项方法适用于什么场景，实验结果能说明什么，还有哪些限制？
                </p>
              </div>
              <button
                className="btn primary"
                disabled={busy || active.status !== "ready"}
                onClick={() => {
                  setSelected(active);
                  setReading(null);
                }}
              >
                按问题阅读
              </button>
            </section>
            {history.length > 0 && (
              <div className="research-next-step">
                <div>
                  <strong>已有 {history.length} 条阅读记录</strong>
                  <p>从右侧继续阅读，保留之前的问题、范围和核对记录。</p>
                </div>
              </div>
            )}
            <details className="research-advanced">
              <summary>阅读与证据的区别</summary>
              <p>
                已读范围说明你查看了哪些文字。一个论断是否得到支持，还需要核对引句、条件和局限。教学示例不能作为本项目已实现或测试通过的证明。
              </p>
            </details>
          </article>
        ) : (
          <ResearchEmpty title="选择一篇文献开始阅读">
            从左侧查找文献，或调整资料用途。
          </ResearchEmpty>
        )}
      </ResearchColumns>
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

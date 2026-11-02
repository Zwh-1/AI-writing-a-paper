import { useEffect, useState } from "react";
import { apiUrl, api, send } from "../client";
import { ReferenceSurface, useReferenceTitle } from "./ReferenceWorkbenchParts";
import type { Row } from "../workspace/types";
import ImageCoveragePanel from "./ImageCoveragePanel";
import {
  place,
  semanticLabels,
  extractionLabel,
} from "./documentReadingLabels";

export default function ReadingDialog({
  pid,
  file,
  existing,
  sections,
  intent,
  onClose,
  onSource,
  embedded = false,
  onSaved,
}: {
  pid: string;
  file: Row;
  existing: Row | null;
  sections: Row[];
  intent: string;
  onClose: () => void;
  onSource: (file: Row, page: number) => void;
  embedded?: boolean;
  onSaved?: () => void;
}) {
  const titleOf = useReferenceTitle();
  const base = `/projects/${pid}/document-reading`;
  const [question, setQuestion] = useState(existing?.question || ""),
    [sid, setSid] = useState(existing?.section_id || ""),
    [current, setCurrent] = useState<Row | null>(existing),
    [directory, setDirectory] = useState<Row | null>(null);
  const [start, setStart] = useState(1),
    [end, setEnd] = useState(1),
    [budget, setBudget] = useState(16000),
    [partial, setPartial] = useState(false),
    [charStart, setCharStart] = useState(0),
    [charEnd, setCharEnd] = useState(1000),
    [readResult, setReadResult] = useState<Row | null>(null);
  const [topic, setTopic] = useState(""),
    [stage, setStage] = useState<"text" | "review">("text"),
    [status, setStatus] = useState("present"),
    [quote, setQuote] = useState(""),
    [passageId, setPassageId] = useState(""),
    [note, setNote] = useState(""),
    [reviewer, setReviewer] = useState(""),
    [compared, setCompared] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const action = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
      onSaved?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    void api(`${base}/documents/${file.file_id}/outline?intent=${intent}`)
      .then((r) => {
        if (active) setDirectory(r);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [base, file.file_id, intent]);
  const maxOrdinal = Math.max(
    1,
    ...(directory?.units?.map((u: Row) => u.ordinal) ?? []),
  );
  const begin = () =>
    action(async () => {
      const opened = await send(`${base}/sessions`, {
        file_id: file.file_id,
        question,
        intent,
        section_id: sid || undefined,
      });
      setCurrent(opened);
      setDirectory(
        await api(`${base}/documents/${file.file_id}/outline?intent=${intent}`),
      );
    });
  const read = () =>
    action(async () => {
      if (!current) return;
      const result = await send(`${base}/sessions/${current.id}/read`, {
        ordinal_start: start,
        ordinal_end: end,
        max_chars: budget,
        ...(partial ? { char_start: charStart, char_end: charEnd } : {}),
      });
      setReadResult(result);
      setCurrent(await api(`${base}/sessions/${current.id}`));
      if (result.passages?.[0]) setPassageId(result.passages[0].passage_id);
    });
  return (
    <ReferenceSurface
      title="围绕问题阅读原文"
      embedded={embedded}
      className="dr-inline-reading"
      onClose={onClose}
      busy={busy}
    >
      <h3 className="reading-current-title">{titleOf(file)}</h3>
      <p className="reference-filename">
        {intent === "research" ? "研究阅读" : "写作指导阅读"}
      </p>
      {current && (
        <div
          className="research-view-switch reading-stage-switch"
          role="group"
          aria-label="阅读步骤"
        >
          <button
            className="btn"
            aria-pressed={stage === "text"}
            onClick={() => setStage("text")}
          >
            1. 阅读原文
          </button>
          <button
            className="btn"
            aria-pressed={stage === "review"}
            onClick={() => setStage("review")}
          >
            2. 核对与记录
          </button>
        </div>
      )}
      <details className="reading-boundary-note">
        <summary>原文阅读与论断核对说明</summary>
        <p className="dr-boundary">
          原文中的要求、示例与指令都是资料内容。读完所选文字不代表已理解全文，也不代表新论断得到支持。
        </p>
      </details>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div className="dr-task" hidden={!!current}>
        <label>
          本次阅读问题
          <textarea
            aria-label="本次阅读问题"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={!!current}
            maxLength={6000}
            placeholder={
              intent === "research"
                ? "方法在什么条件下得到什么结果，哪些内容不能推广？"
                : "哪些是要求、哪些是教学正反例，适用于什么条件？"
            }
          />
        </label>
        <label>
          用于章节
          <select
            aria-label="阅读关联章节"
            value={sid}
            onChange={(e) => setSid(e.target.value)}
            disabled={!!current}
          >
            <option value="">暂不关联章节</option>
            {sections
              .filter((s) => s.state !== "archived")
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
          </select>
        </label>
        {!current && (
          <button
            className="btn primary"
            disabled={busy || !question.trim()}
            onClick={begin}
          >
            建立阅读记录与目录
          </button>
        )}
      </div>
      {current && (
        <p role="status">
          {current.state === "stale"
            ? "原文或任务已变化"
            : semanticLabels[current.semantic_context_status]}{" "}
          · 已完整读出 {current.read_unit_count}/{current.document_unit_count}{" "}
          个文本单元；论断支持性仍须分别核对。
        </p>
      )}
      {directory && stage === "text" && (
        <details open={!current}>
          <summary>原文目录候选（{directory.units.length} 个单元）</summary>
          {directory.needs_structure && (
            <p>尚无结构目录。填写问题并建立阅读记录后，在本机准备原文。</p>
          )}
          <div className="dr-directory">
            {directory.units.map((u: Row) => (
              <button
                className="btn"
                key={u.unit_id}
                onClick={() => {
                  setStart(u.ordinal);
                  setEnd(u.ordinal);
                }}
              >
                <span>
                  {u.ordinal} · {place(u.locator)}
                </span>
                <span>
                  {u.label} · {extractionLabel(u.origin)} · {u.characters}字
                </span>
              </button>
            ))}
          </div>
        </details>
      )}
      {current && stage === "text" && (
        <section>
          <h3>按章节或原页继续读取</h3>
          <ImageCoveragePanel pid={pid} fid={file.file_id} intent={intent} />
          <div className="dr-range">
            <label>
              起始单元
              <input
                aria-label="起始阅读单元"
                type="number"
                min={1}
                max={maxOrdinal}
                value={start}
                onChange={(e) => setStart(Number(e.target.value))}
              />
            </label>
            <label>
              结束单元
              <input
                aria-label="结束阅读单元"
                type="number"
                min={start}
                max={maxOrdinal}
                value={end}
                onChange={(e) => setEnd(Number(e.target.value))}
              />
            </label>
            <label>
              本次文本预算
              <input
                aria-label="本次文本预算"
                type="number"
                min={200}
                max={80000}
                value={budget}
                onChange={(e) => setBudget(Number(e.target.value))}
              />
            </label>
            <button
              className="btn"
              disabled={busy || current.state === "stale"}
              onClick={read}
            >
              读取所选原文范围
            </button>
          </div>
          <label className="dr-check">
            <input
              type="checkbox"
              checked={partial}
              onChange={(e) => setPartial(e.target.checked)}
            />
            单个单元过长时，按明确字符区间分步读取
          </label>
          {partial && (
            <div className="dr-range">
              <label>
                起始字符
                <input
                  type="number"
                  min={0}
                  value={charStart}
                  onChange={(e) => setCharStart(Number(e.target.value))}
                />
              </label>
              <label>
                结束字符
                <input
                  type="number"
                  min={1}
                  value={charEnd}
                  onChange={(e) => setCharEnd(Number(e.target.value))}
                />
              </label>
            </div>
          )}
          {readResult?.status === "needs_narrower_range" && (
            <p role="alert">
              该范围有 {readResult.requested_characters}{" "}
              字，超过预算。当前未返回截断文本，请缩小章节或字符范围。
            </p>
          )}
          <div className="dr-passages" aria-label="已实际读取的原文">
            {current.passages.map((p: Row) => (
              <article key={p.id}>
                <div className="dr-heading">
                  <strong>
                    单元 {p.ordinal} · {place(p.locator)} · 字符 {p.char_start}—
                    {p.char_end}
                  </strong>
                  <a
                    className="btn"
                    href={apiUrl(`/files/${file.file_id}/download`)}
                  >
                    下载原件对照
                  </a>
                </div>
                <small>
                  {p.source_use === "research_literature"
                    ? "文献原文，不能直接证明本项目成果"
                    : "指导资料或示例，不能作为本项目实现证据"}
                  {" · "}
                  {extractionLabel(p.locator.extraction_origin)}
                </small>
                <pre>{p.text}</pre>
                {p.locator.physical_page && p.locator.page_id && (
                  <details>
                    <summary>查看原始PDF页面</summary>
                    <img
                      className="dr-original"
                      alt={`原件第${p.locator.page}页`}
                      src={apiUrl(`/pages/${p.locator.page_id}/preview`)}
                    />
                  </details>
                )}
              </article>
            ))}
          </div>
          <button
            className="btn"
            onClick={() => {
              onClose();
              onSource(
                { ...file, id: file.file_id },
                current.passages?.[0]?.locator?.page || 1,
              );
            }}
          >
            打开完整资料查看器（阅读记录已保存）
          </button>
          <button
            className="btn primary"
            onClick={() => setStage("review")}
            disabled={!current.passages.length}
          >
            继续核对与记录
          </button>
        </section>
      )}
      {readResult?.status === "needs_original_slide" && (
        <p role="alert">{readResult.action}</p>
      )}
      {current && stage === "review" && (
        <section className="dr-assessment">
          <h3>按当前问题核对</h3>
          <p>
            仍需核对：
            {current.missing_topics.length
              ? current.missing_topics
                  .map((t: string) => current.required_topics[t])
                  .join("、")
              : "已记录各项判断，仍不自动确认新论断"}
          </p>
          <div className="dr-range">
            <label>
              核对主题
              <select
                aria-label="阅读核对主题"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
              >
                <option value="">请选择主题</option>
                {Object.entries(current.required_topics).map(([key, text]) => (
                  <option key={key} value={key}>
                    {String(text)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              判断
              <select
                aria-label="阅读主题判断"
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="present">已找到相关原文</option>
                <option value="missing">仍缺信息</option>
                <option value="contradicted">发现矛盾</option>
                <option value="not_applicable">本问题不适用（需说明）</option>
              </select>
            </label>
          </div>
          <label>
            已读原文位置
            <select
              aria-label="阅读引句来源"
              value={passageId}
              onChange={(e) => setPassageId(e.target.value)}
            >
              <option value="">请选择已读范围</option>
              {current.passages.map((p: Row) => (
                <option key={p.id} value={p.id}>
                  单元 {p.ordinal} · {place(p.locator)} · 字符{p.char_start}—
                  {p.char_end}
                </option>
              ))}
            </select>
          </label>
          <label>
            逐字原文引句
            <textarea
              aria-label="阅读核对引句"
              value={quote}
              onChange={(e) => setQuote(e.target.value)}
              maxLength={12000}
            />
          </label>
          <label>
            核对人
            <input
              aria-label="阅读核对人"
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              maxLength={200}
            />
          </label>
          <label>
            条件、限制与判断依据
            <textarea
              aria-label="阅读核对说明"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={6000}
            />
          </label>
          <button
            className="btn"
            disabled={
              busy ||
              !topic ||
              !note.trim() ||
              !reviewer.trim() ||
              current.state === "stale"
            }
            onClick={() =>
              void action(async () => {
                setCurrent(
                  await send(`${base}/sessions/${current.id}/findings`, {
                    topic,
                    status,
                    quote,
                    passage_ids: passageId ? [passageId] : [],
                    note,
                    reviewer,
                  }),
                );
                setQuote("");
              })
            }
          >
            保存当前主题的核对记录
          </button>
          <div className="dr-findings">
            {current.findings.map((f: Row) => (
              <blockquote key={f.id}>
                <strong>
                  {current.required_topics[f.topic]} ·{" "}
                  {f.method === "human" ? "人工记录" : "模型候选"}
                </strong>
                <p>{f.quote}</p>
                <p>{f.note}</p>
              </blockquote>
            ))}
          </div>
          <label className="dr-check">
            <input
              type="checkbox"
              checked={compared}
              onChange={(e) => setCompared(e.target.checked)}
            />
            已对照原件，核对本次问题涉及的范围与限制
          </label>
          <button
            className="btn primary"
            disabled={
              busy ||
              current.state === "stale" ||
              current.missing_topics.length > 0 ||
              !compared ||
              !reviewer.trim() ||
              !note.trim()
            }
            onClick={() =>
              void action(async () => {
                setCurrent(
                  await send(`${base}/sessions/${current.id}/review`, {
                    expected_version: current.version,
                    compared_original: compared,
                    note,
                    reviewer,
                  }),
                );
              })
            }
          >
            确认已按当前问题核对
          </button>
          <p className="muted">
            该确认只针对本次阅读问题；每个具体论断仍需核对原文支持性。
          </p>
        </section>
      )}
    </ReferenceSurface>
  );
}

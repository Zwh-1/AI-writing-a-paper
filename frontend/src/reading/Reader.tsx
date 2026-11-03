import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUrl, send } from "../client";
import {
  base,
  Row,
  TaskStatus,
  useStudioTask,
  safeHtml,
} from "../research/shared";
import { StudioChat } from "../assistant/ChatNotes";
import { useWorkspaceLocation } from "../workspace/WorkspaceContext";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";

function MapView({
  tree,
  onJump,
}: {
  tree: Row;
  onJump: (id: string) => void;
}) {
  const nodes: Row[] = [],
    edges: Row[] = [];
  const visit = (node: Row, depth: number, parent?: Row) => {
    const item = { ...node, x: 20 + depth * 190, y: 28 + nodes.length * 46 };
    nodes.push(item);
    if (parent) edges.push({ a: parent, b: item });
    node.children?.forEach((child: Row) => visit(child, depth + 1, item));
  };
  visit(tree, 0);
  return (
    <div className="studio-map">
      <svg
        role="img"
        aria-label="论文章节与段落结构导图"
        width={Math.max(600, ...nodes.map((n) => n.x + 180))}
        height={Math.max(240, nodes.length * 46 + 30)}
      >
        {edges.map((e, i) => (
          <path
            key={i}
            d={`M${e.a.x + 170},${e.a.y} C${e.a.x + 185},${e.a.y} ${e.b.x - 20},${e.b.y} ${e.b.x},${e.b.y}`}
            fill="none"
            stroke="#9babb6"
          />
        ))}
        {nodes.map((node) => (
          <g
            key={node.id}
            role="button"
            tabIndex={0}
            onClick={() => node.unit_id && onJump(node.unit_id)}
            onKeyDown={(event) =>
              event.key === "Enter" && node.unit_id && onJump(node.unit_id)
            }
            transform={`translate(${node.x},${node.y - 15})`}
          >
            <rect
              width={170}
              height={31}
              rx={7}
              fill={node.type === "paragraph" ? "#f3f6f7" : "#dceee8"}
              stroke="#b7c6c1"
            />
            <text x={9} y={20} fontSize={12} fill="#263c35">
              {node.label.slice(0, 14)}
              {node.label.length > 14 ? "…" : ""}
            </text>
            <title>{node.label}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

export default function StudioReader({
  pid,
  files,
  initialFile = "",
  initialUnit = "",
  onAsk,
}: {
  pid: string;
  files: Row[];
  initialFile?: string;
  initialUnit?: string;
  onAsk?: (fid: string, text: string) => void;
}) {
  const workspace = useWorkspaceLocation(pid, "reader");
  const [fid, setFid] = useState(initialFile || files[0]?.id || ""),
    [language, setLanguage] = useState("zh-CN"),
    [view, setView] = useState<Row | null>(null),
    [tab, setTab] = useState("read");
  const [parallel, setParallel] = useState(true),
    [selection, setSelection] = useState<Row | null>(null),
    [note, setNote] = useState(""),
    [color, setColor] = useState("yellow"),
    [refs, setRefs] = useState<Row[]>([]),
    [returnTo, setReturnTo] = useState(""),
    [page, setPage] = useState<Row | null>(null),
    [instruction, setInstruction] = useState("");
  const area = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => {
    if (fid)
      setView(await api(base(pid) + `/reader/${fid}?language=${language}`));
  }, [pid, fid, language]);
  const task = useStudioTask(() => void load(), pid + ":reader:" + fid);
  useEffect(() => {
    if (!initialFile && workspace.location.fileId)
      setFid(workspace.location.fileId);
  }, [pid]);
  useEffect(() => {
    workspace.update({ fileId: fid });
  }, [fid]);
  useEffect(() => {
    if (initialUnit && view)
      setTimeout(
        () =>
          area.current
            ?.querySelector(`[data-reader-unit="${CSS.escape(initialUnit)}"]`)
            ?.scrollIntoView({ block: "center" }),
        30,
      );
  }, [initialUnit, view?.parse_id]);
  useEffect(() => {
    if (initialFile) setFid(initialFile);
  }, [initialFile]);
  useEffect(() => {
    void task.run(load);
  }, [load]);
  const jump = (id: string) => {
    setTab("read");
    setTimeout(
      () =>
        area.current
          ?.querySelector(`[data-reader-unit="${CSS.escape(id)}"]`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" }),
      30,
    );
    if (view)
      void send(
        base(pid) + `/reader/${fid}/progress`,
        { unit_id: id, completed_units: view.progress?.completed_units || [] },
        "PUT",
      );
  };
  const capture = () => {
    const selected = window.getSelection();
    if (!selected?.rangeCount || selected.isCollapsed) return;
    const range = selected.getRangeAt(0),
      origin =
        range.startContainer.nodeType === Node.ELEMENT_NODE
          ? (range.startContainer as Element)
          : range.startContainer.parentElement;
    const element = origin?.closest<HTMLElement>("[data-original-unit]");
    const end =
      range.endContainer.nodeType === Node.ELEMENT_NODE
        ? (range.endContainer as Element)
        : range.endContainer.parentElement;
    if (!element || end?.closest("[data-original-unit]") !== element) return;
    const unit = view?.units.find(
      (u: Row) => u.id === element.dataset.originalUnit,
    );
    if (!unit) return;
    const prefix = document.createRange();
    prefix.selectNodeContents(element);
    prefix.setEnd(range.startContainer, range.startOffset);
    const start = Array.from(prefix.toString()).length,
      stop = start + Array.from(range.toString()).length;
    if (Array.from(unit.text).slice(start, stop).join("") !== range.toString())
      return;
    setSelection({
      unit_id: unit.id,
      start,
      end: stop,
      quote: range.toString(),
      source_hash: unit.source_hash,
      parse_id: view?.parse_id,
    });
    workspace.update({
      fileId: fid,
      unitId: unit.id,
      parseId: view?.parse_id,
      selection: { start, end: stop, quote: range.toString() },
    });
  };
  const citationText = (text: string, uid: string, offset: number) => {
    const parts: any[] = [];
    const regex = /\[\d+(?:\s*[-–—,，]\s*\d+)*\]/g;
    let cursor = 0;
    for (const match of text.matchAll(regex)) {
      const numbers: number[] = [];
      match[0]
        .slice(1, -1)
        .split(/[,，]/)
        .forEach((part) => {
          const bounds = part.split(/[-–—]/).map(Number);
          if (
            bounds.length === 2 &&
            bounds[1] >= bounds[0] &&
            bounds[1] - bounds[0] < 100
          ) {
            for (let n = bounds[0]; n <= bounds[1]; n++) numbers.push(n);
          } else if (bounds.length === 1) numbers.push(bounds[0]);
        });
      const referenceItems =
        view?.references.filter((row: Row) => numbers.includes(row.number)) ||
        [];
      parts.push(text.slice(cursor, match.index));
      parts.push(
        <button
          className="studio-inline-reference"
          title={
            referenceItems.map((row: Row) => row.text).join("\n") ||
            "未找到可定位的参考条目"
          }
          key={`${offset}-${match.index}`}
          onClick={() => {
            setRefs(referenceItems);
            setReturnTo(uid);
          }}
        >
          {match[0]}
        </button>,
      );
      cursor = match.index! + match[0].length;
    }
    parts.push(text.slice(cursor));
    return parts;
  };
  const marked = (unit: Row) => {
    const annotations =
      view?.annotations.filter(
        (row: Row) => row.current && row.data.unit_id === unit.id,
      ) || [];
    const chars = Array.from(unit.text as string);
    const boundaries = [
      ...new Set([
        0,
        chars.length,
        ...annotations.flatMap((row: Row) => [row.data.start, row.data.end]),
      ]),
    ].sort((a: any, b: any) => a - b) as number[];
    return boundaries.slice(0, -1).map((start, index) => {
      const end = boundaries[index + 1],
        active = annotations.find(
          (row: Row) => row.data.start <= start && row.data.end >= end,
        ),
        text = citationText(chars.slice(start, end).join(""), unit.id, start);
      return active ? (
        <mark
          key={start}
          className={`studio-mark studio-${active.data.color}`}
          title={active.data.note}
        >
          {text}
        </mark>
      ) : (
        <span key={start}>{text}</span>
      );
    });
  };
  return (
    <section className="studio-reader">
      <div className="studio-toolbar">
        <label>
          原文
          <select
            aria-label="阅读原文"
            disabled={task.busy}
            value={fid}
            onChange={(e) => {
              setFid(e.target.value);
              setSelection(null);
            }}
          >
            <option value="">选择文件</option>
            {files.map((file) => (
              <option key={file.id} value={file.id}>
                {file.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          译文语言
          <select
            disabled={task.busy}
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          >
            <option value="zh-CN">简体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
            <option value="de">Deutsch</option>
          </select>
        </label>
        <button
          className="btn"
          disabled={task.busy || !fid}
          onClick={() => void task.run(load)}
        >
          刷新原文
        </button>
        <button
          className="btn primary"
          disabled={task.busy || !view}
          onClick={() =>
            void task
              .run(() =>
                send(base(pid) + `/reader/${fid}/translate`, { language }),
              )
              .then((result) => result && load())
          }
        >
          翻译全文（计费）
        </button>
      </div>
      <TaskStatus task={task} />
      {view && (
        <>
          <div className="studio-tabs" role="tablist">
            {[
              ["read", "原译对照"],
              ["original", "原始页面"],
              ["guide", "AI 导读"],
              ["qa", "问答"],
              ["map", "结构导图"],
              ["annotations", "高亮与笔记"],
              ["webpage", "交互网页"],
            ].map(([id, label]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {tab === "map" && <MapView tree={view.structure} onJump={jump} />}
          {tab === "original" && (
            <div className="studio-list">
              <a
                className="btn"
                href={apiUrl(`/files/${fid}/download`)}
                download
              >
                下载原始文件
              </a>
              {view.file.extension === ".pdf" ? (
                view.pages.map((page: Row) => (
                  <article className="studio-card" key={page.id}>
                    <h3>第 {page.page_number} 页</h3>
                    <img
                      className="studio-original-image"
                      src={apiUrl(`/pages/${page.id}/preview`)}
                      alt={`原件第${page.page_number}页`}
                      loading="lazy"
                    />
                  </article>
                ))
              ) : (
                <p>
                  此文件没有固定 PDF
                  原页，可在原译对照中阅读结构化正文，或下载原件查看。
                </p>
              )}
            </div>
          )}
          {tab === "guide" && (
            <section>
              <button
                className="btn primary"
                disabled={task.busy}
                onClick={() =>
                  void task
                    .run(() => send(base(pid) + `/reader/${fid}/guide`, {}))
                    .then((value) => value && load())
                }
              >
                生成导读与重点（计费）
              </button>
              {view.guide && (
                <article className="studio-card">
                  <h3>论文概括</h3>
                  <p>{view.guide.data.summary}</p>
                  <h3>原文重点</h3>
                  {view.guide.data.keypoints.map(
                    (point: Row, index: number) => (
                      <article key={index}>
                        <p>{point.text}</p>
                        <blockquote>{point.quote}</blockquote>
                        <button
                          className="btn"
                          onClick={() => jump(point.unit_id)}
                        >
                          跳转对应原文
                        </button>
                      </article>
                    ),
                  )}
                  <h3>后续问题与局限</h3>
                  <ul>
                    {[
                      ...(view.guide.data.questions || []),
                      ...(view.guide.data.limitations || []),
                    ].map((text: string, index: number) => (
                      <li key={index}>{text}</li>
                    ))}
                  </ul>
                </article>
              )}
            </section>
          )}
          {tab === "qa" && (
            <StudioChat
              key={fid}
              pid={pid}
              files={files}
              assets={[]}
              initialFile={fid}
              initialText={
                selection ? `请结合论文解释这段原文：${selection.quote}` : ""
              }
            />
          )}
          {tab === "read" && (
            <>
              <div className="studio-toolbar">
                <label>
                  <input
                    type="checkbox"
                    checked={parallel}
                    onChange={(e) => setParallel(e.target.checked)}
                  />
                  显示双栏译文
                </label>
                <span>
                  {view.units.length} 个原文单元 · 已读{" "}
                  {view.progress?.completed_units?.length || 0}
                </span>
                {view.progress?.unit_id && (
                  <button
                    className="btn"
                    onClick={() => jump(view.progress.unit_id)}
                  >
                    继续上次阅读
                  </button>
                )}
              </div>
              {selection && (
                <div className="studio-selection">
                  <q>{selection.quote.slice(0, 150)}</q>
                  <select
                    aria-label="高亮颜色"
                    value={color}
                    onChange={(e) => setColor(e.target.value)}
                  >
                    {[
                      ["yellow", "黄色"],
                      ["green", "绿色"],
                      ["blue", "蓝色"],
                      ["pink", "粉色"],
                      ["purple", "紫色"],
                    ].map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label="选区笔记"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="写下批注（可选）"
                  />
                  <button
                    className="btn primary"
                    disabled={task.busy}
                    onClick={() =>
                      void task.run(async () => {
                        await send(base(pid) + `/reader/${fid}/annotations`, {
                          ...selection,
                          color,
                          note,
                        });
                        setSelection(null);
                        setNote("");
                        await load();
                      })
                    }
                  >
                    保存高亮与批注
                  </button>
                  <button
                    className="btn"
                    disabled={task.busy}
                    onClick={() =>
                      void task.run(() =>
                        send(
                          `/projects/${pid}/reading-files/${fid}/evidence`,
                          selection,
                        ),
                      )
                    }
                  >
                    建立选区候选证据
                  </button>
                  <button
                    className="btn"
                    onClick={() => onAsk?.(fid, selection.quote)}
                  >
                    就这段提问
                  </button>
                </div>
              )}
              <div
                className="studio-reading-area"
                ref={area}
                onMouseUp={capture}
              >
                {view.units.map((unit: Row) => (
                  <article
                    key={unit.id}
                    data-reader-unit={unit.id}
                    className="studio-reading-unit"
                  >
                    <div className="studio-unit-heading">
                      <span>
                        单元 {unit.ordinal} · 第 {unit.locator.page || 1} 页
                      </span>
                      <button
                        className="text-button"
                        onClick={() =>
                          void task.run(async () => {
                            const progress = await send(
                              base(pid) + `/reader/${fid}/progress`,
                              {
                                unit_id: unit.id,
                                completed_units: [
                                  ...new Set([
                                    ...(view.progress?.completed_units || []),
                                    unit.id,
                                  ]),
                                ],
                              },
                              "PUT",
                            );
                            setView({ ...view, progress });
                          })
                        }
                      >
                        标记已读
                      </button>
                    </div>
                    <div className={parallel ? "studio-parallel" : ""}>
                      <div
                        data-original-unit={unit.id}
                        className="studio-unit-text"
                      >
                        {marked(unit)}
                      </div>
                      {parallel && (
                        <div className="studio-unit-text studio-translation">
                          {unit.translation || (
                            <span className="muted">尚无此段译文</span>
                          )}
                        </div>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
          {tab === "annotations" && (
            <div className="studio-list">
              {view.annotations.length ? (
                view.annotations.map((row: Row) => (
                  <article key={row.id} className="studio-card">
                    <blockquote>{row.data.quote}</blockquote>
                    <p>{row.data.note || "高亮标记"}</p>
                    <span>
                      {row.current
                        ? "原文位置有效"
                        : "原文版本已变化，需重新定位"}
                    </span>
                    <div className="studio-toolbar">
                      <button
                        className="btn"
                        disabled={!row.current}
                        onClick={() => jump(row.data.unit_id)}
                      >
                        回到原文
                      </button>
                      <button
                        className="btn"
                        disabled={!row.current || task.busy}
                        onClick={() =>
                          void task.run(() =>
                            send(
                              `/projects/${pid}/annotations/${row.id}/notes`,
                              {},
                            ),
                          )
                        }
                      >
                        转为随记
                      </button>
                      <button
                        className="btn"
                        onClick={() =>
                          void task.run(async () => {
                            await send(
                              base(pid) +
                                `/records/${row.id}?revision=${row.revision}`,
                              {},
                              "DELETE",
                            );
                            await load();
                          })
                        }
                      >
                        删除
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <p>选中原文可保存高亮与笔记。</p>
              )}
            </div>
          )}
          {tab === "webpage" && (
            <>
              <label>
                希望怎样呈现
                <textarea
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  placeholder="例如：用可调参数解释论文方法，保留结论适用条件"
                />
              </label>
              <button
                className="btn primary"
                disabled={task.busy}
                onClick={() =>
                  void task
                    .run(async () =>
                      send(base(pid) + `/reader/${fid}/webpage`, {
                        instruction,
                      }),
                    )
                    .then((result) => result && setPage(result))
                }
              >
                生成交互网页（计费）
              </button>
              <button
                className="btn"
                onClick={() =>
                  void task.run(async () => {
                    const saved = await api(
                      base(pid) + `/records?kind=webpage&owner=${fid}`,
                    );
                    setPage(saved.at(-1) || null);
                  })
                }
              >
                打开已保存网页
              </button>
              {page && (
                <>
                  <iframe
                    className="studio-webpage"
                    sandbox="allow-scripts"
                    title="论文交互网页预览"
                    srcDoc={safeHtml(page.data.html)}
                  />
                  <a
                    className="btn"
                    href={apiUrl(base(pid) + `/webpages/${page.id}/download`)}
                    download
                  >
                    下载网页
                  </a>
                </>
              )}
            </>
          )}
        </>
      )}
      {!!returnTo && (
        <aside className="studio-reference-panel">
          <button
            className="text-button"
            onClick={() => {
              jump(returnTo);
              setRefs([]);
              setReturnTo("");
            }}
          >
            返回引用段落
          </button>
          {refs.length ? (
            refs.map((ref) => (
              <p key={ref.number}>
                <strong>[{ref.number}]</strong> {ref.text}
                <button className="btn" onClick={() => jump(ref.unit_id)}>
                  定位参考条目
                </button>
                {ref.url && (
                  <a href={ref.url} target="_blank" rel="noreferrer">
                    打开文献来源
                  </a>
                )}
              </p>
            ))
          ) : (
            <p>尚未识别到该编号的参考条目，请对照原文。</p>
          )}
          <button className="btn" onClick={() => setReturnTo("")}>
            关闭
          </button>
        </aside>
      )}
    </section>
  );
}

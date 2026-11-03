import { useEffect, useRef, useState } from "react";
import { api, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import RecordHistory from "../research/RecordHistory";

type Point = [number, number];
const empty = () => ({ version: 1, elements: [] as Row[] });
export default function CanvasEditor({
  pid,
  identity = "",
  onInsert,
}: {
  pid: string;
  identity?: string;
  onInsert?: (artifact: Row) => void;
}) {
  const [record, setRecord] = useState<Row | null>(null),
    [scene, setScene] = useState<Row>(empty),
    [title, setTitle] = useState("自由画布"),
    [tool, setTool] = useState("path"),
    [stroke, setStroke] = useState("#254d40"),
    [width, setWidth] = useState(3),
    [label, setLabel] = useState("文字"),
    [selected, setSelected] = useState("");
  const [history, setHistory] = useState<Row[]>([]),
    [future, setFuture] = useState<Row[]>([]),
    [saved, setSaved] = useState(false);
  const svg = useRef<SVGSVGElement>(null),
    drag = useRef<Row | null>(null),
    current = useRef(scene),
    arrowId = useRef("canvas-arrow-" + crypto.randomUUID());
  const task = useStudioTask();
  current.current = scene;
  useEffect(() => {
    if (identity)
      void task.run(async () => {
        const row = await api(base(pid) + `/records/${identity}`);
        setRecord(row);
        setScene(row.data.scene);
        setTitle(row.data.title || "自由画布");
        setHistory([]);
        setFuture([]);
        setSaved(true);
      });
    else {
      setRecord(null);
      setScene(empty());
    }
  }, [identity, pid]);
  const snapshot = () => {
    setHistory((items) => [
      ...items.slice(-49),
      structuredClone(current.current),
    ]);
    setFuture([]);
    setSaved(false);
  };
  const point = (event: React.PointerEvent): Point => {
    const box = svg.current!.getBoundingClientRect();
    return [
      ((event.clientX - box.left) * 900) / box.width,
      ((event.clientY - box.top) * 560) / box.height,
    ];
  };
  const down = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = point(event);
    if (tool === "select") {
      const target =
        (event.target as Element)
          .closest("[data-element-id]")
          ?.getAttribute("data-element-id") || "";
      setSelected(target);
      const item = scene.elements.find((item: Row) => item.id === target);
      if (item) {
        snapshot();
        drag.current = {
          type: "move",
          id: target,
          start,
          original: structuredClone(item),
        };
      }
      return;
    }
    snapshot();
    const id = crypto.randomUUID();
    const item = {
      id,
      type: tool,
      stroke,
      strokeWidth: width,
      x: tool === "path" ? 0 : start[0],
      y: tool === "path" ? 0 : start[1],
      width: 0,
      height: 0,
      points: tool === "path" ? [start] : [],
      text: tool === "text" ? label : undefined,
      fill: "none",
    };
    setScene({ ...scene, elements: [...scene.elements, item] });
    setSelected(id);
    drag.current = tool === "text" ? null : { type: "draw", id, start };
  };
  const move = (event: React.PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const position = point(event),
      gesture = drag.current;
    setScene((before: Row) => ({
      ...before,
      elements: before.elements.map((item: Row) => {
        if (item.id !== gesture.id) return item;
        if (gesture.type === "move")
          return {
            ...gesture.original,
            x: gesture.original.x + position[0] - gesture.start[0],
            y: gesture.original.y + position[1] - gesture.start[1],
          };
        return item.type === "path"
          ? { ...item, points: [...item.points, position] }
          : {
              ...item,
              width: position[0] - gesture.start[0],
              height: position[1] - gesture.start[1],
            };
      }),
    }));
  };
  const persist = async () => {
    const data = { title, scene };
    const row = record
      ? await send(
          base(pid) + `/records/${record.id}`,
          { revision: record.revision, data },
          "PUT",
        )
      : await send(base(pid) + "/records", { kind: "canvas", data });
    setRecord(row);
    setSaved(true);
    return row;
  };
  const png = async () => {
    const clone = svg.current!.cloneNode(true) as SVGSVGElement;
    clone.querySelectorAll("[data-canvas-ui]").forEach((node) => node.remove());
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", "1800");
    clone.setAttribute("height", "1120");
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], {
        type: "image/svg+xml;charset=utf-8",
      }),
      url = URL.createObjectURL(blob);
    try {
      const image = new Image();
      await new Promise<void>((resolve, reject) => {
        image.onload = () => resolve();
        image.onerror = () => reject(new Error("画布图片生成失败"));
        image.src = url;
      });
      const canvas = document.createElement("canvas");
      canvas.width = 1800;
      canvas.height = 1120;
      const context = canvas.getContext("2d")!;
      context.fillStyle = "#fff";
      context.fillRect(0, 0, 1800, 1120);
      context.drawImage(image, 0, 0, 1800, 1120);
      return canvas.toDataURL("image/png");
    } finally {
      URL.revokeObjectURL(url);
    }
  };
  const publish = async () => {
    const row = await persist();
    const image = await png();
    const artifact = await send(base(pid) + `/canvas/${row.id}/image`, {
      revision: row.revision,
      png: image,
    });
    onInsert?.(artifact);
    return artifact;
  };
  const remove = () => {
    if (!selected) return;
    snapshot();
    setScene({
      ...scene,
      elements: scene.elements.filter((item: Row) => item.id !== selected),
    });
    setSelected("");
  };
  const bounds = (item: Row) => {
    if (item.type === "path" && item.points.length) {
      const xs = item.points.map((p: Point) => p[0] + item.x),
        ys = item.points.map((p: Point) => p[1] + item.y);
      return {
        x: Math.min(...xs),
        y: Math.min(...ys),
        width: Math.max(...xs) - Math.min(...xs),
        height: Math.max(...ys) - Math.min(...ys),
      };
    }
    return {
      x: item.width < 0 ? item.x + item.width : item.x,
      y: item.height < 0 ? item.y + item.height : item.y,
      width:
        Math.abs(item.width) ||
        (item.type === "text" ? Math.max(60, item.text?.length * 16) : 2),
      height: Math.abs(item.height) || (item.type === "text" ? 25 : 2),
    };
  };
  return (
    <section className="studio-canvas-editor">
      <div className="studio-toolbar">
        <label>
          画布名称
          <input
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setSaved(false);
            }}
          />
        </label>
        <label>
          工具
          <select
            aria-label="画布工具"
            value={tool}
            onChange={(e) => setTool(e.target.value)}
          >
            {[
              ["select", "选择与移动"],
              ["path", "手绘"],
              ["rect", "矩形"],
              ["ellipse", "椭圆"],
              ["line", "线段"],
              ["arrow", "箭头"],
              ["text", "文字"],
            ].map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          颜色
          <input
            type="color"
            value={stroke}
            onChange={(e) => setStroke(e.target.value)}
          />
        </label>
        <label>
          线宽
          <input
            type="range"
            min={1}
            max={12}
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
          />
        </label>
        {tool === "text" && (
          <label>
            文字
            <input
              aria-label="画布文字"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
        )}
      </div>
      <div className="studio-toolbar">
        <button
          className="btn"
          disabled={!history.length}
          onClick={() => {
            setFuture([structuredClone(scene), ...future]);
            setScene(history.at(-1)!);
            setHistory(history.slice(0, -1));
            setSaved(false);
          }}
        >
          撤销
        </button>
        <button
          className="btn"
          disabled={!future.length}
          onClick={() => {
            setHistory([...history, structuredClone(scene)]);
            setScene(future[0]);
            setFuture(future.slice(1));
            setSaved(false);
          }}
        >
          重做
        </button>
        <button className="btn" disabled={!selected} onClick={remove}>
          删除选中元素
        </button>
        {selected && tool === "select" && (
          <button
            className="btn"
            onClick={() => {
              snapshot();
              setScene({
                ...scene,
                elements: scene.elements.map((item: Row) =>
                  item.id === selected
                    ? {
                        ...item,
                        stroke,
                        strokeWidth: width,
                        ...(item.type === "text" ? { text: label } : {}),
                      }
                    : item,
                ),
              });
            }}
          >
            应用颜色／文字
          </button>
        )}
      </div>
      {record && (
        <RecordHistory
          pid={pid}
          record={record}
          onRestored={(row) => {
            setRecord(row);
            setScene(row.data.scene);
            setTitle(row.data.title);
            setHistory([]);
            setFuture([]);
            setSaved(true);
          }}
        />
      )}
      <svg
        ref={svg}
        role="img"
        aria-label="自由绘图画布"
        viewBox="0 0 900 560"
        className="studio-drawing"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <defs>
          <marker
            id={arrowId.current}
            markerWidth={10}
            markerHeight={8}
            refX={9}
            refY={4}
            orient="auto-start-reverse"
          >
            <path d="M0 0 L10 4 L0 8" fill="context-stroke" />
          </marker>
        </defs>
        <rect width={900} height={560} fill="white" />
        {scene.elements.map((item: Row) => {
          const props = {
            "data-element-id": item.id,
            stroke: item.stroke,
            strokeWidth: item.strokeWidth,
            fill: "none",
            strokeLinecap: "round" as const,
            strokeLinejoin: "round" as const,
          };
          const box = bounds(item);
          return (
            <g key={item.id}>
              {item.type === "path" ? (
                <polyline
                  {...props}
                  transform={`translate(${item.x} ${item.y})`}
                  points={item.points
                    .map((point: Point) => point.join(","))
                    .join(" ")}
                />
              ) : item.type === "rect" ? (
                <rect {...props} {...box} />
              ) : item.type === "ellipse" ? (
                <ellipse
                  {...props}
                  cx={box.x + box.width / 2}
                  cy={box.y + box.height / 2}
                  rx={box.width / 2}
                  ry={box.height / 2}
                />
              ) : item.type === "text" ? (
                <text
                  data-element-id={item.id}
                  x={item.x}
                  y={item.y + 20}
                  fill={item.stroke}
                  fontSize={20}
                >
                  {item.text}
                </text>
              ) : (
                <line
                  {...props}
                  x1={item.x}
                  y1={item.y}
                  x2={item.x + item.width}
                  y2={item.y + item.height}
                  markerEnd={
                    item.type === "arrow"
                      ? `url(#${arrowId.current})`
                      : undefined
                  }
                />
              )}
              {item.id === selected && (
                <rect
                  data-canvas-ui="true"
                  {...box}
                  x={box.x - 4}
                  y={box.y - 4}
                  width={box.width + 8}
                  height={box.height + 8}
                  fill="none"
                  stroke="#478dcc"
                  strokeDasharray="5 4"
                  pointerEvents="none"
                />
              )}
            </g>
          );
        })}
      </svg>
      <div className="studio-toolbar">
        <button
          className="btn primary"
          disabled={task.busy || !title.trim()}
          onClick={() => void task.run(persist)}
        >
          保存可编辑画布
        </button>
        <button
          className="btn"
          disabled={task.busy}
          onClick={() => void task.run(publish)}
        >
          生成图片并{onInsert ? "用于正文" : "加入研究资源"}
        </button>
        <button
          className="btn"
          disabled={task.busy}
          onClick={() =>
            void task.run(async () => {
              const source = await png(),
                link = document.createElement("a");
              link.href = source;
              link.download = (title || "画布") + ".png";
              link.click();
            })
          }
        >
          下载 PNG
        </button>
        <span className="muted">
          {saved ? "已保存场景，可再次编辑" : "有未保存的修改"}
        </span>
      </div>
      <TaskStatus task={task} />
    </section>
  );
}

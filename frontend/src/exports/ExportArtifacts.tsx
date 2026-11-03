import { useState } from "react";
import { apiUrl, send } from "../api/client";
import { useCommand } from "../tasks/useCommand";
import type { Row } from "../workspace/types";

export function ExportFiles({ item }: { item: Row }) {
  return (
    <>
      <span className="badge">
        {(
          { docx: "Word", latex: "LaTeX", markdown: "Markdown" } as Record<
            string,
            string
          >
        )[item.format] || "历史导出"}{" "}
        · {item.mode === "strict" ? "正式" : "草稿"}
      </span>
      {(item.artifacts || []).map((file: Row) => (
        <a
          className={"btn " + (file.role === "archive" ? "primary" : "")}
          key={file.name}
          href={apiUrl(file.download_url)}
          download
        >
          {file.name}
        </a>
      ))}
      {!item.artifacts?.length && (
        <span role="status">文件已移除，请重新生成</span>
      )}
    </>
  );
}

export function ExportActions({
  pid,
  mode = "draft",
  onChanged,
}: {
  pid: string;
  mode?: string;
  onChanged?: () => void;
}) {
  const [format, setFormat] = useState("latex"),
    [language, setLanguage] = useState("zh"),
    [result, setResult] = useState<Row | null>(null);
  const command = useCommand(onChanged, pid + ":export");
  return (
    <div className="studio-card">
      <h3>多格式导出</h3>
      <p>使用当前正文快照与统一交付检查，保留具体产物和版本信息。</p>
      <div className="studio-toolbar">
        <label>
          格式
          <select
            aria-label="导出格式"
            value={format}
            onChange={(e) => setFormat(e.target.value)}
          >
            <option value="latex">LaTeX 工程</option>
            <option value="markdown">Markdown</option>
            <option value="docx">Word</option>
          </select>
        </label>
        {format === "latex" && (
          <label>
            模板
            <select
              aria-label="LaTeX 模板语言"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              <option value="zh">中文（XeLaTeX）</option>
              <option value="en">英文（pdfLaTeX）</option>
            </select>
          </label>
        )}
        <button
          className="btn primary"
          disabled={command.busy}
          onClick={() =>
            void command
              .run(() =>
                send(`/projects/${pid}/exports`, { format, language, mode }),
              )
              .then((value) => value && setResult(value))
          }
        >
          生成导出文件
        </button>
      </div>
      {command.busy && <p role="status">{command.phase}</p>}
      {command.error && <p role="alert">{command.error}</p>}
      {result && <ExportFiles item={result} />}
    </div>
  );
}

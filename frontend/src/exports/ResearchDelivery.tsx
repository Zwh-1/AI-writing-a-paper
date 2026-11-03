import { useState } from "react";
import { apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";

export default function ResearchDelivery({ pid }: { pid: string }) {
  const [language, setLanguage] = useState("zh"),
    [result, setResult] = useState<Row | null>(null);
  const task = useStudioTask();
  return (
    <section>
      <div className="studio-card">
        <h3>LaTeX 工程交付</h3>
        <p>
          导出当前已保存正文、公式、图片和参考文献。工程保留正文版本清单与转换提示。
        </p>
        <div className="studio-toolbar">
          <select
            aria-label="LaTeX 模板语言"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
          >
            <option value="zh">中文（XeLaTeX）</option>
            <option value="en">英文（pdfLaTeX／XeLaTeX）</option>
          </select>
          <button
            className="btn primary"
            disabled={task.busy}
            onClick={() =>
              void task
                .run(() => send(base(pid) + "/exports/latex", { language }))
                .then((value) => value && setResult(value))
            }
          >
            生成 LaTeX 工程 ZIP
          </button>
        </div>
        <TaskStatus task={task} />
        {result && (
          <>
            <a className="btn primary" href={apiUrl(result.download)} download>
              下载 LaTeX 工程
            </a>
            <p className="muted">
              生成完成，尚未执行 TeX 编译；请按包内说明编译并检查版面。
            </p>
            {result.warnings?.map((text: string, index: number) => (
              <p key={index}>{text}</p>
            ))}
          </>
        )}
      </div>
      <div className="studio-card">
        <h3>工作区只读命令</h3>
        <p>
          在项目目录用数据命令查看当前工作区，支持概要、文献列表、详情和全文搜索。
        </p>
        <pre>
          node scripts/yantai-data.mjs summary{"\n"}node scripts/yantai-data.mjs
          list knowledge{"\n"}node scripts/yantai-data.mjs get knowledge
          &lt;文献ID&gt;{"\n"}node scripts/yantai-data.mjs search
          &quot;研究问题&quot;
        </pre>
        <p>
          可加 <code>--project 项目ID</code> 或 <code>--snapshot 备份.zip</code>{" "}
          指定范围。命令仅查询数据。
        </p>
      </div>
    </section>
  );
}

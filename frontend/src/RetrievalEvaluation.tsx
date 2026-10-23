import { useEffect, useRef, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;
type Props = {
  pid: string;
  tick: number;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
};
type Sample = { id: number; query: string; kind: string; sources: Row[] };
const kinds: Row = {
  all: "全部资料",
  research: "研究证据",
  example: "正例与反例",
};
const placeholders = new Set([
  "人工确认的正确来源ID",
  "实际来源ID",
  "真实来源ID",
  "来源ID",
]);
const example = JSON.stringify(
  [
    {
      query: "请输入要验证的真实问题",
      expected_source_ids: ["实际来源ID"],
      kind: "all",
    },
  ],
  null,
  2,
);
function validate(value: any): { cases: Row[]; reason: string } {
  if (!Array.isArray(value) || !value.length || value.length > 200)
    return {
      cases: [],
      reason: "请填写 1 至 200 个真实问题，每个问题至少选择一个标准来源。",
    };
  const result: Row[] = [];
  for (let i = 0; i < value.length; i++) {
    const item = value[i],
      prefix = "问题 " + (i + 1) + "：";
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.query !== "string" ||
      !item.query.trim()
    )
      return { cases: [], reason: prefix + "请填写要检验的真实问题。" };
    if (["待检验的问题", "请输入要验证的真实问题"].includes(item.query.trim()))
      return { cases: [], reason: prefix + "仍是示例问题，请替换为真实问题。" };
    if (
      !Array.isArray(item.expected_source_ids) ||
      !item.expected_source_ids.length ||
      item.expected_source_ids.some(
        (id: any) => typeof id !== "string" || !id.trim(),
      )
    )
      return { cases: [], reason: prefix + "请至少从原文中选择一个标准来源。" };
    if (
      item.expected_source_ids.some((id: string) => placeholders.has(id.trim()))
    )
      return {
        cases: [],
        reason:
          prefix +
          "仍使用示例来源 ID。请从当前项目选择真实来源，或填写真实的来源 ID。",
      };
    if (!Object.prototype.hasOwnProperty.call(kinds, item.kind ?? "all"))
      return {
        cases: [],
        reason: prefix + "检索范围应为 all、research 或 example。",
      };
    result.push({
      query: item.query.trim(),
      expected_source_ids: [
        ...new Set(item.expected_source_ids.map((id: string) => id.trim())),
      ],
      kind: item.kind ?? "all",
    });
  }
  return { cases: result, reason: "" };
}

function SourcePicker({
  files,
  selected,
  onSelect,
  disabled,
}: {
  files: Row[];
  selected: Row[];
  onSelect: (source: Row) => void;
  disabled: boolean;
}) {
  const [fileId, setFileId] = useState("");
  const [pageId, setPageId] = useState("");
  const [pages, setPages] = useState<Row[]>([]);
  const [sources, setSources] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [loadingPages, setLoadingPages] = useState(false);
  const [loadingSources, setLoadingSources] = useState(false);
  useEffect(() => {
    let active = true;
    setPageId("");
    setPages([]);
    setSources([]);
    setError("");
    setLoadingPages(!!fileId);
    if (fileId)
      api("/files/" + fileId + "/pages")
        .then((data) => {
          if (active) {
            setPages(data);
            setPageId(data[0]?.id ?? "");
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoadingPages(false);
        });
    return () => {
      active = false;
    };
  }, [fileId]);
  useEffect(() => {
    let active = true;
    setSources([]);
    setLoadingSources(!!pageId);
    setError("");
    if (pageId)
      api("/pages/" + pageId)
        .then((data) => {
          if (active) setSources(data.sources ?? []);
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoadingSources(false);
        });
    return () => {
      active = false;
    };
  }, [pageId]);
  const file = files.find((f) => f.id === fileId),
    page = pages.find((p) => p.id === pageId);
  return (
    <div className="retrieval-source-picker">
      <div className="retrieval-fields">
        <label className="field">
          <span>选择标准来源的资料</span>
          <select
            aria-label="选择标准来源的资料"
            value={fileId}
            disabled={disabled}
            onChange={(e) => setFileId(e.target.value)}
          >
            <option value="">请选择当前项目的资料</option>
            {files.map((f) => (
              <option
                key={f.id}
                value={f.id}
                disabled={f.status !== "ready" || !f.pages}
              >
                {f.name}
                {f.status !== "ready" ? "（解析未完成）" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>选择原文页码</span>
          <select
            aria-label="选择原文页码"
            value={pageId}
            disabled={disabled || !pages.length || loadingPages}
            onChange={(e) => setPageId(e.target.value)}
          >
            <option value="">
              {loadingPages ? "正在读取页面…" : "请先选择资料"}
            </option>
            {pages.map((p) => (
              <option key={p.id} value={p.id}>
                第 {p.page_number} 页 · {p.title || "原文"}
              </option>
            ))}
          </select>
        </label>
      </div>
      {loadingSources && (
        <p className="muted" role="status">
          正在读取本页原文片段…
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {!!pageId && !loadingSources && !error && !sources.length && (
        <p className="muted">
          本页没有可选的原文片段，请选择其他页，或先完成文字提取。
        </p>
      )}
      <div className="retrieval-source-options">
        {sources.map((source) => (
          <div className="retrieval-source-option" key={source.id}>
            <label>
              <input
                type="checkbox"
                checked={selected.some((s) => s.id === source.id)}
                disabled={disabled}
                onChange={() =>
                  onSelect({
                    ...source,
                    file_name: file?.name,
                    page_number: page?.page_number,
                  })
                }
              />
              <span>选为标准来源 · 第 {page?.page_number} 页</span>
            </label>
            <p>
              {String(source.text).slice(0, 260)}
              {String(source.text).length > 260 ? "…" : ""}
            </p>
            {String(source.text).length > 260 && (
              <details>
                <summary>展开完整原文</summary>
                <pre>{source.text}</pre>
              </details>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function EvaluationResult({ result }: { result: Row }) {
  return (
    <div className="retrieval-evaluation-result">
      <p>
        <strong>
          {result.hits}/{result.total} 个问题命中标准来源
        </strong>{" "}
        · 平均 Recall@10 {(Number(result.recall_at_10) * 100).toFixed(1)}%
      </p>
      <p className="muted">
        按本次提交的来源标注计算，不会自动标记资料原文已核对。
      </p>
      <ol>
        {result.cases?.map((item: Row, i: number) => (
          <li key={i}>
            <p>{item.query}</p>
            <small>
              {item.hit ? "已命中" : "未命中"} · 找到{" "}
              {
                new Set(
                  item.expected_source_ids.filter((id: string) =>
                    item.retrieved_ids.includes(id),
                  ),
                ).size
              }
              /{item.expected_source_ids.length} 个标准来源 · Recall@10{" "}
              {(Number(item.recall_at_10) * 100).toFixed(1)}%
            </small>
          </li>
        ))}
      </ol>
      <details>
        <summary>查看评测技术数据（JSON）</summary>
        <pre className="technical">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}

function EvaluationForm({ pid, tick, act }: Props) {
  const counter = useRef(2);
  const [samples, setSamples] = useState<Sample[]>([
    { id: 1, query: "", kind: "all", sources: [] },
  ]);
  const [mode, setMode] = useState("form");
  const [json, setJson] = useState("");
  const [files, setFiles] = useState<Row[]>([]);
  const [history, setHistory] = useState<Row[]>([]);
  const [latest, setLatest] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api("/projects/" + pid + "/files")
      .then((data) => {
        if (active) setFiles(data.filter((f: Row) => f.active));
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    api("/projects/" + pid + "/retrieval/evaluations")
      .then((data) => {
        if (active) setHistory(data);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [pid, tick]);
  const formCases = samples.map((s) => ({
    query: s.query,
    kind: s.kind,
    expected_source_ids: s.sources.map((source) => source.id),
  }));
  let validation = validate(formCases);
  if (mode === "json") {
    if (!json.trim())
      validation = {
        cases: [],
        reason: "请粘贴包含真实问题与当前项目来源 ID 的 JSON 样本。",
      };
    else
      try {
        validation = validate(JSON.parse(json));
      } catch {
        validation = {
          cases: [],
          reason: "JSON 格式不正确，请使用数组，并为字段名和文字加英文双引号。",
        };
      }
  } else if (
    !validation.reason &&
    samples.some((s) =>
      s.sources.some((source) => !files.some((f) => f.id === source.file_id)),
    )
  )
    validation = {
      cases: [],
      reason: "已选来源的文件已移除或停用，请重新选择当前项目的来源。",
    };
  if (mode === "form" && !validation.reason) {
    const mismatched = samples.findIndex((s) =>
      s.sources.some(
        (source) =>
          (s.kind === "research" && source.kind !== "evidence") ||
          (s.kind === "example" &&
            !["example", "counterexample"].includes(source.kind)),
      ),
    );
    if (mismatched >= 0)
      validation = {
        cases: [],
        reason:
          "问题 " +
          (mismatched + 1) +
          "：标准来源不在所选检索范围，请改为“全部资料”或重新选择来源。",
      };
  }
  const update = (id: number, value: Partial<Sample>) =>
    setSamples((current) =>
      current.map((s) => (s.id === id ? { ...s, ...value } : s)),
    );
  return (
    <details
      className="panel mb workflow-panel retrieval-evaluation"
      data-testid="retrieval-evaluation"
    >
      <summary>
        <strong>资料检索召回评测</strong>
        <span>填写真实问题，并从原文中选择应当找到的标准来源</span>
      </summary>
      <p className="muted">
        用于检查资料检索能否找到你指定的原文。先填写问题，再勾选能够回答该问题的原文片段；来源由你选择，不自动生成标准答案；每个问题检查前
        10 条检索结果。
      </p>
      <div
        className="retrieval-mode"
        role="group"
        aria-label="评测样本填写方式"
      >
        <button
          className={"btn " + (mode === "form" ? "primary" : "")}
          disabled={busy}
          aria-pressed={mode === "form"}
          onClick={() => {
            setMode("form");
            setError("");
          }}
        >
          表单填写
        </button>
        <button
          className={"btn " + (mode === "json" ? "primary" : "")}
          disabled={busy}
          aria-pressed={mode === "json"}
          onClick={() => {
            setMode("json");
            setError("");
          }}
        >
          JSON 批量填写
        </button>
      </div>
      {mode === "form" ? (
        <>
          {samples.map((sample, index) => (
            <section
              className="retrieval-sample"
              key={sample.id}
              aria-label={"评测问题 " + (index + 1)}
            >
              <div className="section-heading">
                <h3>问题 {index + 1}</h3>
                {samples.length > 1 && (
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() =>
                      setSamples((current) =>
                        current.filter((s) => s.id !== sample.id),
                      )
                    }
                  >
                    移除此问题
                  </button>
                )}
              </div>
              <div className="retrieval-fields">
                <label className="field">
                  <span>要检验的真实问题</span>
                  <textarea
                    rows={2}
                    value={sample.query}
                    disabled={busy}
                    onChange={(e) =>
                      update(sample.id, { query: e.target.value })
                    }
                    placeholder="例如：资料对参考文献数量有什么要求？"
                  />
                </label>
                <label className="field">
                  <span>检索范围</span>
                  <select
                    aria-label="检索范围"
                    value={sample.kind}
                    disabled={busy}
                    onChange={(e) =>
                      update(sample.id, { kind: e.target.value })
                    }
                  >
                    {Object.entries(kinds).map(([id, label]) => (
                      <option key={id} value={id}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <SourcePicker
                files={files}
                selected={sample.sources}
                disabled={busy}
                onSelect={(source) =>
                  setSamples((current) =>
                    current.map((s) =>
                      s.id === sample.id
                        ? {
                            ...s,
                            sources: s.sources.some((p) => p.id === source.id)
                              ? s.sources.filter((p) => p.id !== source.id)
                              : [...s.sources, source],
                          }
                        : s,
                    ),
                  )
                }
              />
              <div className="retrieval-selected">
                <strong>已选标准来源：{sample.sources.length} 个</strong>
                {sample.sources.map((source) => (
                  <div key={source.id}>
                    <span>
                      {source.file_name} · 第 {source.page_number} 页 ·{" "}
                      {String(source.text).slice(0, 80)}
                    </span>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        update(sample.id, {
                          sources: sample.sources.filter(
                            (s) => s.id !== source.id,
                          ),
                        })
                      }
                    >
                      移除此来源
                    </button>
                  </div>
                ))}
              </div>
            </section>
          ))}
          <div className="retrieval-actions">
            <button
              className="btn"
              disabled={busy || samples.length >= 200}
              onClick={() =>
                setSamples((current) => [
                  ...current,
                  {
                    id: counter.current++,
                    query: "",
                    kind: "all",
                    sources: [],
                  },
                ])
              }
            >
              添加问题
            </button>
            <button
              className="btn"
              disabled={busy || !!validate(formCases).reason}
              onClick={() => {
                setJson(JSON.stringify(formCases, null, 2));
                setMode("json");
              }}
            >
              将当前表单转为 JSON
            </button>
          </div>
        </>
      ) : (
        <label className="field">
          <span>评测样本 JSON</span>
          <textarea
            aria-label="评测样本 JSON"
            rows={7}
            value={json}
            onChange={(e) => setJson(e.target.value)}
            disabled={busy}
            placeholder={example}
          />
          <small>
            示例仅用于说明格式，不能直接运行。来源 ID
            必须来自当前项目；可先在“表单填写”中选择原文，再转为 JSON。
          </small>
        </label>
      )}
      <p className="muted">
        标准来源的选择仅用于本次评测标注，不会修改原文核对状态。建议逐步收集不少于
        30 个覆盖不同问题类型的真实样本。
      </p>
      <div className="retrieval-actions">
        <button
          className="btn primary"
          disabled={busy || !!validation.reason}
          onClick={async () => {
            if (busy || validation.reason) return;
            setBusy(true);
            setError("");
            try {
              const result = await act(async () => {
                try {
                  return await send(
                    "/projects/" + pid + "/retrieval/evaluate",
                    { cases: validation.cases },
                  );
                } catch (failure) {
                  setError((failure as Error).message);
                  throw failure;
                }
              }, "评测已完成，逐条结果保留在项目中");
              if (result) setLatest(result);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "正在评测…" : "运行实际召回评测"}
        </button>
        {validation.reason && (
          <span className="muted" role="status">
            {validation.reason}
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {latest && (
        <section className="retrieval-latest" data-testid="retrieval-latest">
          <h3>本次评测结果</h3>
          <EvaluationResult result={latest} />
        </section>
      )}
      {!!history.length && (
        <details className="retrieval-history">
          <summary>历史评测记录（{history.length} 次）</summary>
          {history.map((run) => {
            let result;
            try {
              result = JSON.parse(run.result);
            } catch {
              return null;
            }
            return (
              <details key={run.id}>
                <summary>
                  {new Date(run.created_at).toLocaleString("zh-CN")} ·{" "}
                  {result.hits}/{result.total} 个问题命中
                </summary>
                <EvaluationResult result={result} />
              </details>
            );
          })}
        </details>
      )}
    </details>
  );
}
export default function RetrievalEvaluation(props: Props) {
  return <EvaluationForm key={props.pid} {...props} />;
}

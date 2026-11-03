import { useCallback, useEffect, useState } from "react";
import { api, apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";

export default function StudioSettings({
  pid,
  files: providedFiles,
  onRefresh,
}: {
  pid: string;
  files: Row[];
  onRefresh: () => Promise<void>;
}) {
  const resource = useWorkspaceResource<Row[]>(
    providedFiles.length ? "" : `/projects/${pid}/files`,
    0,
  );
  const files = providedFiles.length ? providedFiles : resource.data || [];
  const [parsing, setParsing] = useState<Row | null>(null),
    [execution, setExecution] = useState<Row | null>(null),
    [cache, setCache] = useState<Row | null>(null),
    [orphans, setOrphans] = useState<Row | null>(null),
    [fid, setFid] = useState(files[0]?.id || ""),
    [parseResults, setParseResults] = useState<Row[]>([]),
    [code, setCode] = useState(
      "import matplotlib.pyplot as plt\nplt.plot([1,2,3],[2,4,3])\nplt.title('Research figure')\nplt.show()",
    ),
    [result, setResult] = useState<Row | null>(null),
    [parseKey, setParseKey] = useState(""),
    [executionKey, setExecutionKey] = useState(""),
    [health, setHealth] = useState<Row | null>(null);
  const task = useStudioTask(() => {
    void loadResults();
  }, pid + ":parsing-execution");
  const load = useCallback(async () => {
    const [p, e, c] = await Promise.all([
      api(base(pid) + "/parsing"),
      api(base(pid) + "/execution"),
      api("/studio/cache"),
    ]);
    setParsing(p);
    setExecution(e);
    setCache(c);
  }, [pid]);
  useEffect(() => {
    void task.run(load);
  }, [load]);
  const loadResults = async () => {
    if (fid)
      setParseResults(
        await api(base(pid) + `/records?kind=parse_result&owner=${fid}`),
      );
  };
  useEffect(() => {
    if (!files.some((row) => row.id === fid)) setFid(files[0]?.id || "");
  }, [files, fid]);
  useEffect(() => {
    if (fid) void task.run(loadResults);
  }, [fid]);
  const provider = parsing?.provider || "surya-local",
    config = parsing?.providers?.[provider] || {};
  return (
    <section>
      <TaskStatus task={task} />
      {health && (
        <div className="studio-card" role="status">
          <h3>连接状态</h3>
          <pre>{JSON.stringify(health, null, 2)}</pre>
        </div>
      )}
      <div className="studio-card">
        <h3>高级文档解析</h3>
        <p>
          原有本地解析继续可用。高级解析保留结构类型、阅读顺序与页坐标，结果须核对后再设为当前阅读版本。
        </p>
        {parsing && (
          <>
            <div className="studio-toolbar">
              <label>
                解析来源
                <select
                  aria-label="高级解析来源"
                  value={provider}
                  onChange={(e) => {
                    setParsing({ ...parsing, provider: e.target.value });
                    setParseKey("");
                  }}
                >
                  <option value="surya-local">Surya 本地服务</option>
                  <option value="surya-modal">Surya Modal 服务</option>
                  <option value="mineru">MinerU 云解析</option>
                </select>
              </label>
              <label>
                服务地址
                <input
                  value={config.url || ""}
                  onChange={(e) =>
                    setParsing({
                      ...parsing,
                      providers: {
                        ...parsing.providers,
                        [provider]: { ...config, url: e.target.value },
                      },
                    })
                  }
                />
              </label>
              <label>
                访问凭证
                <input
                  type="password"
                  value={parseKey}
                  onChange={(e) => setParseKey(e.target.value)}
                  placeholder={
                    config.key_configured
                      ? "已保存，留空保留"
                      : "按服务要求填写"
                  }
                />
              </label>
            </div>
            <button
              className="btn"
              disabled={task.busy}
              onClick={() =>
                void task.run(async () => {
                  setParsing(
                    await send(
                      base(pid) + "/parsing",
                      {
                        provider,
                        providers: {
                          [provider]: {
                            ...config,
                            ...(parseKey ? { api_key: parseKey } : {}),
                          },
                        },
                      },
                      "PUT",
                    ),
                  );
                  setParseKey("");
                })
              }
            >
              保存解析设置
            </button>
            <button
              className="btn"
              disabled={task.busy}
              onClick={() =>
                void task.run(async () =>
                  setHealth(await api(base(pid) + "/parsing/health")),
                )
              }
            >
              检查解析连接
            </button>
          </>
        )}
        <div className="studio-toolbar">
          <label>
            待解析文献
            <select
              aria-label="待解析文献"
              value={fid}
              onChange={(e) => setFid(e.target.value)}
            >
              <option value="">选择原件</option>
              {files.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.name}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn primary"
            disabled={task.busy || !fid}
            onClick={() =>
              void task
                .run(() => send(base(pid) + `/parsing/${fid}`, { provider }))
                .then((value) => value && loadResults())
            }
          >
            提交高级解析
          </button>
          <button
            className="btn"
            disabled={task.busy || !fid}
            onClick={() => void task.run(loadResults)}
          >
            刷新解析结果
          </button>
        </div>
        {parseResults.map((row) => (
          <article key={row.id} className="studio-card">
            <strong>{row.data.provider}</strong>
            <p>
              {row.data.status === "completed"
                ? `${row.data.pages} 页 · ${row.data.blocks} 个结构块`
                : "远端任务处理中"}
              {row.data.job_id ? ` · 任务 ${row.data.job_id}` : ""}
            </p>
            <button
              className="btn"
              disabled={task.busy || row.data.status !== "completed"}
              onClick={() =>
                void task.run(async () => {
                  await send(base(pid) + `/parse-results/${row.id}/adopt`, {
                    revision: row.revision,
                  });
                  await onRefresh();
                  await loadResults();
                })
              }
            >
              设为当前阅读版本
            </button>
            <small>切换会使旧阅读位置与依赖需要复查；不会替代人工核对。</small>
          </article>
        ))}
      </div>
      <div className="studio-card">
        <h3>Python 运行与绘图</h3>
        {execution && (
          <>
            <div className="studio-toolbar">
              <label>
                运行引擎
                <select
                  aria-label="Python 运行引擎"
                  value={execution.provider}
                  onChange={(e) =>
                    setExecution({ ...execution, provider: e.target.value })
                  }
                >
                  <option value="local-container">本机 Docker 隔离运行</option>
                  <option value="remote-http">远端执行服务</option>
                </select>
              </label>
              {execution.provider === "remote-http" && (
                <>
                  <label>
                    远端地址
                    <input
                      value={execution.url}
                      onChange={(e) =>
                        setExecution({ ...execution, url: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    访问凭证
                    <input
                      type="password"
                      value={executionKey}
                      onChange={(e) => setExecutionKey(e.target.value)}
                      placeholder={
                        execution.key_configured
                          ? "已保存，留空保留"
                          : "服务访问凭证"
                      }
                    />
                  </label>
                </>
              )}
            </div>
            <div className="studio-toolbar">
              <button
                className="btn"
                disabled={task.busy}
                onClick={() =>
                  void task.run(async () => {
                    setExecution(
                      await send(
                        base(pid) + "/execution",
                        {
                          ...execution,
                          ...(executionKey ? { api_key: executionKey } : {}),
                        },
                        "PUT",
                      ),
                    );
                    setExecutionKey("");
                  })
                }
              >
                保存运行设置
              </button>
              <button
                className="btn"
                disabled={task.busy}
                onClick={() =>
                  void task.run(async () =>
                    setHealth(await api(base(pid) + "/execution/health")),
                  )
                }
              >
                检查运行引擎
              </button>
              <button
                className="btn"
                disabled={task.busy}
                onClick={() =>
                  void task
                    .run(() => send(base(pid) + "/execution/install", {}))
                    .then((value) => value && load())
                }
              >
                准备隔离绘图镜像
              </button>
              <span>
                {execution.plot_image_ready
                  ? "绘图镜像已准备"
                  : "绘图镜像待准备"}
              </span>
            </div>
          </>
        )}
        <label>
          Python 代码
          <textarea
            aria-label="研究助手 Python 代码"
            rows={8}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <button
          className="btn primary"
          disabled={task.busy || !code.trim()}
          onClick={() =>
            void task
              .run(() =>
                send(base(pid) + "/execution", { code, timeout_seconds: 30 }),
              )
              .then((value) => value && setResult(value))
          }
        >
          运行并收集图片
        </button>
        {result && (
          <div>
            <p>退出状态：{result.exit_code}</p>
            <pre>{result.stdout}</pre>
            {result.stderr && <pre>{result.stderr}</pre>}
            {result.artifacts?.map((item: Row) => (
              <figure key={item.artifact_id}>
                <img
                  className="studio-output-image"
                  src={apiUrl(item.preview_url)}
                  alt={item.name}
                />
                <figcaption>{item.name} · 已存入研究资源</figcaption>
              </figure>
            ))}
          </div>
        )}
      </div>
      <div className="studio-card">
        <h3>作者与关键词图谱</h3>
        <p>
          从当前书目建立作者和关键词导航。作者未提供唯一标识时保留独立身份，避免同名误合并。
        </p>
        <button
          className="btn"
          disabled={task.busy}
          onClick={() =>
            void task.run(() => send(base(pid) + "/metadata-graph", {}))
          }
        >
          更新书目关系图谱
        </button>
      </div>
      <div className="studio-card">
        <h3>磁盘缓存与孤立资源</h3>
        <p>原始资料、论文正文与历史版本不属于可再生缓存。</p>
        <button
          className="btn"
          disabled={task.busy}
          onClick={() => void task.run(load)}
        >
          重新统计
        </button>
        {cache && (
          <ul className="studio-cache-list">
            {cache.categories.map((item: Row) => (
              <li key={item.id}>
                <span>
                  {{
                    previews: "原页预览",
                    temporary: "临时产物",
                    ocr: "OCR 缓存",
                    models: "本地模型",
                  }[item.id as string] || item.id}{" "}
                  · {(item.bytes / 1024 / 1024).toFixed(1)} MB · {item.files}{" "}
                  个文件
                </span>
                <button
                  className="btn"
                  disabled={task.busy || !item.bytes}
                  onClick={() =>
                    void task.run(async () => {
                      await send("/studio/cache/clear", {
                        category: item.id,
                        expected_bytes: item.bytes,
                      });
                      await load();
                    })
                  }
                >
                  清理并按需重建
                </button>
              </li>
            ))}
          </ul>
        )}
        <button
          className="btn"
          disabled={task.busy}
          onClick={() =>
            void task.run(async () =>
              setOrphans(await api("/studio/cache/orphans")),
            )
          }
        >
          预览无人引用的资源
        </button>
        {orphans && (
          <div>
            <p>
              发现 {orphans.items.length} 个无人引用的对象，占{" "}
              {(orphans.bytes / 1024 / 1024).toFixed(2)}{" "}
              MB。历史记录引用的对象继续保留。
            </p>
            <button
              className="btn"
              disabled={task.busy || !orphans.items.length}
              onClick={() =>
                void task.run(async () => {
                  await send("/studio/cache/orphans", {
                    fingerprint: orphans.fingerprint,
                  });
                  setOrphans(null);
                  await load();
                })
              }
            >
              回收这批孤立资源
            </button>
            <button className="btn" onClick={() => setOrphans(null)}>
              保留
            </button>
          </div>
        )}
      </div>
    </section>
  );
}

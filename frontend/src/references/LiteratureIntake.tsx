import { useEffect, useState } from "react";
import { api, apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";

export function LiteratureLibrary({
  pid,
  library,
  onRefresh,
  onRead,
}: {
  pid: string;
  library: Row;
  onRefresh: () => Promise<void>;
  onRead: (fid: string) => void;
}) {
  const [url, setUrl] = useState(""),
    [title, setTitle] = useState(""),
    [query, setQuery] = useState(""),
    [tag, setTag] = useState(""),
    [selected, setSelected] = useState<string[]>([]),
    [tagDraft, setTagDraft] = useState<Row>({}),
    [translated, setTranslated] = useState<Row | null>(null),
    [preview, setPreview] = useState<Row | null>(null),
    [zotero, setZotero] = useState<Row>({
      library_id: "",
      library_type: "users",
      attachments: true,
    }),
    [key, setKey] = useState(""),
    [authorization, setAuthorization] = useState("");
  const task = useStudioTask(onRefresh,pid+':literature-import');
  useEffect(() => {
    void task.run(async () => setZotero(await api(base(pid) + "/zotero")));
  }, [pid]);
  const items = library.items.filter(
    (row: Row) =>
      (!query ||
        JSON.stringify(row.metadata)
          .toLowerCase()
          .includes(query.toLowerCase())) &&
      (!tag || row.metadata.tags?.includes(tag)),
  );
  const execute = async (action: () => Promise<any>) => {
    const value = await task.run(action);
    if (value) await onRefresh();
    return value;
  };
  return (
    <section>
      <div className="studio-card">
        <h3>导入研究文献</h3>
        <form
          className="studio-toolbar"
          onSubmit={(event) => {
            event.preventDefault();
            void execute(() =>
              send(base(pid) + "/imports/url", {
                url,
                metadata: { title: title.trim() || undefined },
              }),
            ).then((value) => {
              if (value) {
                setUrl("");
                setTitle("");
              }
            });
          }}
        >
          <label>
            论文文件链接
            <input
              aria-label="论文文件链接"
              type="url"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.org/paper.pdf"
            />
          </label>
          <label>
            标题（可选）
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <button className="btn primary" disabled={task.busy || !url.trim()}>
            从链接导入
          </button>
          <label className="btn">
            上传本地文献
            <input
              aria-label="上传研究文献"
              type="file"
              accept=".pdf,.docx,.doc,.txt,.md"
              disabled={task.busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                void execute(async () => {
                  const form = new FormData();
                  form.append("file", file);
                  form.append("role", "research");
                  const response = await api(`/projects/${pid}/files`, {
                    method: "POST",
                    body: form,
                  });
                  await send(`/projects/${pid}/references`, {
                    file_id: response.file.id,
                    metadata: { title: file.name.replace(/\.[^.]+$/, "") },
                  });
                  return response.task || response;
                });
                event.target.value = "";
              }}
              hidden
            />
          </label>
        </form>
      </div>
      <details className="studio-card">
        <summary>连接 Zotero</summary>
        <div className="studio-toolbar">
          <label>
            库类型
            <select
              value={zotero.library_type || "users"}
              onChange={(e) =>
                setZotero({ ...zotero, library_type: e.target.value })
              }
            >
              <option value="users">个人文献库</option>
              <option value="groups">群组文献库</option>
            </select>
          </label>
          <label>
            文献库编号
            <input
              aria-label="Zotero 文献库编号"
              value={zotero.library_id || ""}
              onChange={(e) =>
                setZotero({ ...zotero, library_id: e.target.value })
              }
            />
          </label>
          <label>
            API Key
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={
                zotero.key_configured
                  ? "已保存；留空保留"
                  : "填写 Zotero API Key"
              }
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={zotero.attachments !== false}
              onChange={(e) =>
                setZotero({ ...zotero, attachments: e.target.checked })
              }
            />
            同步 PDF 附件
          </label>
        </div>
        <div className="studio-toolbar">
          <button
            className="btn"
            disabled={task.busy}
            onClick={() =>
              void task.run(async () => {
                setZotero(
                  await send(
                    base(pid) + "/zotero",
                    { ...zotero, ...(key ? { api_key: key } : {}) },
                    "PUT",
                  ),
                );
                setKey("");
              })
            }
          >
            保存连接
          </button>
          <button
            className="btn primary"
            disabled={task.busy || !zotero.library_id || !zotero.key_configured}
            onClick={() =>
              void execute(() => send(base(pid) + "/zotero/sync", {}))
            }
          >
            同步全部文献
          </button>
          <button
            className="btn"
            disabled={task.busy || !zotero.oauth_configured}
            onClick={() =>
              void task.run(async () => {
                const result = await send(base(pid) + "/zotero/oauth");
                setAuthorization(result.authorize_url);
              })
            }
          >
            使用 Zotero 授权登录
          </button>
          {authorization && (
            <a
              className="btn"
              href={authorization}
              target="_blank"
              rel="noreferrer"
            >
              打开 Zotero 授权页面
            </a>
          )}
          <button
            className="btn"
            onClick={() =>
              void task.run(async () =>
                setZotero(await api(base(pid) + "/zotero")),
              )
            }
          >
            刷新连接状态
          </button>
        </div>
        <p className="muted">
          API Key 可在 Zotero
          账户中创建。授权登录需配置应用凭据；同步会分页读取，并保留已有文献的内部身份。
        </p>
        {zotero.last_sync && (
          <p>
            上次同步读取 {zotero.last_sync.seen} 篇，更新{" "}
            {zotero.last_sync.updated} 篇。
          </p>
        )}
      </details>
      <TaskStatus task={task} />
      <div className="studio-toolbar">
        <input
          aria-label="搜索文献"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索题名、作者和摘要"
        />
        <select
          aria-label="按标签筛选"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
        >
          <option value="">全部标签</option>
          {library.tags.map((tag: string) => (
            <option key={tag}>{tag}</option>
          ))}
        </select>
        <button
          className="btn"
          onClick={() => setSelected(items.map((row: Row) => row.id))}
        >
          选择当前结果
        </button>
        <button
          className="btn"
          disabled={!selected.length || task.busy}
          onClick={() =>
            void task.run(async () =>
              setPreview(
                await send(base(pid) + "/deletion/preview", {
                  reference_ids: selected,
                }),
              ),
            )
          }
        >
          移除所选文献（{selected.length}）
        </button>
        <input
          aria-label="所选文献批量标签"
          value={tagDraft._batch || ""}
          onChange={(e) => setTagDraft({ ...tagDraft, _batch: e.target.value })}
          placeholder="批量标签，逗号分隔"
        />
        <button
          className="btn"
          disabled={!selected.length || task.busy}
          onClick={() =>
            void execute(() =>
              send(
                base(pid) + "/tags",
                {
                  reference_ids: selected,
                  tags: (tagDraft._batch || "").split(/[,，]/),
                  mode: "add",
                },
                "PUT",
              ),
            )
          }
        >
          为所选文献添加标签
        </button>
      </div>
      {preview && (
        <div className="studio-card studio-deletion-preview">
          <h3>移除影响预览</h3>
          <p>
            将从当前文献库移除 {preview.items.length}{" "}
            篇。历史正文引用、共享原件和审计记录继续保留。
          </p>
          {preview.items.map((item: Row) => (
            <p key={item.reference_id}>
              {item.title} · 历史引用 {item.historical_citations} 处
            </p>
          ))}
          <button
            className="btn"
            disabled={task.busy}
            onClick={() =>
              void execute(() =>
                send(base(pid) + "/deletion", {
                  reference_ids: preview.items.map(
                    (item: Row) => item.reference_id,
                  ),
                  fingerprint: preview.fingerprint,
                }),
              ).then((value) => {
                if (value) {
                  setPreview(null);
                  setSelected([]);
                }
              })
            }
          >
            确认移出当前库
          </button>
          <button className="btn" onClick={() => setPreview(null)}>
            取消
          </button>
        </div>
      )}
      <div className="studio-list">
        {items.map((row: Row) => (
          <article key={row.id} className="studio-card">
            <div className="studio-card-heading">
              <label className="studio-check">
                <input
                  type="checkbox"
                  checked={selected.includes(row.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, row.id]
                        : selected.filter((id) => id !== row.id),
                    )
                  }
                />
                <strong>{row.metadata.title || "未命名文献"}</strong>
              </label>
              <span>{row.metadata.year}</span>
            </div>
            <p className="muted">
              {Array.isArray(row.metadata.authors)
                ? row.metadata.authors.join("，")
                : row.metadata.authors}
            </p>
            {row.metadata.abstract && (
              <details>
                <summary>文献摘要</summary>
                <p>{row.metadata.abstract}</p>
              </details>
            )}
            <div className="studio-toolbar">
              <input
                aria-label={`${row.metadata.title}的标签`}
                value={tagDraft[row.id] ?? (row.metadata.tags || []).join("，")}
                onChange={(e) =>
                  setTagDraft({ ...tagDraft, [row.id]: e.target.value })
                }
                placeholder="标签，用逗号分隔"
              />
              <button
                className="btn"
                disabled={task.busy}
                onClick={() =>
                  void execute(() =>
                    send(
                      base(pid) + "/tags",
                      {
                        reference_ids: [row.id],
                        tags: (
                          tagDraft[row.id] ??
                          (row.metadata.tags || []).join("，")
                        ).split(/[,，]/),
                        mode: "replace",
                      },
                      "PUT",
                    ),
                  )
                }
              >
                保存标签
              </button>
              {row.file_id && (
                <button
                  className="btn primary"
                  onClick={() => onRead(row.file_id)}
                >
                  阅读与翻译
                </button>
              )}
              <button
                className="btn"
                disabled={task.busy || !row.metadata.abstract}
                onClick={() =>
                  void task
                    .run(() =>
                      send(base(pid) + "/abstract/translate", {
                        reference_id: row.id,
                        language: "zh-CN",
                      }),
                    )
                    .then((result) => result && setTranslated(result))
                }
              >
                翻译摘要（计费）
              </button>
              <button
                className="btn"
                onClick={() =>
                  void task.run(async () => {
                    const value = await api(base(pid) + `/abstract/${row.id}`);
                    setTranslated(value);
                  })
                }
              >
                查看已存译文
              </button>
            </div>
          </article>
        ))}
        {!items.length && (
          <p className="studio-empty">
            尚无匹配书目。可从链接、本地文件、Zotero 或文献发现导入。
          </p>
        )}
      </div>
      {translated && (
        <aside className="studio-card">
          <h3>文献摘要译文</h3>
          <p className="studio-message-text">{translated.data.text}</p>
          <button className="btn" onClick={() => setTranslated(null)}>
            收起
          </button>
        </aside>
      )}
    </section>
  );
}

export function Discover({
  pid,
  onRefresh,
}: {
  pid: string;
  onRefresh: () => Promise<void>;
}) {
  const [query, setQuery] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState(""),
    [oa, setOa] = useState(false),
    [aiPlan, setAiPlan] = useState(false),
    [results, setResults] = useState<Row | null>(null),
    [page, setPage] = useState(1),
    [key, setKey] = useState("");
  const task = useStudioTask();
  const search = (next = 1) =>
    void task
      .run(() =>
        send(base(pid) + "/openalex/search", {
          query,
          from_year: from || undefined,
          to_year: to || undefined,
          open_access: oa,
          ai_plan: aiPlan,
          page: next,
        }),
      )
      .then((value) => {
        if (value) {
          setResults(value.data || value);
          setPage(next);
        }
      });
  return (
    <section>
      <h3>发现研究文献</h3>
      <p className="muted">
        通过 OpenAlex
        检索外部文献。元数据与全文分开登记，导入书目不会标记为已经读过。
      </p>
      <details>
        <summary>OpenAlex 凭证</summary>
        <input
          aria-label="OpenAlex API Key"
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="个人 API Key"
        />
        <button
          className="btn"
          onClick={() =>
            void task.run(async () => {
              await send(
                base(pid) + "/credentials/openalex",
                { value: key },
                "PUT",
              );
              setKey("");
            })
          }
        >
          保存凭证
        </button>
      </details>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <label>
          研究问题或关键词
          <textarea
            aria-label="文献发现查询"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            rows={3}
          />
        </label>
        <div className="studio-toolbar">
          <label>
            起始年份
            <input
              type="number"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
          <label>
            截止年份
            <input
              type="number"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={oa}
              onChange={(e) => setOa(e.target.checked)}
            />
            仅开放获取
          </label>
          <label>
            <input
              type="checkbox"
              checked={aiPlan}
              onChange={(e) => setAiPlan(e.target.checked)}
            />
            AI 扩展查询并复审（计费）
          </label>
          <button className="btn primary" disabled={task.busy || !query.trim()}>
            检索文献
          </button>
        </div>
      </form>
      <TaskStatus task={task} />
      {results && (
        <>
          <p>
            {results.total !== undefined
              ? `共 ${results.total} 条匹配 · 第 ${page} 页`
              : `本次筛选 ${results.items.length} 篇`}
          </p>
          {results.plan && (
            <details>
              <summary>本次检索计划</summary>
              <ul>
                {results.plan.queries?.map((query: string) => (
                  <li key={query}>{query}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="studio-list">
            {results.items.map((item: Row) => (
              <article key={item.id} className="studio-card">
                <h4>{item.title}</h4>
                <p>
                  {item.authors?.join("，")} · {item.year} · {item.journal}
                </p>
                {item.abstract && (
                  <details>
                    <summary>摘要</summary>
                    <p>{item.abstract}</p>
                  </details>
                )}
                <div className="studio-toolbar">
                  <button
                    className="btn"
                    disabled={task.busy}
                    onClick={() =>
                      void task.run(async () => {
                        const result = await send(
                          base(pid) + "/openalex/import",
                          { metadata: item },
                        );
                        await onRefresh();
                        return result;
                      })
                    }
                  >
                    加入书目
                  </button>
                  <button
                    className="btn primary"
                    disabled={task.busy || !item.pdf_url}
                    onClick={() =>
                      void task
                        .run(() =>
                          send(base(pid) + "/openalex/import", {
                            metadata: item,
                            download: true,
                          }),
                        )
                        .then((value) => value && onRefresh())
                    }
                  >
                    导入 PDF 原件
                  </button>
                  {/^https?:\/\//.test(item.url || "") && (
                    <a href={item.url} target="_blank" rel="noreferrer">
                      打开文献页面
                    </a>
                  )}
                </div>
              </article>
            ))}
          </div>
          {!results.plan && (
            <div className="studio-toolbar">
              <button
                className="btn"
                disabled={task.busy || page <= 1}
                onClick={() => search(page - 1)}
              >
                上一页
              </button>
              <button
                className="btn"
                disabled={task.busy || results.items.length < 20}
                onClick={() => search(page + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

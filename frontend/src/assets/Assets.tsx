import { useCallback, useEffect, useState } from "react";
import { api, apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import { RichTextEditor as NoteEditor } from "../editor/ResearchTextEditor";
import { researchApi } from "../api/research";
import {useWorkspaceLocation} from '../workspace/WorkspaceContext';
export default function StudioAssets({
  pid,
  onRefresh,
  onEditCanvas,
}: {
  pid: string;
  onRefresh: () => Promise<void>;
  onEditCanvas?: (id: string) => void;
}) {
  const workspace=useWorkspaceLocation(pid,'studio');
  const [items, setItems] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row | null>(null),
    [draft, setDraft] = useState<Row>({ title: "", text: "", tags: [] }),
    [query, setQuery] = useState(""),
    [tag, setTag] = useState("");
  const task = useStudioTask();
  const load = useCallback(
    async () => setItems(await researchApi(pid).assets()),
    [pid],
  );
  useEffect(() => {
    void task.run(load);
  }, [load]);
  const select = async (row: Row | null) => {
    setSelected(row);
    workspace.update({assetId:row?.id});
    if (!row) {
      setDraft({ title: "", text: "", tags: [] });
      return;
    }
    const value = row.metadata;
    if (value.studio_rich_text)
      setDraft({
        title: row.name,
        text: "",
        document: value.document,
        tags: value.tags || [],
      });
    else setDraft({ title: row.name, tags: value.tags || [] });
  };
  useEffect(()=>{
    const row=items.find(item=>item.id===workspace.location.assetId);
    if(row&&row.id!==selected?.id)void select(row);
  },[items,workspace.location.assetId]);
  const tags = [...new Set(items.flatMap((item) => item.metadata.tags || []))];
  return (
    <section className="studio-chat-layout">
      <aside className="studio-sidebar">
        <button className="btn primary" onClick={() => void select(null)}>
          新建富文本资产
        </button>
        <input
          aria-label="搜索资产"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索资产"
        />
        <select
          aria-label="资产标签筛选"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
        >
          <option value="">全部标签</option>
          {tags.map((tag) => (
            <option key={tag}>{tag}</option>
          ))}
        </select>
        {items
          .filter(
            (item) =>
              (!query ||
                JSON.stringify(item)
                  .toLowerCase()
                  .includes(query.toLowerCase())) &&
              (!tag || item.metadata.tags?.includes(tag)),
          )
          .map((item) => (
            <button
              className="studio-history"
              key={item.id}
              onClick={() => void select(item)}
            >
              {item.name}
            </button>
          ))}
      </aside>
      <main className="studio-chat-main">
        {!selected || selected.metadata.studio_rich_text ? (
          <>
            <label>
              资产标题
              <input
                aria-label="资产标题"
                value={draft.title || ""}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <label>
              资产标签
              <input
                aria-label="资产标签"
                value={(draft.tags || []).join("，")}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    tags: e.target.value
                      .split(/[,，]/)
                      .map((value) => value.trim())
                      .filter(Boolean),
                  })
                }
              />
            </label>
            <NoteEditor
              pid={pid}
              key={
                (selected?.id || "new") +
                ":" +
                (selected?.metadata.revision || 1)
              }
              value={draft}
              onChange={setDraft}
            />
            <button
              className="btn primary"
              disabled={task.busy || !draft.title?.trim()}
              onClick={() =>
                void task.run(async () => {
                  const saved = selected
                    ? await send(
                        base(pid) + `/assets/${selected.id}/content`,
                        { ...draft, revision: selected.metadata.revision },
                        "PUT",
                      )
                    : await send(base(pid) + "/assets", draft);
                  await load();
                  await onRefresh();
                  await select(saved);
                })
              }
            >
              保存资产
            </button>
          </>
        ) : (
          <>
            <h3>{selected.name}</h3>
            <p>
              类型：{selected.kind} · 版本 {selected.revision || 1}
            </p>
            <a
              className="btn"
              href={apiUrl(base(pid) + `/assets/${selected.id}`)}
              download
            >
              下载资产原文件
            </a>
            {selected.metadata.studio_canvas_id && onEditCanvas && (
              <button
                className="btn"
                onClick={() => onEditCanvas(selected.metadata.studio_canvas_id)}
              >
                回到原画布编辑
              </button>
            )}
            {selected.kind === "figure" && (
              <img
                className="studio-output-image"
                src={apiUrl(base(pid) + `/assets/${selected.id}`)}
                alt={selected.name}
              />
            )}
            <p>
              可在学术对话中选择此资源，助手会读取实际内容并按来源权限发送。
            </p>
          </>
        )}
        <TaskStatus task={task} />
      </main>
    </section>
  );
}

import { RichTextEditor as NoteEditor } from "../editor/ResearchTextEditor";
export { RichTextEditor as NoteEditor } from "../editor/ResearchTextEditor";
import { researchApi } from "../api/research";
import SourceCard from "../workspace/SourceCard";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import {
  readLocation,
  useWorkspaceLocation,
} from "../workspace/WorkspaceContext";
import { useCallback, useEffect, useState } from "react";
import { api, apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import AgentSettings from "./AgentSettings";
import ProductPicker from '../products/ProductPicker';
import type { SourceRef } from '../workspace/WorkspaceContext';
import RecordHistory from "../research/RecordHistory";

function CodeMessage({
  pid,
  message,
  onNote,
  onUseImage,
  onDocumentAdopted,
}: {
  pid: string;
  message: Row;
  onNote: () => void;
  onUseImage?: (id: string) => void;
  onDocumentAdopted?: (value: Row) => void;
}) {
  const [executionInputs,setExecutionInputs]=useState<SourceRef[]>([]);
  const [execution, setExecution] = useState<Row | null>(null),
    task = useStudioTask(
      () => loadExecution(),
      pid + ":message-execution:" + message.id,
    );
  const loadExecution = useCallback(async () => {
    const value = await api(
      base(pid) + `/execution/results?message_id=${message.id}`,
    );
    if (value.result) setExecution(value.result);
  }, [pid, message.id]);
  useEffect(() => {
    if (message.data.text.includes("```")) void loadExecution();
  }, [loadExecution]);
  const [proposal, setProposal] = useState<Row | null>(null);
  const pieces = message.data.text.split(/(```(?:python|py)\s*\n[\s\S]*?```)/g);
  return (
    <article className={"studio-message " + message.data.role}>
      <strong>{message.data.role === "user" ? "我" : "助手"}</strong>
      {message.data.text.includes('```')&&<div className="studio-card"><ProductPicker pid={pid} value={executionInputs} onChange={setExecutionInputs} purpose="execute" disabled={task.busy}/><small>所选内容写入 /input/products.json；文件和图片在其中列出文件名。执行沿用当前隔离引擎及外发设置。</small></div>}
      {pieces.map((piece: string, index: number) =>
        piece.startsWith("```") ? (
          <div key={index}>
            <pre>{piece.replace(/^```(?:python|py)\s*\n|```$/g, "")}</pre>
            <button
              className="btn"
              disabled={task.busy}
              onClick={() =>
                void task
                  .run(() =>
                    send(base(pid) + "/execution", {
                      code: piece.replace(/^```(?:python|py)\s*\n|```$/g, ""),
                      timeout_seconds: 30,
                      message_id: message.id,
                      product_refs:executionInputs,
                    }),
                  )
                  .then((value) => value && setExecution(value))
              }
            >
              运行 Python
            </button>
          </div>
        ) : (
          <p key={index} className="studio-message-text">
            {piece}
          </p>
        ),
      )}
      {message.data.citations?.map((citation: Row) =>
        citation.source_ref ? (
          <SourceCard
            key={citation.source_id}
            pid={pid}
            source={citation.source_ref}
            label={citation.name}
          />
        ) : (
          <small key={citation.source_id}>
            来源：{citation.name || "文献原文"}
          </small>
        ),
      )}
      <button className="text-button" onClick={onNote}>
        存为随记
      </button>
      <TaskStatus task={task} />
      {message.data.proposal_id && (
        <div className="studio-card">
          <button
            className="btn"
            disabled={task.busy}
            onClick={() =>
              void task.run(async () =>
                setProposal(
                  await api(base(pid) + `/records/${message.data.proposal_id}`),
                ),
              )
            }
          >
            查看正文改写候选
          </button>
          {proposal && (
            <>
              <ul>
                {proposal.data.operations.map(
                  (operation: Row, index: number) => (
                    <li key={index}>
                      <del>{operation.before}</del>
                      <p>{operation.after}</p>
                    </li>
                  ),
                )}
              </ul>
              <button
                className="btn primary"
                disabled={task.busy || !!proposal.data.adopted_revision_id}
                onClick={() =>
                  void task.run(async () => {
                    const saved = await send(
                      base(pid) + `/proposals/${proposal.id}/adopt`,
                      { revision: proposal.revision },
                    );
                    onDocumentAdopted?.(saved);
                    setProposal(
                      await api(base(pid) + `/records/${proposal.id}`),
                    );
                  })
                }
              >
                {proposal.data.adopted_revision_id
                  ? "已经采用"
                  : "采用到当前正文"}
              </button>
              <small>会核对正文版本、来源与锁定段落，保留新旧版本。</small>
            </>
          )}
        </div>
      )}
      {execution && (
        <div className="studio-execution-result">
          <p>
            退出状态：{execution.exit_code} ·{" "}
            {execution.timed_out_or_output_limited
              ? "达到时限或输出上限"
              : "执行结束"}
          </p>
          <pre>{execution.stdout}</pre>
          {execution.input_manifest?.length>0&&<details><summary>本次执行实际输入</summary><ul>{execution.input_manifest.map((item:Row)=><li key={item.name}>{item.name} · {item.sha256.slice(0,12)}</li>)}</ul></details>}
          {execution.stderr && <pre>{execution.stderr}</pre>}
          {execution.artifacts?.map((item: Row) => (
            <figure key={item.artifact_id}>
              <img src={apiUrl(item.preview_url)} alt="Python 生成的图像" />
              <figcaption>{item.name}</figcaption>
              <button
                className="btn"
                onClick={() => onUseImage?.(item.artifact_id)}
              >
                用于正文
              </button>
            </figure>
          ))}
        </div>
      )}
    </article>
  );
}

export function StudioChat({
  pid,
  files,
  assets,
  initialFile = "",
  initialText = "",
  onSavedNote,
  onUseImage,
  getDocumentContext,
  onDocumentAdopted,
}: {
  pid: string;
  files: Row[];
  assets: Row[];
  initialFile?: string;
  initialText?: string;
  onSavedNote?: () => void;
  onUseImage?: (id: string) => void;
  getDocumentContext?: () => Promise<Row>;
  onDocumentAdopted?: (value: Row) => void;
}) {
  const workspace = useWorkspaceLocation(pid, "assistant");
  const handoff = useWorkspaceLocation(pid, 'studio');
  const [selectedProducts, setSelectedProducts] = useState<SourceRef[]>(workspace.location.productRefs || []);
  useEffect(() => {
    if (handoff.location.productRefs?.length) {
      setSelectedProducts(handoff.location.productRefs);
      workspace.update({productRefs:handoff.location.productRefs});
      handoff.update({productRefs:undefined});
    }
  }, [handoff.location.productRefs]);
  const sharedAssets = useWorkspaceResource<Row[]>(
    assets.length ? "" : `/projects/${pid}/asset-library`,
    0,
  );
  const availableAssets = assets.length ? assets : sharedAssets.data || [];
  const [conversations, setConversations] = useState<Row[]>([]),
    [conversation, setConversation] = useState<Row | null>(null),
    [messages, setMessages] = useState<Row[]>([]),
    [agents, setAgents] = useState<Row[]>([]),
    [agent, setAgent] = useState("academic");
  const [text, setText] = useState(initialText),
    [selectedFiles, setSelectedFiles] = useState<string[]>(
      initialFile ? [initialFile] : [],
    ),
    [selectedAssets, setSelectedAssets] = useState<string[]>([]),
    [useKnowledge, setUseKnowledge] = useState(true),
    [showAgent, setShowAgent] = useState(false);
  const task = useStudioTask(() => void load(), pid + ":chat");
  const noteList = useWorkspaceResource<Row[]>(
    base(pid) + "/records?kind=note",
    0,
  );
  const [selectedNotes, setSelectedNotes] = useState<string[]>([]);
  const [allowEdit, setAllowEdit] = useState(false);
  const load = useCallback(async () => {
    const [rows, status] = await Promise.all([
      api(base(pid) + "/records?kind=conversation"),
      api(base(pid) + "/status"),
    ]);
    setConversations(rows);
    setAgents(status.agents);
    const last = readLocation(pid,'studio').conversationId || readLocation(pid, "assistant").conversationId;
    const saved = rows.find((row: Row) => row.id === last);
    if (saved) {
      setConversation(saved);
      setSelectedProducts(readLocation(pid,'assistant').productRefs || saved.data.product_refs || []);
      setMessages(await api(base(pid) + `/conversations/${saved.id}/messages`));
    }
  }, [pid]);
  useEffect(() => {
    void task.run(load);
  }, [load]);
  useEffect(() => {
    if (initialText) setText(initialText);
    if (initialFile) setSelectedFiles([initialFile]);
  }, [initialText, initialFile]);
  const open = async (row: Row) => {
    handoff.update({conversationId:undefined});
    workspace.update({ conversationId: row.id });
    setConversation(row);
    setAgent(row.data.agent_id || "academic");
    setSelectedFiles(row.data.file_ids || []);
    setSelectedAssets([]);
    setSelectedProducts(row.data.product_refs || []);
    workspace.update({productRefs:row.data.product_refs || []});
    setMessages(await api(base(pid) + `/conversations/${row.id}/messages`));
  };
  const create = async () => {
    const row = await send(base(pid) + "/records", {
      kind: "conversation",
      data: {
        title: text.trim().slice(0, 40) || "新的学术对话",
        agent_id: agent,
        file_ids: selectedFiles,
        product_refs:selectedProducts,
      },
    });
    await load();
    await open(row);
    return row;
  };
  const submit = async () => {
    const row = conversation || (await create());
    const documentContext = getDocumentContext
      ? await getDocumentContext()
      : {};
    const pending = await send(
      base(pid) + `/conversations/${row.id}/messages`,
      {
        text,
        agent_id: agent,
        file_ids: selectedFiles,
        asset_ids: selectedAssets,
        note_ids: selectedNotes,
        product_refs: selectedProducts,
        use_knowledge: useKnowledge,
        ...documentContext,
        allow_edit: allowEdit,
      },
    );
    setText("");
    setMessages(await api(base(pid) + `/conversations/${row.id}/messages`));
    return pending;
  };
  return (
    <section className="studio-chat-layout">
      <aside className="studio-sidebar">
        <button
          className="btn primary"
          disabled={task.busy}
          onClick={() => {
            workspace.update({ conversationId: undefined });
            setConversation(null);
            setMessages([]);
            setText("");
          }}
        >
          新建对话
        </button>
        <h3>会话历史</h3>
        {[...conversations].reverse().map((row) => (
          <button
            className={
              "studio-history " + (conversation?.id === row.id ? "active" : "")
            }
            key={row.id}
            onClick={() => void task.run(() => open(row))}
          >
            {row.data.title || "学术对话"}
          </button>
        ))}
      </aside>
      <div className="studio-chat-main">
        <div className="studio-toolbar">
          <label>
            智能体
            <select
              aria-label="对话智能体"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            >
              {agents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" onClick={() => setShowAgent(!showAgent)}>
            设置智能体
          </button>
          <label>
            <input
              type="checkbox"
              checked={useKnowledge}
              onChange={(e) => setUseKnowledge(e.target.checked)}
            />
            检索本项目知识库
          </label>
        </div>
        {showAgent && (
          <AgentSettings
            pid={pid}
            onSaved={async (id) => {
              await load();
              setAgent(id || "academic");
            }}
          />
        )}
        <details className="studio-sources">
          <summary>选择文献与研究资源（文献将按当前外发权限读取）</summary>
          <div className="studio-two-col">
            <label>
              文献
              <select
                aria-label="对话文献范围"
                multiple
                value={selectedFiles}
                onChange={(e) =>
                  setSelectedFiles(
                    Array.from(e.target.selectedOptions).map(
                      (item) => item.value,
                    ),
                  )
                }
              >
                {files.map((file) => (
                  <option key={file.id} value={file.id}>
                    {file.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              研究资源
              <select
                multiple
                value={selectedAssets}
                onChange={(e) =>
                  setSelectedAssets(
                    Array.from(e.target.selectedOptions).map(
                      (item) => item.value,
                    ),
                  )
                }
              >
                {availableAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
        <ProductPicker pid={pid} value={selectedProducts} onChange={(values)=>{setSelectedProducts(values);workspace.update({productRefs:values});}} disabled={task.busy} />
        {getDocumentContext && (
          <label>
            <input
              type="checkbox"
              aria-label="允许提出正文改写"
              checked={allowEdit}
              onChange={(event) => setAllowEdit(event.target.checked)}
            />
            读取当前正文，并允许提出待采用的改写（智能体需启用正文改写能力）
          </label>
        )}
        <label>
          个人随记（未核实）
          <select
            aria-label="对话随记范围"
            multiple
            value={selectedNotes}
            onChange={(event) =>
              setSelectedNotes(
                Array.from(event.target.selectedOptions).map(
                  (item) => item.value,
                ),
              )
            }
          >
            {noteList.data?.map((row) => (
              <option key={row.id} value={row.id}>
                {row.data.title}
              </option>
            ))}
          </select>
        </label>
        <div className="studio-messages" aria-live="polite">
          {messages.length ? (
            messages.map((message) => (
              <CodeMessage
                key={message.id}
                pid={pid}
                message={message}
                onUseImage={onUseImage}
                onDocumentAdopted={onDocumentAdopted}
                onNote={() =>
                  void task.run(async () => {
                    await send(base(pid) + "/records", {
                      kind: "note",
                      data: {
                        title: message.data.text.slice(0, 40),
                        text: message.data.text,
                        conversation_id: conversation?.id,
                        message_id: message.id,
                        source_refs: message.source_refs || [],
                      },
                    });
                    onSavedNote?.();
                  })
                }
              />
            ))
          ) : (
            <div className="studio-empty">
              <h3>从一个研究问题开始</h3>
              <p>
                可直接讨论，也可选定文献或检索当前知识库。模型回复保留为讨论内容。
              </p>
            </div>
          )}
        </div>
        <TaskStatus task={task} />
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void task.run(submit).then(async (value) => {
              if (value && conversation) await open(conversation);
              else if (value) {
                const cid = value.conversation_id;
                const row = await api(base(pid) + `/records/${cid}`);
                await open(row);
              }
            });
          }}
        >
          <textarea
            aria-label="学术对话消息"
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="输入研究问题，或就选中的原文继续讨论…"
          />
          <div className="studio-toolbar">
            <button
              className="btn primary"
              disabled={task.busy || !text.trim()}
              type="submit"
            >
              发送（计费）
            </button>
            <span className="muted">结果保留在会话和任务中心</span>
          </div>
        </form>
      </div>
    </section>
  );
}

export function StudioNotes({ pid, tick = 0 }: { pid: string; tick?: number }) {
  const workspace=useWorkspaceLocation(pid,'studio');
  const [notes, setNotes] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row | null>(null),
    [draft, setDraft] = useState<Row>({ title: "", text: "", tags: [] }),
    [candidate, setCandidate] = useState<Row | null>(null),
    [query, setQuery] = useState("");
  const task = useStudioTask(undefined, pid + ":notes");
  const sections = useWorkspaceResource<Row[]>(`/projects/${pid}/sections`, 0);
  const load = useCallback(
    async () => setNotes(await researchApi(pid).notes()),
    [pid],
  );
  useEffect(() => {
    void task.run(load);
  }, [load, tick]);
  const choose = (row: Row | null) => {
    setSelected(row);
    setDraft(row?.data || { title: "", text: "", tags: [] });
    setCandidate(null);
    workspace.update({noteId:row?.id});
  };
  useEffect(()=>{
    const row=notes.find(item=>item.id===workspace.location.noteId);
    if(row&&row.id!==selected?.id)choose(row);
  },[notes,workspace.location.noteId]);
  const save = async () => {
    const row = selected
      ? await send(
          base(pid) + `/records/${selected.id}`,
          { revision: selected.revision, data: draft },
          "PUT",
        )
      : await send(base(pid) + "/records", { kind: "note", data: draft });
    choose(row);
    await load();
  };
  return (
    <section className="studio-chat-layout">
      <aside className="studio-sidebar">
        <button className="btn primary" onClick={() => choose(null)}>
          新建随记
        </button>
        <input
          aria-label="搜索随记"
          placeholder="搜索标题和内容"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {notes
          .filter((row) =>
            JSON.stringify(row.data)
              .toLowerCase()
              .includes(query.toLowerCase()),
          )
          .map((row) => (
            <button
              className={
                "studio-history " + (selected?.id === row.id ? "active" : "")
              }
              key={row.id}
              onClick={() => choose(row)}
            >
              {row.data.title || "未命名随记"}
            </button>
          ))}
      </aside>
      <div className="studio-chat-main">
        <label>
          关联章节
          <select
            aria-label="随记关联章节"
            value={draft.section_id || ""}
            onChange={(event) =>
              setDraft({ ...draft, section_id: event.target.value || null })
            }
          >
            <option value="">未关联章节</option>
            {sections.data
              ?.filter((row) => row.state !== "archived")
              .map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}
                </option>
              ))}
          </select>
        </label>
        <label>
          随记标题
          <input
            value={draft.title || ""}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        </label>
        {draft.section_id&&<label><input type="checkbox" checked={draft.use_for_writing===true} onChange={event=>setDraft({...draft,use_for_writing:event.target.checked})}/>将此随记作为关联章节的写作材料（保存后生效）</label>}
        <label>
          标签
          <input
            value={(draft.tags || []).join("，")}
            onChange={(e) =>
              setDraft({
                ...draft,
                tags: e.target.value
                  .split(/[,，]/)
                  .map((item) => item.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
        <NoteEditor
          pid={pid}
          key={(selected?.id || "new") + ":" + (selected?.revision || 0)}
          value={draft}
          onChange={setDraft}
        />
        {selected && (
          <RecordHistory
            pid={pid}
            record={selected}
            onRestored={(row) => {
              choose(row);
              void load();
            }}
          />
        )}
        <div className="studio-toolbar">
          <button
            className="btn primary"
            disabled={task.busy || !draft.title?.trim()}
            onClick={() => void task.run(save)}
          >
            保存随记
          </button>
          {selected && (
            <button
              className="btn"
              onClick={() =>
                void task.run(async () => {
                  await send(
                    base(pid) +
                      `/records/${selected.id}?revision=${selected.revision}`,
                    {},
                    "DELETE",
                  );
                  choose(null);
                  await load();
                })
              }
            >
              删除随记
            </button>
          )}
        </div>
        <TaskStatus task={task} />
        {selected && draft.section_id && (
          <button
            className="btn"
            disabled={task.busy}
            onClick={() =>
              void task.run(async () => {
                const context = await api(
                  `/sections/${draft.section_id}/content`,
                );
                const proposal = await send(
                  `/projects/${pid}/notes/${selected.id}/proposals`,
                  {
                    section_id: draft.section_id,
                    revision_id: context.revision?.id,
                  },
                );
                const saved = await send(
                  base(pid) + `/proposals/${proposal.id}/adopt`,
                  { revision: proposal.revision },
                );
                return saved;
              })
            }
          >
            作为未核实草稿追加到关联章节
          </button>
        )}
        {selected?.source_refs?.map((ref: Row) => (
          <SourceCard key={ref.type + ref.id} pid={pid} source={ref as any} />
        ))}
        {selected && (
          <div className="studio-toolbar">
            {[
              ["summarize", "概括"],
              ["organize", "整理"],
              ["refine", "润写"],
              ["expand", "扩展"],
            ].map(([operation, label]) => (
              <button
                key={operation}
                className="btn"
                disabled={task.busy}
                onClick={() =>
                  void task
                    .run(() =>
                      send(base(pid) + `/notes/${selected.id}/ai`, {
                        operation,
                      }),
                    )
                    .then((result) => result && setCandidate(result))
                }
              >
                {label}（计费）
              </button>
            ))}
          </div>
        )}
        {candidate && (
          <article className="studio-card">
            <h3>随记整理候选</h3>
            <p className="studio-message-text">{candidate.candidate.text}</p>
            <button
              className="btn"
              onClick={() => {
                setDraft({
                  ...draft,
                  title: candidate.candidate.title || draft.title,
                  text: candidate.candidate.text,
                  document: {
                    type: "doc",
                    content: candidate.candidate.text
                      .split("\n")
                      .map((text: string) => ({
                        type: "paragraph",
                        content: text ? [{ type: "text", text }] : [],
                      })),
                  },
                });
                setCandidate(null);
                setSelected(
                  selected
                    ? { ...selected, revision: selected.revision }
                    : null,
                );
              }}
            >
              放入编辑草稿
            </button>
            <p className="muted">保存后形成新版本，原内容保留在历史中。</p>
          </article>
        )}
      </div>
    </section>
  );
}

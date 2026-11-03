import { tiptapPort } from "./EditorPort";
import { useCallback, useEffect, useState } from "react";
import { api, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import { commentKey, anchorPrefix } from "./EditorExtensions";
import FormulaTool from "./FormulaTool";
import CompletionTool from "./CompletionTool";
import CommentPanel from "./CommentPanel";
import CanvasEditor from "../assets/CanvasEditor";
import {navigateWorkspace} from '../workspace/WorkspaceContext';
import { StudioChat } from "../assistant/ChatNotes";

export default function EditorTools({
  pid,
  sectionId,
  editor,
  getRevision,
  ensureSaved,
  onArtifact,
  onDocumentAdopted,
  files = [],
  assets = [],
}: {
  pid: string;
  sectionId: string;
  editor: any;
  getRevision: () => string;
  ensureSaved: () => Promise<boolean>;
  onArtifact: (value: Row) => void;
  onDocumentAdopted?: (value: Row) => void;
  files?: Row[];
  assets?: Row[];
}) {
  const [panel, setPanel] = useState(""),
    [comments, setComments] = useState<Row[]>([]),
    [quote, setQuote] = useState<Row | null>(null),
    [formula, setFormula] = useState(""),
    [inline, setInline] = useState(true),
    [canvasId, setCanvasId] = useState(""),
    [pendingIntents, setPendingIntents] = useState<Row[]>([]);
  const task = useStudioTask(undefined, pid + ":editor:" + sectionId);
  const port = tiptapPort(
    editor,
    getRevision,
    ensureSaved,
    onDocumentAdopted || (() => {}),
    onArtifact,
  );
  const selection = useCallback(() => {
    if (!editor) return null;
    const { from, to, $from, $to } = editor.state.selection;
    let nodeId = $from.parent.attrs.nodeId;
    for (let depth = $from.depth; depth > 0 && !nodeId; depth--)
      nodeId = $from.node(depth).attrs.nodeId;
    const single = $from.parent === $to.parent,
      before = anchorPrefix($from.parent, $from.parentOffset),
      through = single ? anchorPrefix($to.parent, $to.parentOffset) : "";
    return {
      from,
      to,
      node_id: nodeId,
      text: single
        ? through.slice(before.length)
        : editor.state.doc.textBetween(from, to, "\n"),
      start: Array.from(before).length,
      end: Array.from(through).length,
      single,
    };
  }, [editor]);
  const loadComments = useCallback(async () => {
    const rows = await api(base(pid) + `/comments?section_id=${sectionId}`);
    setComments(rows);
    if (editor && !editor.isDestroyed)
      editor.view.dispatch(editor.state.tr.setMeta(commentKey, rows));
  }, [pid, sectionId, editor]);
  useEffect(() => {
    if (!editor) return;
    const selected = () => {
      const value = selection();
      setQuote(value?.text && value.single ? value : null);
    };
    editor.on("selectionUpdate", selected);
    selected();
    void task.run(loadComments);
    const focus = (event: Event) => {
      setPanel("comments");
      setTimeout(
        () =>
          document
            .getElementById(
              "studio-comment-" + (event as CustomEvent).detail.id,
            )
            ?.scrollIntoView({ block: "nearest" }),
        40,
      );
    };
    window.addEventListener("studio-comment-focus", focus);
    return () => {
      editor.off("selectionUpdate", selected);
      window.removeEventListener("studio-comment-focus", focus);
    };
  }, [editor, selection, loadComments]);
  const loadIntents = useCallback(
    async () =>
      setPendingIntents(
        await api(`/projects/${pid}/workspace-intents?section_id=${sectionId}`),
      ),
    [pid, sectionId],
  );
  useEffect(() => {
    void loadIntents();
    const changed = () => void loadIntents();
    window.addEventListener("paper-resource-changed", changed);
    return () => window.removeEventListener("paper-resource-changed", changed);
  }, [loadIntents]);
  const insertArtifact = async (value: Row) => {
    const asset = value.asset_version_id
      ? value
      : await api(
          base(pid) +
            "/assets/" +
            (value.artifact_id || value.id) +
            "/metadata",
        );
    port.insertAsset(
      { ...asset, artifact_id: value.artifact_id || value.id },
      !!canvasId,
    );
    setPanel("");
  };
  const consumeIntent = async (intent: Row) => {
    const doc = editor.getJSON();
    const exists = doc.content?.some(
      (node: Row) =>
        node.attrs?.artifactId === intent.artifact_id &&
        node.attrs?.assetVersionId === intent.asset_version_id,
    );
    if (!exists) {
      if (getRevision() !== intent.base_revision_id)
        throw new Error("目标正文已更新，请关闭此待办并重新选择图片");
      port.insertAsset({
        ...intent.artifact,
        asset_version_id: intent.asset_version_id,
        artifact_id: intent.artifact_id,
      });
    }
    if (!(await ensureSaved())) throw new Error("请保存正文后完成插入");
    await send(`/projects/${pid}/workspace-intents/${intent.id}/finish`, {
      revision_id: getRevision(),
    });
    await loadIntents();
  };
  const roots = comments.filter((row) => !row.data.parent_id);
  return (
    <section className="studio-editor-tools">
      <div className="studio-toolbar">
        <button className="btn" disabled={task.busy} onClick={() => void task.run(async () => {
          if (!await ensureSaved()) throw new Error('请先保存当前正文');
          navigateWorkspace(pid,{page:'products',sectionId});
        })}>复用项目产物</button>
        <button
          className="btn"
          onClick={() => setPanel(panel === "chat" ? "" : "chat")}
        >
          学术对话
        </button>
        <button
          className="btn"
          onClick={() => setPanel(panel === "comments" ? "" : "comments")}
        >
          选区评论（{roots.filter((row) => !row.data.resolved).length}）
        </button>
        <button
          className="btn"
          onClick={() => {
            const node = editor?.state.selection.node;
            if (
              node?.type.name === "inlineFormula" ||
              node?.type.name === "formula"
            ) {
              setFormula(node.attrs.latex || node.attrs.expression || "");
              setInline(node.type.name === "inlineFormula");
            }
            setPanel(panel === "math" ? "" : "math");
          }}
        >
          可视化公式
        </button>
        <button
          className="btn"
          onClick={() => {
            void task.run(async () => {
              const selected = editor?.state.selection.node;
              let artifact = assets.find(
                (item) => item.id === selected?.attrs.artifactId,
              );
              if (selected?.attrs.artifactId && !artifact)
                artifact = await api(
                  base(pid) +
                    "/assets/" +
                    selected.attrs.artifactId +
                    "/metadata",
                );
              let metadata = artifact?.metadata || {};
              if (typeof metadata === "string")
                try {
                  metadata = JSON.parse(metadata);
                } catch {}
              setCanvasId(metadata.studio_canvas_id || "");
              setPanel(panel === "canvas" ? "" : "canvas");
            });
          }}
        >
          自由画布
        </button>
      </div>
      <TaskStatus task={task} />
      {pendingIntents.map((intent) => (
        <p className="studio-card" key={intent.id}>
          待插入：{intent.artifact.name} ·{" "}
          {intent.current ? "目标正文版本一致" : "目标正文已变化"}
          <button
            className="btn"
            disabled={task.busy}
            onClick={() => void task.run(() => consumeIntent(intent))}
          >
            插入到当前光标
          </button>
          <button
            className="text-button"
            onClick={() =>
              void task.run(async () => {
                await send(
                  `/projects/${pid}/workspace-intents/${intent.id}/finish`,
                  { dismiss: true },
                );
                await loadIntents();
              })
            }
          >
            忽略
          </button>
        </p>
      ))}
      <CompletionTool pid={pid} sectionId={sectionId} port={port} />
      {panel === "chat" && (
        <StudioChat
          pid={pid}
          files={files}
          assets={assets}
          initialText={quote?.text ? `请讨论这段文字：\n${quote.text}` : ""}
          onUseImage={(id) => void insertArtifact({ artifact_id: id })}
          onDocumentAdopted={onDocumentAdopted}
          getDocumentContext={async () => {
            if (!(await ensureSaved())) throw new Error("请先保存当前正文");
            return { section_id: sectionId, revision_id: getRevision() };
          }}
        />
      )}
      {panel === "math" && (
        <FormulaTool
          value={formula}
          onChange={setFormula}
          inline={inline}
          onInlineChange={setInline}
          onInsert={() => {
            const selected = editor.state.selection.node;
            const type = inline ? "inlineFormula" : "formula";
            if (selected?.type.name === type)
              editor
                .chain()
                .focus()
                .updateAttributes(type, {
                  latex: formula,
                  expression: formula,
                  omml: "",
                })
                .run();
            else
              editor
                .chain()
                .focus()
                .insertContent({
                  type,
                  attrs: {
                    latex: formula,
                    expression: formula,
                    nodeId: crypto.randomUUID(),
                  },
                })
                .run();
            setPanel("");
          }}
        />
      )}
      {panel === "canvas" && (
        <CanvasEditor
          pid={pid}
          identity={canvasId}
          onInsert={(value) => void insertArtifact(value)}
        />
      )}
      {panel === "comments" && (
        <CommentPanel
          pid={pid}
          sectionId={sectionId}
          port={port}
          quote={quote}
          selection={selection}
          comments={comments}
          onChanged={loadComments}
          onLocate={(id) =>
            editor.view.dom
              .querySelector(`[data-node-id="${CSS.escape(id)}"]`)
              ?.scrollIntoView({ behavior: "smooth", block: "center" })
          }
        />
      )}
    </section>
  );
}

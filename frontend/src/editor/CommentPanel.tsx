import { useState } from "react";
import { send } from "../api/client";
import { useCommand } from "../tasks/useCommand";
import { TaskStatus } from "../research/shared";
import type { Row } from "../workspace/types";
import type { EditorPort } from "./EditorPort";
export default function CommentPanel({
  pid,
  sectionId,
  port,
  quote,
  selection,
  comments,
  onChanged,
  onLocate,
}: {
  pid: string;
  sectionId: string;
  port: EditorPort;
  quote: Row | null;
  selection: () => Row | null;
  comments: Row[];
  onChanged: () => Promise<void>;
  onLocate: (id: string) => void;
}) {
  const [text, setText] = useState(""),
    [replies, setReplies] = useState<Row>({}),
    command = useCommand(onChanged, pid + ":comments:" + sectionId);
  const base = `/projects/${pid}/writing/comments`;
  const add = async () => {
    if (!(await port.save())) throw new Error("请先保存当前正文");
    const current = selection();
    if (!current?.text || !current.single)
      throw new Error("请重新选择同一段落内的文字");
    await send(base, {
      section_id: sectionId,
      revision_id: port.revision(),
      node_id: current.node_id,
      quote: current.text,
      start: current.start,
      end: current.end,
      text,
    });
    setText("");
  };
  return (
    <div className="studio-comment-panel">
      <h3>正文评论</h3>
      <blockquote>
        {quote?.text || "在正文中选中同一段落内的一句话，再填写评论。"}
      </blockquote>
      <textarea
        aria-label="选区评论内容"
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={3}
      />
      <button
        className="btn primary"
        disabled={command.busy || !text.trim() || !quote}
        onClick={() => void command.run(add)}
      >
        添加选区评论
      </button>
      <TaskStatus task={command} />
      {comments
        .filter((row) => !row.data.parent_id)
        .map((root) => (
          <article
            id={"studio-comment-" + root.id}
            key={root.id}
            className="studio-card"
          >
            <blockquote>{root.data.quote}</blockquote>
            <p>{root.data.text}</p>
            <small>
              {root.current ? "原文位置有效" : "选区已变化，需重新核对"} ·{" "}
              {root.data.resolved ? "已解决" : "待处理"}
            </small>
            <div className="studio-toolbar">
              <button
                className="btn"
                onClick={() => onLocate(root.data.node_id)}
              >
                定位原文
              </button>
              <button
                className="btn"
                onClick={() =>
                  void command.run(() =>
                    send(
                      `/projects/${pid}/research-records/${root.id}`,
                      {
                        revision: root.revision,
                        data: { resolved: !root.data.resolved },
                      },
                      "PUT",
                    ),
                  )
                }
              >
                {root.data.resolved ? "重新打开" : "解决评论"}
              </button>
              <button
                className="btn"
                disabled={command.busy || !root.current}
                onClick={() =>
                  void command.run(() => send(base + `/${root.id}/reply`))
                }
              >
                AI 回复（计费）
              </button>
            </div>
            {comments
              .filter((row) => row.data.parent_id === root.id)
              .map((row) => (
                <p className="studio-comment-reply" key={row.id}>
                  <strong>{row.data.author === "ai" ? "AI" : "回复"}：</strong>
                  {row.data.text}
                </p>
              ))}
            <textarea
              aria-label="回复评论"
              value={replies[root.id] || ""}
              onChange={(event) =>
                setReplies({ ...replies, [root.id]: event.target.value })
              }
            />
            <button
              className="btn"
              disabled={!replies[root.id]?.trim() || command.busy}
              onClick={() =>
                void command.run(async () => {
                  await send(base, {
                    section_id: sectionId,
                    parent_id: root.id,
                    text: replies[root.id],
                  });
                  setReplies({ ...replies, [root.id]: "" });
                })
              }
            >
              保存回复
            </button>
          </article>
        ))}
    </div>
  );
}

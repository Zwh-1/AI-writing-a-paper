import { useEffect, useState } from "react";
import { send } from "../api/client";
import { useCommand } from "../tasks/useCommand";
import { TaskStatus } from "../research/shared";
import type { Row } from "../workspace/types";
import type { EditorPort } from "./EditorPort";
import EvidenceTrace from "../writing/EvidenceTrace";
export default function CompletionTool({
  pid,
  sectionId,
  port,
}: {
  pid: string;
  sectionId: string;
  port: EditorPort;
}) {
  const [automatic, setAutomatic] = useState(false),
    [suggestion, setSuggestion] = useState<Row | null>(null),
    [message, setMessage] = useState("");
  const command = useCommand(undefined, pid + ":completion:" + sectionId);
  const complete = async () => {
    let captured: Row | null = null;
    const value = await command.run(async () => {
      if (!(await port.save())) throw new Error("请先保存当前正文");
      captured = port.completionContext();
      if (!captured) return null;
      return send(`/projects/${pid}/writing/completion`, {
        section_id: sectionId,
        revision_id: port.revision(),
        node_id: captured.node_id,
        prefix: captured.prefix,
        suffix: captured.suffix,
      });
    });
    if (value && captured) setSuggestion({ ...value, ...(captured as Row) });
  };
  useEffect(() => {
    if (!automatic) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const remove = port.subscribeUpdates(() => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (!command.busy) void complete();
      }, 3000);
    });
    return () => {
      clearTimeout(timer);
      remove();
    };
  }, [automatic, port.editor, command.busy]);
  return (
    <div className="completion-tool">
      <div className="studio-toolbar">
        <button
          className="btn"
          disabled={command.busy}
          onClick={() => void complete()}
        >
          补全当前位置（计费）
        </button>
        <label>
          <input
            type="checkbox"
            checked={automatic}
            onChange={(e) => setAutomatic(e.target.checked)}
          />
          输入后补全（计费）
        </label>
      </div>
      <TaskStatus task={command} />
      {message && <p role="status">{message}</p>}
      {suggestion && (
        <div className="studio-completion">
          <strong>补全建议</strong>
          <p>{suggestion.text}</p>
          <EvidenceTrace pid={pid} sectionId={sectionId} bundle={suggestion.evidence_bundle} quality={suggestion.writing_quality} argumentPlan={suggestion.argument_plan} coverage={suggestion.context_coverage} compact />
          <button
            className="btn primary"
            disabled={suggestion.can_insert === false}
            onClick={() => {
              if (!port.insertCompletion(suggestion))
                setMessage("正文已变化，请重新生成建议");
              setSuggestion(null);
            }}
          >
            接受
          </button>
          <button className="btn" onClick={() => setSuggestion(null)}>
            忽略
          </button>
          <small>{suggestion.can_insert === false ? "来源或主张映射尚有阻断项，请先补证或重新生成。" : "正文版本或位置变化时不会采用旧建议。"}</small>
        </div>
      )}
    </div>
  );
}

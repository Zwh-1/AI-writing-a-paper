import { useEffect, useState } from "react";
import { api, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
const fresh = () => ({
  title: "",
  prompt: "",
  capabilities: ["knowledge", "document", "assets"],
  model_profile: "primary",
});
export default function AgentSettings({
  pid,
  onSaved,
}: {
  pid: string;
  onSaved: (id?: string) => Promise<void>;
}) {
  const [items, setItems] = useState<Row[]>([]),
    [selected, setSelected] = useState<Row | null>(null),
    [value, setValue] = useState<Row>(fresh);
  const task = useStudioTask();
  const load = async () =>
    setItems(await api(base(pid) + "/records?kind=agent&limit=500"));
  useEffect(() => {
    void task.run(load);
  }, [pid]);
  return (
    <div className="studio-card">
      <h3>学术智能体设置</h3>
      <label>
        已有自定义智能体
        <select
          aria-label="编辑已有智能体"
          value={selected?.id || ""}
          onChange={(event) => {
            const row =
              items.find((item) => item.id === event.target.value) || null;
            setSelected(row);
            setValue(row?.data || fresh());
          }}
        >
          <option value="">创建新智能体</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.data.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        名称
        <input
          aria-label="智能体名称"
          value={value.title}
          onChange={(event) =>
            setValue({ ...value, title: event.target.value })
          }
        />
      </label>
      <label>
        职责与写作偏好
        <textarea
          aria-label="智能体职责"
          value={value.prompt}
          onChange={(event) =>
            setValue({ ...value, prompt: event.target.value })
          }
        />
      </label>
      <label>
        模型路线
        <select
          value={value.model_profile || "primary"}
          onChange={(event) =>
            setValue({ ...value, model_profile: event.target.value })
          }
        >
          <option value="primary">主模型</option>
          <option value="gpt">GPT 备选</option>
        </select>
      </label>
      <div className="studio-toolbar">
        {[
          ["knowledge", "知识检索"],
          ["assets", "读取研究资源"],
          ["document", "读取文献和正文"],
          ["document_edit", "提出正文改写"],
          ["notes", "读取个人随记"],
        ].map(([id, label]) => (
          <label key={id}>
            <input
              type="checkbox"
              checked={value.capabilities.includes(id)}
              onChange={(event) =>
                setValue({
                  ...value,
                  capabilities: event.target.checked
                    ? [...value.capabilities, id]
                    : value.capabilities.filter((item: string) => item !== id),
                })
              }
            />
            {label}
          </label>
        ))}
      </div>
      <div className="studio-toolbar">
        <button
          className="btn primary"
          disabled={task.busy || !value.title.trim()}
          onClick={() =>
            void task.run(async () => {
              const row = selected
                ? await send(
                    base(pid) + `/records/${selected.id}`,
                    { revision: selected.revision, data: value },
                    "PUT",
                  )
                : await send(base(pid) + "/records", {
                    kind: "agent",
                    data: value,
                  });
              setSelected(row);
              await load();
              await onSaved(row.id);
            })
          }
        >
          保存智能体
        </button>
        {selected && (
          <button
            className="btn"
            disabled={task.busy}
            onClick={() =>
              void task.run(async () => {
                await send(
                  base(pid) +
                    `/records/${selected.id}?revision=${selected.revision}`,
                  {},
                  "DELETE",
                );
                setSelected(null);
                setValue(fresh());
                await load();
                await onSaved();
              })
            }
          >
            删除智能体
          </button>
        )}
      </div>
      <TaskStatus task={task} />
    </div>
  );
}

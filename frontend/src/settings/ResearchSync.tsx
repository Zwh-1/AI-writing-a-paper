import { useState } from "react";
import { api, send } from "../api/client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import { useCommand } from "../tasks/useCommand";
import type { Row } from "../workspace/types";
export default function ResearchSync({ pid }: { pid: string }) {
  const conflicts = useWorkspaceResource<Row[]>(
      `/projects/${pid}/research-sync/conflicts`,
      0,
    ),
    capabilities = useWorkspaceResource<Row>(
      `/projects/${pid}/capabilities`,
      0,
    );
  const [result, setResult] = useState<Row | null>(null),
    command = useCommand(undefined, pid + ":research-sync");
  const download = async () => {
    const packet = await send(`/projects/${pid}/research-sync/snapshot`, {});
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(packet, null, 2)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "research-sync.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <section className="panel">
      <h2>研究记录协作</h2>
      <p>
        先用完整知识包建立共同项目，再合并对话、随记、批注和画布记录。消息追加保存；编辑冲突保留两侧内容供比较。
      </p>
      <div className="studio-toolbar">
        <button
          className="btn"
          disabled={command.busy}
          onClick={() => void command.run(download)}
        >
          导出研究记录
        </button>
        <label className="btn">
          导入研究记录
          <input
            type="file"
            accept=".json,application/json"
            disabled={command.busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file)
                void command.run(async () => {
                  if (file.size > 20 * 1024 * 1024)
                    throw new Error("同步文件超过20MB");
                  const value = await send(
                    `/projects/${pid}/research-sync/apply`,
                    JSON.parse(await file.text()),
                  );
                  setResult(value);
                  await conflicts.load();
                });
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {result && (
        <p>
          已合并 {result.merged?.length || 0} 项，待处理冲突{" "}
          {result.conflicts?.length || 0} 项。
        </p>
      )}
      {conflicts.data?.map((row) => (
        <article className="studio-card" key={row.id}>
          <h3>{row.local.kind} 的编辑冲突</h3>
          <div className="studio-two-col">
            <pre>
              {JSON.stringify(
                typeof row.local.data === "string"
                  ? JSON.parse(row.local.data)
                  : row.local.data,
                null,
                2,
              )}
            </pre>
            <pre>
              {JSON.stringify(
                typeof row.remote.data === "string"
                  ? JSON.parse(row.remote.data)
                  : row.remote.data,
                null,
                2,
              )}
            </pre>
          </div>
          {[
            ["local", "保留本地"],
            ["remote", "采用远端"],
            ["copy_remote", "远端另存一份"],
          ].map(([choice, label]) => (
            <button
              className="btn"
              key={choice}
              disabled={
                command.busy ||
                (choice === "remote" && row.local.kind === "message")
              }
              onClick={() =>
                void command.run(async () => {
                  await send(
                    `/projects/${pid}/research-sync/conflicts/${row.id}`,
                    { choice, local_hash: row.local_hash },
                  );
                  await conflicts.load();
                })
              }
            >
              {label}
            </button>
          ))}
        </article>
      ))}
      {command.error && <p role="alert">{command.error}</p>}
      {capabilities.data && (
        <p>
          支持格式：{capabilities.data.exports.formats.join("、")} ·
          当前解析可检索 · 凭证保存在本机。
        </p>
      )}
    </section>
  );
}

import { useEffect,useState } from "react";
import { api, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "./shared";
export default function RecordHistory({
  pid,
  record,
  onRestored,
}: {
  pid: string;
  record: Row;
  onRestored: (row: Row) => void;
}) {
  const [versions, setVersions] = useState<Row[]>([]),
    [remoteHistory, setRemoteHistory] = useState<Row[]>([]),
    [open, setOpen] = useState(false);
  const task = useStudioTask();
  useEffect(()=>{
    setVersions([]);setRemoteHistory([]);
    if(!open)return;
    let alive=true;
    void Promise.all([api(base(pid)+`/records/${record.id}/versions`),api(base(pid)+`/records/${record.id}/sync-history`)]).then(([local,remote])=>{
      if(alive){setVersions(local);setRemoteHistory(remote);}
    }).catch(()=>{});
    return()=>{alive=false;};
  },[pid,record.id,record.revision,open]);
  return (
    <section>
      <button
        className="btn"
        disabled={task.busy}
        onClick={() => {
          setOpen(!open);
          if (!open)
            void task.run(async () => {
              const [local, remote] = await Promise.all([
                api(base(pid) + `/records/${record.id}/versions`),
                api(base(pid) + `/records/${record.id}/sync-history`),
              ]);
              setVersions(local);
              setRemoteHistory(remote);
            });
        }}
      >
        查看版本与恢复
      </button>
      {open && (
        <div className="studio-card">
          {versions.map((item) => (
            <details key={item.revision}>
              <summary>
                版本 {item.revision} ·{" "}
                {new Date(item.created_at).toLocaleString()}{" "}
                {item.current ? "（当前）" : ""}
              </summary>
              <pre>
                {item.data.text ||
                  item.data.title ||
                  JSON.stringify(item.data.scene)}
              </pre>
              {!item.current && (
                <button
                  className="btn"
                  disabled={task.busy}
                  onClick={() =>
                    void task.run(async () => {
                      const restored = await send(
                        base(pid) + `/records/${record.id}/restore`,
                        {
                          revision: record.revision,
                          target_revision: item.revision,
                        },
                      );
                      onRestored(restored);
                      setVersions(
                        await api(base(pid) + `/records/${record.id}/versions`),
                      );
                    })
                  }
                >
                  从此版本创建恢复版本
                </button>
              )}
            </details>
          ))}
          {remoteHistory.flatMap((branch) =>
            [...(branch.snapshot.versions || []), branch.snapshot].map(
              (item: Row) => {
                let data: Row;
                try {
                  data =
                    typeof item.data === "string"
                      ? JSON.parse(item.data)
                      : item.data;
                } catch {
                  data = { text: item.data };
                }
                return (
                  <details key={branch.id + ":" + item.revision}>
                    <summary>远端分支 · 版本 {item.revision}</summary>
                    <pre>
                      {data.text || data.title || JSON.stringify(data.scene)}
                    </pre>
                    {["note", "agent", "canvas"].includes(record.kind) && (
                      <button
                        className="btn"
                        disabled={task.busy}
                        onClick={() =>
                          void task.run(async () => {
                            const restored = await send(
                              base(pid) + `/records/${record.id}/restore-sync`,
                              {
                                history_id: branch.id,
                                target_revision: item.revision,
                                revision: record.revision,
                              },
                            );
                            onRestored(restored);
                            setVersions(
                              await api(
                                base(pid) + `/records/${record.id}/versions`,
                              ),
                            );
                          })
                        }
                      >
                        从远端历史创建恢复版本
                      </button>
                    )}
                  </details>
                );
              },
            ),
          )}
          <TaskStatus task={task} />
        </div>
      )}
    </section>
  );
}

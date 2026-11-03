import { useState } from "react";
import { send } from "../api/client";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import { navigateWorkspace } from "../workspace/WorkspaceContext";
import { useCommand } from "../tasks/useCommand";
import type { Row } from "../workspace/types";
import Assets from "./Assets";
import ProductLink from '../products/ProductLink';
import CanvasEditor from "./CanvasEditor";
export default function AssetWorkspace({ pid }: { pid: string }) {
  const [tab, setTab] = useState("assets"),
    [canvas, setCanvas] = useState(""),
    [section, setSection] = useState("");
  const canvases = useWorkspaceResource<Row[]>(
      `/projects/${pid}/studio/records?kind=canvas`,
      0,
    ),
    sections = useWorkspaceResource<Row[]>(`/projects/${pid}/sections`, 0);
  const command = useCommand(undefined, pid + ":asset-insertion");
  return (
    <section className="panel">
      <h2>共享资产与自由画布</h2>
      <div className="studio-toolbar">
        <ProductLink pid={pid} sectionId={section || undefined} />
        <button className="btn" onClick={() => setTab("assets")}>
          资产库
        </button>
        <button className="btn" onClick={() => setTab("canvas")}>
          自由画布
        </button>
      </div>
      {tab === "assets" ? (
        <Assets
          pid={pid}
          onRefresh={canvases.load}
          onEditCanvas={(id) => {
            setCanvas(id);
            setTab("canvas");
          }}
        />
      ) : (
        <>
          <div className="studio-toolbar">
            <label>
              画布
              <select
                value={canvas}
                onChange={(event) => setCanvas(event.target.value)}
              >
                <option value="">新建画布</option>
                {canvases.data?.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.data.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              目标章节
              <select
                value={section}
                onChange={(event) => setSection(event.target.value)}
              >
                <option value="">选择章节</option>
                {sections.data
                  ?.filter((row) => row.state !== "archived")
                  .map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.title}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          <CanvasEditor
            key={canvas}
            pid={pid}
            identity={canvas}
            onInsert={(asset) => {
              if (section)
                void command.run(async () => {
                  await send(`/projects/${pid}/workspace-intents`, {
                    section_id: section,
                    artifact_id: asset.artifact_id,
                  });
                  navigateWorkspace(pid, {
                    page: "editor",
                    sectionId: section,
                  });
                });
              else void canvases.load();
            }}
          />
        </>
      )}
      {command.error && <p role="alert">{command.error}</p>}
    </section>
  );
}

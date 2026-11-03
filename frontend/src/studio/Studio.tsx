import Delivery from "../exports/ResearchDelivery";
import ProductLink from '../products/ProductLink';
import {
  navigateWorkspace,
  useWorkspaceLocation,
  readLocation,
} from "../workspace/WorkspaceContext";
import { LiteratureLibrary, Discover } from "../references/LiteratureIntake";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, apiUrl, send } from "../client";
import { base, Row, TaskStatus, useStudioTask } from "../research/shared";
import StudioReader from "../reading/Reader";
import { StudioChat, StudioNotes } from "../assistant/ChatNotes";
import CanvasEditor from "../assets/CanvasEditor";
import StudioSettings from "../settings/ResearchSettings";
import StudioAssets from "../assets/Assets";
import "./studio.css";
import "../workspace/workspace.css";

export default function ResearchStudio({
  pid,
  onOpenSection,
  navigation,
}: {
  pid: string;
  onOpenSection: (id: string) => void;
  navigation?: ReactNode;
}) {
  const workspace = useWorkspaceLocation(pid, "studio");
  const [tab, setTab] = useState(workspace.location.view || "library"),
    [library, setLibrary] = useState<Row>({ items: [], tags: [], files: [] }),
    [assets, setAssets] = useState<Row[]>([]),
    [sections, setSections] = useState<Row[]>([]),
    [sectionId, setSectionId] = useState(workspace.location.sectionId || ""),
    [canvases, setCanvases] = useState<Row[]>([]),
    [canvasId, setCanvasId] = useState(workspace.location.canvasId || ""),
    [readFile, setReadFile] = useState(workspace.location.fileId || ""),
    [ask, setAsk] = useState<Row>({}),
    [notesTick, setNotesTick] = useState(0);
  const task = useStudioTask();
  const load = useCallback(async () => {
    const [books, resources, chapters, drawings] = await Promise.all([
      api(base(pid) + "/library"),
      api(`/projects/${pid}/artifacts`),
      api(`/projects/${pid}/sections`),
      api(base(pid) + "/records?kind=canvas"),
    ]);
    setLibrary(books);
    setAssets(resources);
    setSections(chapters);
    setCanvases(drawings);
    setSectionId((value) => value || chapters[0]?.id || "");
  }, [pid]);
  useEffect(() => {
    void task.run(load);
    setReadFile(readLocation(pid,'studio').fileId || "");
    setCanvasId(readLocation(pid,'studio').canvasId || "");
  }, [load]);
  useEffect(() => {
    workspace.update({ view: tab, fileId: readFile, sectionId,canvasId });
  }, [tab, readFile, sectionId,canvasId]);
  const useImage = (id: string) => {
    if (!sectionId) return;
    void task.run(async () => {
      await send(`/projects/${pid}/workspace-intents`, {
        section_id: sectionId,
        artifact_id: id,
      });
      navigateWorkspace(pid, { page: "editor", sectionId });
      onOpenSection(sectionId);
    });
  };
  return (
    <div className="research-studio">
      <header className="studio-heading workspace-heading">
        <div>
          <h1>研究助手</h1>
          <p>连接文献、阅读、讨论与写作，把过程保留在当前项目。</p>
        </div>
        <button
          className="btn"
          disabled={task.busy}
          onClick={() => void task.run(load)}
        >
          刷新工作区
        </button>
      </header>
      {navigation}
      <div className="studio-toolbar"><ProductLink pid={pid} sectionId={sectionId || undefined}/></div>
      <div className="studio-tabs" role="tablist" aria-label="研究助手功能">
        {[
          ["library", "文献接入"],
          ["read", "论文阅读"],
          ["chat", "学术对话"],
          ["notes", "思维随记"],
          ["assets", "资产库"],
          ["canvas", "自由画布"],
          ["discover", "文献发现"],
          ["delivery", "TeX 与数据"],
          ["settings", "解析与运行"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <TaskStatus task={task} />
      {["chat", "canvas"].includes(tab) && (
        <div className="studio-toolbar">
          <label>
            图片用于章节
            <select
              aria-label="图片目标章节"
              value={sectionId}
              onChange={(e) => setSectionId(e.target.value)}
            >
              <option value="">先选择章节</option>
              {sections
                .filter((row) => row.state !== "archived")
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.title}
                  </option>
                ))}
            </select>
          </label>
        </div>
      )}
      {tab === "library" && (
        <LiteratureLibrary
          pid={pid}
          library={library}
          onRefresh={load}
          onRead={(fid) => {
            setReadFile(fid);
            setTab("read");
          }}
        />
      )}
      {tab === "read" && (
        <StudioReader
          pid={pid}
          files={library.files}
          initialFile={readFile}
          onAsk={(fid, text) => {
            setAsk({
              file: fid,
              text: `请解释这段原文，保留适用条件：\n${text}`,
            });
            setTab("chat");
          }}
        />
      )}
      {tab === "chat" && (
        <StudioChat
          pid={pid}
          files={library.files}
          assets={assets}
          initialFile={ask.file}
          initialText={ask.text}
          onSavedNote={() => setNotesTick((value) => value + 1)}
          onUseImage={useImage}
        />
      )}
      {tab === "notes" && <StudioNotes pid={pid} tick={notesTick} />}
      {tab === "assets" && <StudioAssets pid={pid} onRefresh={load} />}
      {tab === "canvas" && (
        <>
          <div className="studio-toolbar">
            <select
              aria-label="已存画布"
              value={canvasId}
              onChange={(e) => setCanvasId(e.target.value)}
            >
              <option value="">新建画布</option>
              {canvases.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.data.title}
                </option>
              ))}
            </select>
            <button
              className="btn"
              onClick={() => {
                setCanvasId("");
              }}
            >
              新建画布
            </button>
            <button className="btn" onClick={() => void task.run(load)}>
              刷新画布列表
            </button>
          </div>
          <CanvasEditor
            key={canvasId || "new"}
            pid={pid}
            identity={canvasId}
            onInsert={(value) => useImage(value.artifact_id)}
          />
        </>
      )}
      {tab === "discover" && <Discover pid={pid} onRefresh={load} />}
      {tab === "delivery" && <Delivery pid={pid} />}
      {tab === "settings" && (
        <StudioSettings pid={pid} files={library.files} onRefresh={load} />
      )}
    </div>
  );
}

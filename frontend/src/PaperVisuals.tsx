import { useEffect, useRef, useState } from "react";
import {
  ImagePlus,
  RefreshCw,
  Sparkles,
  Download,
  Save,
  Plus,
  X,
  FileUp,
  Minus,
  Undo2,
} from "lucide-react";
import { api, send, ApiError } from "./client";
import WorkspaceDialog from "./workspace/WorkspaceDialog";
import ImageGenerationSettings from "./ImageGenerationSettings";
import FigureStandardsPanel from "./FigureStandardsPanel";
import "./paperVisuals.css";

type Row = Record<string, any>;
type Props = {
  pid: string;
  sectionId?: string;
  onPrepare?: () => Promise<boolean>;
  getAnchor?: () => string | undefined;
  onInsert?: (node: Row, asset: Row) => Promise<boolean>;
  onSaved: () => void;
};
const labels: Record<string, string> = {
  flowchart: "流程图",
  use_case: "用例图",
  er: "ER 图",
  three_line_table: "三线表",
  architecture: "架构图",
  module: "功能模块图",
  class_diagram: "类图",
  sequence: "时序图",
  data_flow: "数据流图",
  contract_interaction: "合约交互图",
  circuit: "电路设计图",
  structure: "结构示意图",
};
const supportedKind = (value: unknown): value is string =>
  typeof value === "string" &&
  Object.prototype.hasOwnProperty.call(labels, value);
const json = (value: unknown) => JSON.stringify(value, null, 2);
const read = (value: any, fallback: any = {}) => {
  try {
    return typeof value === "string" ? JSON.parse(value) : (value ?? fallback);
  } catch {
    return fallback;
  }
};

export default function PaperVisualsButton(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="btn"
        onClick={async () => {
          if (props.onPrepare && !(await props.onPrepare())) return;
          setOpen(true);
        }}
      >
        <ImagePlus size={16} />
        论文图表
      </button>
      {open && (
        <Studio key={props.pid} {...props} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

function Studio({
  pid,
  sectionId,
  onInsert,
  onSaved,
  onClose,
  getAnchor,
}: Props & { onClose: () => void }) {
  const [templates, setTemplates] = useState<Row>({});
  const [kind, setKind] = useState("flowchart");
  const [code, setCode] = useState("");
  const [preview, setPreview] = useState<Row | null>(null);
  const [previewCode, setPreviewCode] = useState("");
  const [saved, setSaved] = useState<Row | null>(null);
  const [savedCode, setSavedCode] = useState("");
  const [library, setLibrary] = useState<Row[]>([]);
  const [sections, setSections] = useState<Row[]>([]);
  const [sid, setSid] = useState(sectionId ?? "");
  const [instruction, setInstruction] = useState("");
  const [context, setContext] = useState<Row>({});
  const [task, setTask] = useState<Row | null>(null);
  const [candidateTask, setCandidateTask] = useState("");
  const [missing, setMissing] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showSource, setShowSource] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [undoCode, setUndoCode] = useState("");
  const [rawResult, setRawResult] = useState("");
  const [loadVersion, setLoadVersion] = useState(0);
  const [imageReady, setImageReady] = useState(false);
  const [imageTitle, setImageTitle] = useState("");
  const [explanation, setExplanation] = useState("");
  const [figurePurpose, setFigurePurpose] = useState("design_schematic");
  const [relatedSections, setRelatedSections] = useState<string[]>([]);
  const [positions, setPositions] = useState<Row>({ blocks: [] });
  const [anchorId, setAnchorId] = useState(getAnchor?.() ?? "");
  const [requiredLabels, setRequiredLabels] = useState("");
  const [forbiddenContent, setForbiddenContent] = useState("");
  const [layout, setLayout] = useState("auto");
  const [useBlueprint, setUseBlueprint] = useState(false);
  const [briefPreview, setBriefPreview] = useState<Row | null>(null);
  const [outputChecked, setOutputChecked] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [placementError, setPlacementError] = useState("");
  const [projectFigurePolicy, setProjectFigurePolicy] = useState<Row | null>(
    null,
  );
  const alive = useRef(true);
  const initialized = useRef(false);
  const mutation = useRef(false);
  const drafts = useRef<Row>({});
  const saveRequest = useRef({ fingerprint: "", id: "" });
  const imageRequest = useRef({ fingerprint: "", id: "" });
  const sourceFile = useRef<HTMLInputElement>(null);
  const prefix = `/projects/${pid}/paper-visuals`;
  const draftKey = `paper_visual_drafts_${pid}_${sectionId ?? "research"}`;
  const taskKey = `paper_visual_task_${pid}_${sectionId ?? "research"}`;
  const spec = read(code, null);
  const nativeImage = Boolean(
    saved && read(saved.metadata).render_engine === "gpt-image-2",
  );
  const protectedPurpose = [
    "original_result",
    "experimental_result",
    "interface_screenshot",
    "test_capture",
  ].includes(figurePurpose);
  const tableEditable =
    spec?.kind === "three_line_table" &&
    typeof spec.title === "string" &&
    (spec.note == null || typeof spec.note === "string") &&
    Array.isArray(spec.headers) &&
    spec.headers.length >= 1 &&
    spec.headers.length <= 10 &&
    spec.headers.every((h: any) => typeof h === "string") &&
    Array.isArray(spec.rows) &&
    spec.rows.length <= 100 &&
    spec.rows.every(
      (r: any) =>
        Array.isArray(r) &&
        r.length === spec.headers.length &&
        r.every(
          (v: any) =>
            v == null ||
            typeof v === "string" ||
            (typeof v === "number" && Number.isFinite(v)),
        ),
    );
  const ready = Boolean(preview && code === previewCode);
  const savedCurrent = Boolean(
    saved &&
    code === savedCode &&
    (read(saved.metadata).explanation ?? "") === explanation.trim() &&
    (read(saved.metadata).figure_purpose ?? "design_schematic") ===
      figurePurpose &&
    JSON.stringify(read(saved.metadata).related_section_ids ?? []) ===
      JSON.stringify(relatedSections) &&
    (!ready ||
      (kind === "three_line_table"
        ? preview?.renderer_version === read(saved.metadata).renderer_version
        : preview?.render_hash === saved.sha256)),
  );
  const disabled = busy || Boolean(task);
  const snapshot = () => ({
    code,
    context,
    saved,
    savedId: saved?.id,
    savedCode,
    preview,
    previewCode,
    instruction,
    missing,
    candidateTask,
    undoCode,
    imageTitle,
    explanation,
    figurePurpose,
    relatedSections,
    requiredLabels,
    forbiddenContent,
    layout,
    useBlueprint,
  });
  const persistDrafts = () => {
    const portable = Object.fromEntries(
      Object.entries(drafts.current).map(([key, value]) => {
        const draft = value as Row;
        return [
          key,
          {
            code: draft.code,
            context: draft.context,
            savedId: draft.saved?.id ?? draft.savedId,
            savedCode: draft.savedCode,
            instruction: draft.instruction,
            missing: draft.missing,
            candidateTask: draft.candidateTask,
            undoCode: draft.undoCode,
            imageTitle: draft.imageTitle,
            explanation: draft.explanation,
            figurePurpose: draft.figurePurpose,
            relatedSections: draft.relatedSections,
            requiredLabels: draft.requiredLabels,
            forbiddenContent: draft.forbiddenContent,
            layout: draft.layout,
            useBlueprint: draft.useBlueprint,
          },
        ];
      }),
    );
    try {
      localStorage.setItem(draftKey, json({ active: kind, drafts: portable }));
    } catch {}
  };
  const cacheDraft = () => {
    if (initialized.current) {
      drafts.current[kind] = snapshot();
      persistDrafts();
    }
  };
  const applyDraft = (
    next: string,
    draft: Row | undefined,
    examples = templates,
    assets = library,
  ) => {
    setKind(next);
    setCode(
      draft && typeof draft.code === "string"
        ? draft.code
        : json(examples[next]),
    );
    const asset =
      draft?.saved ?? assets.find((a: Row) => a.id === draft?.savedId) ?? null;
    const assetMeta = read(asset?.metadata);
    const restoreImage =
      assetMeta.render_engine === "gpt-image-2" &&
      draft?.code === draft?.savedCode;
    if (restoreImage && assetMeta.image_brief?.target) {
      setAnchorId(assetMeta.image_brief.target.node_id);
      if (!sectionId) setSid(assetMeta.image_brief.target.section_id);
    }
    setSaved(asset);
    setSavedCode(draft?.savedCode ?? "");
    setContext(draft?.context ?? {});
    setPreview(
      draft?.preview ??
        (restoreImage
          ? {
              spec: assetMeta.spec,
              image: `/api/v1/images/${asset.sha256}`,
              width: assetMeta.width,
              height: assetMeta.height,
              render_hash: asset.sha256,
              renderer_version: assetMeta.renderer_version,
            }
          : null),
    );
    setPreviewCode(
      draft?.previewCode ?? (restoreImage ? (draft?.code ?? "") : ""),
    );
    setInstruction(draft?.instruction ?? "");
    setMissing(draft?.missing ?? []);
    setCandidateTask(draft?.candidateTask ?? "");
    setUndoCode(draft?.undoCode ?? "");
    setImageTitle(draft?.imageTitle ?? "");
    setExplanation(draft?.explanation ?? assetMeta.explanation ?? "");
    setFigurePurpose(
      draft?.figurePurpose ?? assetMeta.figure_purpose ?? "design_schematic",
    );
    setRelatedSections(
      draft?.relatedSections ?? assetMeta.related_section_ids ?? [],
    );
    setRequiredLabels(
      draft?.requiredLabels ??
        (assetMeta.image_brief?.required_labels ?? []).join("\n"),
    );
    setForbiddenContent(
      draft?.forbiddenContent ?? assetMeta.image_brief?.forbidden_content ?? "",
    );
    setLayout(draft?.layout ?? "auto");
    setUseBlueprint(draft?.useBlueprint ?? false);
    setBriefPreview(null);
    setZoom(1);
    setRawResult("");
    setError("");
    setMessage("");
    setShowSource(false);
  };
  useEffect(() => {
    cacheDraft();
  }, [
    kind,
    code,
    context,
    saved,
    savedCode,
    preview,
    previewCode,
    instruction,
    missing,
    candidateTask,
    undoCode,
    imageTitle,
    explanation,
    figurePurpose,
    relatedSections,
    requiredLabels,
    forbiddenContent,
    layout,
    useBlueprint,
  ]);
  useEffect(() => {
    let active = true;
    setPositions({ blocks: [] });
    if (sid)
      api(`${prefix}/positions/${sid}`)
        .then((data) => {
          if (!active) return;
          setPositions(data);
          setAnchorId((value) =>
            data.blocks.some((b: Row) => b.node_id === value)
              ? value
              : data.blocks.length === 1
                ? data.blocks[0].node_id
                : "",
          );
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [pid, sid]);
  useEffect(() => {
    setOutputChecked(false);
  }, [saved?.id, explanation, anchorId, sid]);
  useEffect(() => {
    setPlacementError("");
  }, [saved?.id, anchorId, sid]);
  useEffect(() => {
    alive.current = true;
    Promise.all([
      api("/paper-visuals/templates"),
      api(`/projects/${pid}/artifacts`),
      api(`/projects/${pid}/sections`),
    ])
      .then(([data, assets, chapters]) => {
        if (!alive.current) return;
        const visuals = assets.filter((a: Row) =>
          ["paper-visuals-v1", "paper-image-v1"].includes(
            read(a.metadata).generator,
          ),
        );
        setTemplates(data.templates);
        setLibrary(visuals);
        setSections(chapters.filter((s: Row) => s.state !== "archived"));
        let active = "flowchart";
        try {
          const stored = read(localStorage.getItem(draftKey), null);
          if (stored && stored.drafts && typeof stored.drafts === "object") {
            drafts.current = stored.drafts;
            if (supportedKind(stored.active)) active = stored.active;
          }
        } catch {}
        initialized.current = true;
        applyDraft(active, drafts.current[active], data.templates, visuals);
        try {
          const pending = read(localStorage.getItem(taskKey), null);
          if (pending?.id && supportedKind(pending.kind)) {
            if (drafts.current[pending.kind]?.candidateTask !== pending.id) {
              applyDraft(
                pending.kind,
                drafts.current[pending.kind],
                data.templates,
                visuals,
              );
              setTask({ id: pending.id, phase: "正在恢复图表候选" });
            }
          }
        } catch {}
      })
      .catch((e) => {
        if (alive.current) setError(e.message);
      });
    return () => {
      alive.current = false;
    };
  }, [pid, loadVersion]);

  const perform = async (fn: () => Promise<void>) => {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      mutation.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const rememberTask = (next: Row) => {
    try {
      localStorage.setItem(taskKey, json({ id: next.id, kind }));
    } catch {}
    setTask(next);
    setMissing([]);
    setRawResult("");
  };
  const forgetTask = () => {
    try {
      localStorage.removeItem(taskKey);
    } catch {}
  };
  const parseCode = () => {
    let value: Row;
    try {
      value = JSON.parse(code);
    } catch {
      throw new Error("JSON 代码格式不正确，请检查引号、逗号和括号。");
    }
    if (value?.kind !== kind)
      throw new Error("代码中的 kind 与当前类型不一致，请切换类型或修改代码。");
    return value;
  };
  const makePreview = async (value: Row, source: string) => {
    const result = await send(prefix + "/preview", { spec: value });
    if (alive.current) {
      setPreview(result);
      setPreviewCode(source);
    }
  };

  useEffect(() => {
    if (!task) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = await api(`/tasks/${task.id}`);
        if (cancelled || !alive.current) return;
        if (
          current.project_id !== pid ||
          !["paper_visual", "paper_image"].includes(current.kind)
        ) {
          setTask(null);
          forgetTask();
          setError("恢复的任务不属于当前项目的图表任务。");
          return;
        }
        if (current.status === "succeeded") {
          if (!sectionId && current.payload?.section_id)
            setSid(current.payload.section_id);
          setTask(null);
          setCandidateTask(current.id);
          const candidate = current.result,
            source = json(candidate.spec);
          if (current.kind === "paper_image") {
            const asset = candidate.asset,
              metadata = read(asset.metadata);
            setKind(metadata.spec.kind);
            setCode(source);
            setSaved(asset);
            setSavedCode(source);
            setContext(candidate.source_context ?? {});
            setMissing([]);
            setPreviewCode(source);
            setPreview({
              spec: metadata.spec,
              image: `/api/v1/images/${asset.sha256}`,
              width: metadata.width,
              height: metadata.height,
              render_hash: asset.sha256,
              renderer_version: metadata.renderer_version,
            });
            setImageTitle(metadata.spec.title);
            setExplanation(metadata.explanation ?? "");
            setFigurePurpose(metadata.figure_purpose ?? "design_schematic");
            setRelatedSections(metadata.related_section_ids ?? []);
            setRequiredLabels(
              (metadata.image_brief?.required_labels ?? []).join("\n"),
            );
            setForbiddenContent(metadata.image_brief?.forbidden_content ?? "");
            if (metadata.image_brief?.target)
              setAnchorId(metadata.image_brief.target.node_id);
            setBriefPreview(null);
            setReviewNote("");
            setLibrary((rows) => [
              asset,
              ...rows.filter((a) => a.id !== asset.id),
            ]);
            setMessage(
              "gpt-image-2 图片已生成，请对照目标段落、必需标签和图后说明核对后插入。",
            );
            onSaved();
            return;
          }
          setCode(source);
          setContext(candidate.source_context ?? {});
          setSaved(null);
          setSavedCode("");
          setMissing(candidate.missing_materials ?? []);
          setPreview(null);
          mutation.current = true;
          setBusy(true);
          try {
            await makePreview(candidate.spec, source);
            setMessage("AI 候选已生成，请核对图表与正文后保存或插入。");
          } catch (e) {
            setError((e as Error).message);
          } finally {
            mutation.current = false;
            if (alive.current) setBusy(false);
          }
          return;
        }
        if (!["queued", "running"].includes(current.status)) {
          setTask(null);
          forgetTask();
          setError(current.error || "任务已结束，请到任务中心查看。");
          if (current.result?.raw_text) {
            setRawResult(current.result.raw_text);
            setMessage("原始响应已保留，可展开查看并手动整理。");
          }
          return;
        }
        setTask(current);
        timer = setTimeout(poll, 1500);
      } catch (e) {
        if (cancelled || !alive.current) return;
        setError("任务状态读取失败：" + (e as Error).message);
        timer = setTimeout(poll, 5000);
      }
    };
    timer = setTimeout(poll, 700);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [task?.id]);

  const chooseKind = (next: string) => {
    if (next === kind) return;
    cacheDraft();
    applyDraft(next, drafts.current[next]);
  };
  const imageBody = (edit = false): Row => {
    const body: Row = {
      kind,
      title:
        imageTitle.trim() ||
        `${sections.find((s) => s.id === sid)?.title ?? "论文"} ${labels[kind]}`,
      instruction,
      figure_purpose: figurePurpose,
      explanation,
      related_section_ids: relatedSections,
      section_id: sid || undefined,
      anchor_node_id: sid ? anchorId || undefined : undefined,
      required_labels: requiredLabels
        .split(/\n/)
        .map((x) => x.trim())
        .filter(Boolean),
      forbidden_content: forbiddenContent,
      layout,
      ...(!edit && useBlueprint && !nativeImage
        ? { current_spec: parseCode() }
        : {}),
      ...(edit
        ? {
            source_context: context,
            ...(nativeImage
              ? { source_artifact_id: saved!.id }
              : {
                  current_spec: parseCode(),
                  source_artifact_id: saved?.id ?? context.source_artifact_id,
                }),
          }
        : {}),
    };
    return body;
  };
  const previewImageBrief = async (edit = false) => {
    const body = imageBody(edit);
    const result = await send(prefix + "/image-brief", body);
    setBriefPreview({
      ...result,
      edit,
      input: json({ ...body, anchor_node_id: result.brief.target?.node_id }),
    });
    if (result.brief.target) {
      setAnchorId(result.brief.target.node_id);
      setPositions((previous) => ({
        ...previous,
        revision_id: result.brief.target.revision_id,
      }));
    }
    setMessage(
      "绘图要求已整理，未调用模型或产生生图费用。请核对目标范围与提示词后生成。",
    );
  };
  const briefIsCurrent = (edit = false) => {
    try {
      return (
        briefPreview?.edit === edit &&
        briefPreview.input === json(imageBody(edit))
      );
    } catch {
      return false;
    }
  };
  const launchImage = async (edit = false) => {
    if (!briefIsCurrent(edit))
      throw new Error(
        "请先预览当前绘图要求；修改正文、范围或约束后需重新预览。",
      );
    const body = { ...imageBody(edit), brief_hash: briefPreview!.brief.hash };
    const fingerprint = json(body);
    if (imageRequest.current.fingerprint !== fingerprint)
      imageRequest.current = { fingerprint, id: crypto.randomUUID() };
    const next = await send(prefix + "/image-tasks", {
      ...body,
      request_id: imageRequest.current.id,
    });
    imageRequest.current = { fingerprint: "", id: "" };
    rememberTask(next);
  };
  const updateTable = (patch: Row) => setCode(json({ ...spec, ...patch }));
  const changeStructure = (patch: Row) => {
    setUndoCode(code);
    updateTable(patch);
  };
  const save = async (insert = false) => {
    if (!ready) throw new Error("内容已变化，请先重新生成预览。");
    if (insert && kind !== "three_line_table" && !explanation.trim())
      throw new Error("请先填写图后说明，解释图中各元素的含义与相互关系。");
    if (kind !== "three_line_table" && protectedPurpose)
      throw new Error(
        "真实结果与截图请到研究与成果上传实际文件，不能由设计示意图代替。",
      );
    const value = preview!.spec;
    const annotations = {
      explanation,
      figure_purpose: figurePurpose,
      related_section_ids: relatedSections,
    };
    const fingerprint = json({
      spec: value,
      context,
      parent: saved?.id,
      annotations,
    });
    if (saveRequest.current.fingerprint !== fingerprint)
      saveRequest.current = { fingerprint, id: crypto.randomUUID() };
    let asset = savedCurrent
      ? saved!
      : nativeImage
        ? await send(`${prefix}/${saved!.id}/annotate`, {
            ...annotations,
            request_id: saveRequest.current.id,
          })
        : await send(prefix, {
            spec: value,
            source_context: context,
            parent_artifact_id: saved?.id,
            request_id: saveRequest.current.id,
            ...annotations,
          });
    if (!alive.current) return;
    if (!asset.node) asset = await api(`${prefix}/${asset.id}`);
    if (!asset.node)
      asset.node =
        kind === "three_line_table"
          ? {
              ...preview!.node,
              attrs: { ...preview!.node.attrs, artifactId: asset.id },
            }
          : {
              type: "figure",
              attrs: {
                artifactId: asset.id,
                caption: value.title,
                nodeId: crypto.randomUUID(),
              },
            };
    setSaved(asset);
    setSavedCode(code);
    if (candidateTask) {
      forgetTask();
      setCandidateTask("");
    }
    setLibrary((rows) => [asset, ...rows.filter((a) => a.id !== asset.id)]);
    if (insert && onInsert) {
      if (nativeImage) {
        if (!outputChecked || reviewNote.trim().length < 6)
          throw new Error(
            "请逐项核对图片与目标段落，勾选确认并填写具体核对依据。",
          );
        if (!sid || !anchorId) throw new Error("请选择图片要解释的具体段落。");
        const current = await api(`${prefix}/positions/${sid}`);
        try {
          const reviewed = await send(
            `${prefix}/${asset.id}/alignment-review`,
            {
              section_id: sid,
              anchor_node_id: anchorId,
              revision_id: current.revision_id,
              confirmed: true,
              note: reviewNote,
            },
          );
          asset.alignment_review_id = reviewed.review_id;
        } catch (error) {
          if (error instanceof ApiError && error.status === 409)
            setPlacementError(error.message);
          throw error;
        }
      }
      try {
        if (!(await onInsert(asset.node, asset)))
          throw new Error(
            "图表已保存，正文插入未完成。请检查锁定状态或保存提示后重试。",
          );
      } catch (error) {
        if (nativeImage && error instanceof ApiError && error.status === 409)
          setPlacementError(error.message);
        throw error;
      }
      onSaved();
      onClose();
    } else {
      onSaved();
      setMessage(
        "已保存到本项目的研究产物。可下载文件，也可在写作工作台插入。",
      );
    }
  };
  const chooseAsset = async (asset: Row) => {
    cacheDraft();
    const record = await api(`${prefix}/${asset.id}`);
    if (!alive.current) return;
    const metadata = read(record.metadata),
      source = json(metadata.spec);
    setKind(metadata.spec.kind);
    setCode(source);
    setSaved(record);
    setSavedCode(source);
    setContext(
      record.source_context ?? {
        revision_ids: metadata.source_revision_ids ?? [],
        source_artifact_id: record.id,
      },
    );
    setExplanation(metadata.explanation ?? "");
    setFigurePurpose(metadata.figure_purpose ?? "design_schematic");
    setRelatedSections(metadata.related_section_ids ?? []);
    setRequiredLabels((metadata.image_brief?.required_labels ?? []).join("\n"));
    setForbiddenContent(metadata.image_brief?.forbidden_content ?? "");
    if (metadata.image_brief?.target) {
      if (!sectionId) setSid(metadata.image_brief.target.section_id);
      setAnchorId(metadata.image_brief.target.node_id);
    }
    setBriefPreview(null);
    setReviewNote("");
    setMissing([]);
    setPreview(null);
    setError("");
    setMessage("");
    setCandidateTask("");
    forgetTask();
    setUndoCode("");
    setZoom(1);
    const result =
      metadata.spec.kind === "three_line_table"
        ? await send(prefix + "/preview", { spec: metadata.spec })
        : {
            spec: metadata.spec,
            image: `/api/v1/images/${record.sha256}`,
            width: metadata.width,
            height: metadata.height,
            render_hash: record.sha256,
            renderer_version: metadata.renderer_version,
          };
    if (!alive.current) return;
    setPreview(result);
    setPreviewCode(source);
  };

  if (!Object.keys(templates).length)
    return (
      <WorkspaceDialog title="论文图表" wide onClose={onClose}>
        {error ? (
          <div className="pv-error" role="alert">
            {error}
            <button
              type="button"
              className="btn"
              onClick={() => setLoadVersion((v) => v + 1)}
            >
              重新加载
            </button>
          </div>
        ) : (
          <p role="status">正在读取图表模板和本机草稿…</p>
        )}
      </WorkspaceDialog>
    );

  return (
    <WorkspaceDialog
      title="论文图表"
      wide
      busy={busy}
      onClose={() => {
        cacheDraft();
        onClose();
      }}
    >
      <div className="pv-studio">
        <p className="pv-intro">
          代码预览在本机生成；AI 生图和修改使用 gpt-image-2 与独立
          key。三线表导出为可编辑的 Word 表格。
        </p>
        <div className="pv-types" role="tablist" aria-label="图表类型">
          {Object.entries(labels).map(([key, label]) => (
            <button
              type="button"
              role="tab"
              aria-selected={kind === key}
              className={kind === key ? "active" : ""}
              disabled={disabled || !templates[key]}
              key={key}
              onClick={() => chooseKind(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="pv-source-tools">
          <button
            type="button"
            className="btn"
            disabled={disabled}
            onClick={() => sourceFile.current?.click()}
          >
            <FileUp size={15} />
            导入 JSON 源代码
          </button>
          <span>各类型的未保存内容会在本机保留。</span>
          <input
            ref={sourceFile}
            type="file"
            accept=".json,application/json"
            hidden
            aria-label="导入图表源代码文件"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              perform(async () => {
                if (file.size > 600000)
                  throw new Error("源代码文件过大，请拆分图表。");
                let imported: Row;
                try {
                  imported = JSON.parse(
                    (await file.text()).replace(/^\uFEFF/, ""),
                  );
                } catch {
                  throw new Error("文件不是有效的 JSON 源代码。");
                }
                if (
                  !imported ||
                  typeof imported !== "object" ||
                  !supportedKind(imported.kind)
                )
                  throw new Error("文件中没有支持的图表类型 kind。");
                cacheDraft();
                applyDraft(imported.kind, { code: json(imported) });
              });
            }}
          />
        </div>
        {error && (
          <div className="pv-error" role="alert">
            {error}
            {!Object.keys(templates).length && (
              <button
                type="button"
                className="btn"
                onClick={() => setLoadVersion((v) => v + 1)}
              >
                重新加载
              </button>
            )}
          </div>
        )}
        {message && (
          <p className="pv-message" role="status">
            {message}
          </p>
        )}
        <div className="pv-layout">
          <section className="pv-source">
            <div className="pv-heading">
              <h3>{kind === "three_line_table" ? "编辑表格" : "结构代码"}</h3>
              <span>{nativeImage ? "gpt-image-2 图片" : "本机生成预览"}</span>
            </div>
            {kind !== "three_line_table" && (
              <div className="pv-figure-metadata">
                <label>
                  图片用途
                  <select
                    aria-label="图片用途"
                    value={figurePurpose}
                    disabled={disabled}
                    onChange={(e) => setFigurePurpose(e.target.value)}
                  >
                    <option value="design_schematic">设计示意图</option>
                    <option value="original_result">
                      原创结果图（上传真实产物）
                    </option>
                    <option value="experimental_result">
                      实验结果图（上传真实产物）
                    </option>
                    <option value="interface_screenshot">
                      真实界面截图（上传原件）
                    </option>
                    <option value="test_capture">
                      真实测试日志（上传原件）
                    </option>
                    <option value="ai_method_output">
                      AI 研究方法产物（须登记方法）
                    </option>
                  </select>
                </label>
                {protectedPurpose && (
                  <p className="pv-pending">
                    此类图片需来自实际系统、实验或原始记录，不能用 AI
                    生图或结构示意替代。
                  </p>
                )}
                <label>
                  图后说明
                  <textarea
                    aria-label="图后说明"
                    rows={3}
                    maxLength={6000}
                    value={explanation}
                    disabled={disabled}
                    placeholder="说明各元素的含义、相互关系以及图与当前章节的联系；插入正文时会一并插入此段。"
                    onChange={(e) => setExplanation(e.target.value)}
                  />
                </label>
                {!nativeImage && (
                  <button
                    type="button"
                    className="btn"
                    disabled={disabled || !code}
                    onClick={() =>
                      perform(async () => {
                        const result = await send(
                          prefix + "/explanation-draft",
                          { spec: parseCode() },
                        );
                        setExplanation(result.explanation);
                        setMessage(result.statement);
                      })
                    }
                  >
                    根据结构整理说明
                  </button>
                )}
                <label>
                  关联需求与实现章节
                  <select
                    multiple
                    aria-label="关联需求与实现章节"
                    value={relatedSections}
                    disabled={disabled}
                    onChange={(e) =>
                      setRelatedSections(
                        Array.from(e.target.selectedOptions, (o) => o.value),
                      )
                    }
                  >
                    {sections.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.title}
                      </option>
                    ))}
                  </select>
                </label>
                {kind === "data_flow" && (
                  <button
                    type="button"
                    className="btn"
                    disabled={disabled}
                    onClick={() =>
                      perform(async () => {
                        const result = await send(prefix + "/data-flow-table", {
                          spec: parseCode(),
                        });
                        cacheDraft();
                        applyDraft("three_line_table", {
                          code: json(result.spec),
                          context,
                        });
                      })
                    }
                  >
                    整理数据流说明表
                  </button>
                )}
              </div>
            )}
            {kind !== "three_line_table" && typeof spec?.title === "string" && (
              <label>
                图表标题
                <input
                  aria-label="图表标题"
                  disabled={disabled || nativeImage}
                  value={spec.title}
                  onChange={(e) =>
                    setCode(json({ ...spec, title: e.target.value }))
                  }
                />
              </label>
            )}
            {kind === "three_line_table" && tableEditable && (
              <>
                <label>
                  表格标题
                  <input
                    aria-label="表格标题"
                    disabled={disabled}
                    value={spec.title}
                    onChange={(e) => updateTable({ title: e.target.value })}
                  />
                </label>
                <div className="pv-grid-editor">
                  <table>
                    <thead>
                      <tr>
                        {spec.headers.map((header: string, i: number) => (
                          <th key={i}>
                            <textarea
                              rows={1}
                              aria-label={`第${i + 1}列表头`}
                              disabled={disabled}
                              value={header}
                              onChange={(e) =>
                                updateTable({
                                  headers: spec.headers.map(
                                    (v: string, j: number) =>
                                      i === j ? e.target.value : v,
                                  ),
                                })
                              }
                            />
                            <button
                              type="button"
                              className="pv-remove"
                              aria-label={`删除第${i + 1}列`}
                              title={`删除第${i + 1}列`}
                              disabled={disabled || spec.headers.length <= 1}
                              onClick={() =>
                                changeStructure({
                                  headers: spec.headers.filter(
                                    (_: string, j: number) => j !== i,
                                  ),
                                  rows: spec.rows.map((r: any[]) =>
                                    r.filter((_, j) => j !== i),
                                  ),
                                })
                              }
                            >
                              <X size={13} />
                            </button>
                          </th>
                        ))}
                        <th aria-label="行操作" />
                      </tr>
                    </thead>
                    <tbody>
                      {spec.rows.map((row: any[], i: number) => (
                        <tr key={i}>
                          {row.map((value: any, j: number) => (
                            <td key={j}>
                              <textarea
                                rows={1}
                                aria-label={`第${i + 1}行第${j + 1}列`}
                                disabled={disabled}
                                value={value ?? ""}
                                onChange={(e) =>
                                  updateTable({
                                    rows: spec.rows.map(
                                      (r: any[], index: number) =>
                                        index === i
                                          ? r.map((v, column) =>
                                              column === j ? e.target.value : v,
                                            )
                                          : r,
                                    ),
                                  })
                                }
                              />
                            </td>
                          ))}
                          <td>
                            <button
                              type="button"
                              className="pv-remove"
                              aria-label={`删除第${i + 1}行`}
                              disabled={disabled || spec.rows.length <= 1}
                              onClick={() =>
                                changeStructure({
                                  rows: spec.rows.filter(
                                    (_: any, index: number) => index !== i,
                                  ),
                                })
                              }
                            >
                              <X size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="pv-actions pv-table-actions">
                  <button
                    type="button"
                    className="btn"
                    disabled={disabled || spec.rows.length >= 100}
                    onClick={() =>
                      changeStructure({
                        rows: [...spec.rows, spec.headers.map(() => "")],
                      })
                    }
                  >
                    <Plus size={14} />
                    添加一行
                  </button>
                  <button
                    type="button"
                    className="btn"
                    disabled={disabled || spec.headers.length >= 10}
                    onClick={() =>
                      changeStructure({
                        headers: [
                          ...spec.headers,
                          `新列${spec.headers.length + 1}`,
                        ],
                        rows: spec.rows.map((r: any[]) => [...r, ""]),
                      })
                    }
                  >
                    <Plus size={14} />
                    添加一列
                  </button>
                  {undoCode && (
                    <button
                      type="button"
                      className="btn"
                      disabled={disabled}
                      onClick={() => {
                        const previous = undoCode;
                        setUndoCode(code);
                        setCode(previous);
                      }}
                    >
                      <Undo2 size={14} />
                      撤销结构调整
                    </button>
                  )}
                </div>
                <label>
                  表注
                  <textarea
                    rows={2}
                    disabled={disabled}
                    value={spec.note ?? ""}
                    onChange={(e) => updateTable({ note: e.target.value })}
                  />
                </label>
                <button
                  type="button"
                  className="pv-text-button"
                  onClick={() => setShowSource((v) => !v)}
                >
                  {showSource ? "收起" : "编辑"} JSON 源代码与列结构
                </button>
              </>
            )}
            {(kind !== "three_line_table" || showSource || !tableEditable) && (
              <label className="pv-code-label">
                JSON 源代码
                <textarea
                  aria-label={nativeImage ? "GPT 生图记录" : "图表 JSON 源代码"}
                  className="pv-code"
                  spellCheck={false}
                  rows={17}
                  value={code}
                  disabled={disabled}
                  readOnly={nativeImage}
                  onChange={(e) => setCode(e.target.value)}
                />
              </label>
            )}
            {nativeImage ? (
              <p className="pv-help">
                此图由 gpt-image-2
                生成。下面可使用独立生图接口继续修改，生图记录保留用于追溯。
              </p>
            ) : (
              <details className="pv-help">
                <summary>代码填写说明</summary>
                <p>
                  修改 title 和 label 可更换标题与节点文字；每个 id
                  必须唯一，edges 的 source、target 对应节点 id。
                </p>
                <p>
                  流程图形状：process、decision、start、end。用例关系：association
                  为关联，include 指向被包含用例，extend 指向基础用例。ER 图的
                  fields 填写字段，PK / FK 表示主键与外键。
                </p>
                <p>
                  三线表 headers 为表头，rows
                  为逐行数据；可以直接在上方单元格编辑，也可以通过 JSON
                  增减列。模板仅用于示范结构，请换成实际设计。
                </p>
              </details>
            )}
            <button
              type="button"
              className="btn primary"
              disabled={disabled || !code || nativeImage}
              onClick={() => perform(() => makePreview(parseCode(), code))}
            >
              <RefreshCw size={15} />
              生成预览
            </button>
          </section>
          <section className="pv-preview-section">
            <div className="pv-heading">
              <h3>论文预览</h3>
              {preview?.width && (
                <span>
                  {preview.width} × {preview.height} px · PNG
                </span>
              )}
            </div>
            {preview && !ready && (
              <p className="pv-pending">内容已修改，请重新生成预览。</p>
            )}
            {ready && saved && code === savedCode && !savedCurrent && (
              <p className="pv-pending">
                预览已重新生成，请保存后下载更新后的文件。
              </p>
            )}
            {preview?.image && (
              <div className="pv-zoom">
                <button
                  type="button"
                  aria-label="缩小预览"
                  disabled={zoom <= 0.5}
                  onClick={() => setZoom((v) => Math.max(0.5, v - 0.25))}
                >
                  <Minus size={14} />
                </button>
                <button type="button" onClick={() => setZoom(1)}>
                  适应宽度
                </button>
                <button
                  type="button"
                  aria-label="放大预览"
                  disabled={zoom >= 3}
                  onClick={() => setZoom((v) => Math.min(3, v + 0.25))}
                >
                  <Plus size={14} />
                </button>
                <span>{Math.round(zoom * 100)}%</span>
              </div>
            )}
            <div className={`pv-preview ${!ready ? "pv-outdated" : ""}`}>
              {preview?.image ? (
                <img
                  src={preview.image}
                  alt={preview.spec.title}
                  style={{ width: `${zoom * 100}%`, maxWidth: "none" }}
                />
              ) : preview?.spec?.kind === "three_line_table" ? (
                <div className="pv-table-preview">
                  <p>{preview.spec.title}</p>
                  <div className="pv-table-scroll">
                    <table>
                      <thead>
                        <tr>
                          {preview.spec.headers.map((h: string, i: number) => (
                            <th key={i}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.spec.rows.map((r: string[], i: number) => (
                          <tr key={i}>
                            {r.map((v, j) => (
                              <td key={j}>{v}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {preview.spec.note && <small>注：{preview.spec.note}</small>}
                </div>
              ) : (
                <div className="pv-empty">
                  <ImagePlus size={36} />
                  <strong>查看生成效果</strong>
                  <p>编辑左侧内容后点击“生成预览”。</p>
                </div>
              )}
            </div>
            {nativeImage && (
              <div className="pv-output-review">
                <h4>图意与插入位置核对</h4>
                {placementError && (
                  <p className="pv-error">
                    {placementError}。请先处理目标位置或针对当前正文重新生成。
                  </p>
                )}
                {read(saved?.metadata).image_brief?.goal && (
                  <p>本图生成要求：{read(saved?.metadata).image_brief.goal}</p>
                )}
                {read(saved?.metadata).alignment_status ===
                  "source_changed" && (
                  <p className="pv-error">
                    生成期间正文发生变化：
                    {read(saved?.metadata).alignment_message}
                    。本候选已保留，请针对当前段落重新生成。
                  </p>
                )}
                {read(saved?.metadata).image_brief?.target ? (
                  <>
                    <p>
                      生成依据：
                      {read(saved?.metadata).image_brief.target.section_title}
                    </p>
                    <blockquote>
                      {read(saved?.metadata).image_brief.target.text}
                    </blockquote>
                  </>
                ) : (
                  <p>此候选未绑定生成时的段落，请选择实际插入位置并核对。</p>
                )}
                <p>
                  逐项比较图片中的文字、元素和关系方向，确认图后说明解释的内容与目标段落一致。
                </p>
                {read(saved?.metadata).image_brief?.forbidden_content && (
                  <p>
                    本图禁止内容：
                    {read(saved?.metadata).image_brief.forbidden_content}
                  </p>
                )}
                {read(saved?.metadata).revised_prompt && (
                  <details className="pv-brief">
                    <summary>查看服务返回的改写提示词</summary>
                    <p>请对照原绘图要求核对改写是否改变了范围、标签或关系。</p>
                    <textarea
                      aria-label="服务改写的绘图提示词"
                      readOnly
                      rows={6}
                      value={read(saved?.metadata).revised_prompt}
                    />
                  </details>
                )}
                {read(saved?.metadata).image_brief?.required_labels?.length >
                  0 && (
                  <p>
                    必需文字：
                    {read(saved?.metadata).image_brief.required_labels.join(
                      "、",
                    )}
                  </p>
                )}
                <label className="pv-check">
                  <input
                    type="checkbox"
                    aria-label="已核对图片与目标段落的图意一致"
                    checked={outputChecked}
                    disabled={disabled}
                    onChange={(e) => setOutputChecked(e.target.checked)}
                  />
                  已逐项核对图片、图后说明与目标段落
                </label>
                <label>
                  具体核对依据
                  <textarea
                    aria-label="生图产物核对依据"
                    value={reviewNote}
                    disabled={disabled}
                    maxLength={2000}
                    rows={2}
                    onChange={(e) => setReviewNote(e.target.value)}
                    placeholder="说明核对了哪些标签、关系、分支以及它们与本段论述的对应。"
                  />
                </label>
              </div>
            )}
            <div className="pv-actions">
              <button
                type="button"
                className="btn"
                disabled={disabled || !ready}
                onClick={() => perform(() => save())}
              >
                <Save size={15} />
                保存图表
              </button>
              {onInsert && (
                <button
                  type="button"
                  className="btn primary"
                  disabled={
                    disabled ||
                    !ready ||
                    (nativeImage &&
                      (read(saved?.metadata).alignment_status ===
                        "source_changed" ||
                        Boolean(placementError) ||
                        !outputChecked ||
                        reviewNote.trim().length < 6 ||
                        !anchorId)) ||
                    (kind !== "three_line_table" &&
                      (!explanation.trim() || protectedPurpose))
                  }
                  onClick={() => perform(() => save(true))}
                >
                  保存并插入正文
                </button>
              )}
              {savedCurrent && ready && (
                <>
                  <a
                    className="btn"
                    href={`/api/v1${prefix}/${saved!.id}/download?format=${kind === "three_line_table" ? "docx" : "png"}`}
                    download
                  >
                    <Download size={15} />
                    下载{kind === "three_line_table" ? " Word" : " PNG"}
                  </a>
                  <a
                    className="btn"
                    href={`/api/v1${prefix}/${saved!.id}/download?format=source`}
                    download
                  >
                    {nativeImage ? "下载生图记录" : "下载源代码"}
                  </a>
                </>
              )}
            </div>
          </section>
        </div>
        <section className="pv-ai">
          <div className="pv-heading">
            <h3>
              <Sparkles size={17} />
              {kind === "three_line_table"
                ? "AI 辅助表格结构"
                : "GPT 图片生成与修改"}
            </h3>
            <span>候选需核对后插入</span>
          </div>
          {kind !== "three_line_table" && (
            <ImageGenerationSettings
              embedded
              onChange={(data) => setImageReady(Boolean(data.ready))}
            />
          )}
          <div className="pv-ai-fields">
            <label>
              依据章节
              <select
                aria-label="配图依据章节"
                value={sid}
                disabled={disabled || Boolean(sectionId)}
                onChange={(e) => setSid(e.target.value)}
              >
                <option value="">根据下方要求</option>
                {sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              配图或修改要求
              <textarea
                aria-label="AI 配图要求"
                rows={2}
                placeholder="例如：根据本节描述绘制管理员用例图，只保留已实现功能。"
                value={instruction}
                maxLength={4000}
                disabled={disabled}
                onChange={(e) => setInstruction(e.target.value)}
              />
            </label>
          </div>
          {kind !== "three_line_table" && (
            <>
              {sid && (
                <label>
                  图片紧随此段落插入
                  <select
                    aria-label="生图目标段落"
                    value={anchorId}
                    disabled={disabled}
                    onChange={(e) => setAnchorId(e.target.value)}
                  >
                    <option value="">选择本图解释的具体段落</option>
                    {positions.blocks.map((block: Row) => (
                      <option key={block.node_id} value={block.node_id}>
                        {block.text.slice(0, 100)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <div className="pv-brief-fields">
                <label>
                  必须出现的文字（每行一项）
                  <textarea
                    aria-label="生图必需文字"
                    rows={3}
                    value={requiredLabels}
                    maxLength={2400}
                    disabled={disabled}
                    onChange={(e) => setRequiredLabels(e.target.value)}
                    placeholder="填写实际模块名、参与者或关键步骤，避免名称漂移。"
                  />
                </label>
                <label>
                  禁止加入的内容
                  <textarea
                    aria-label="生图禁止内容"
                    rows={2}
                    value={forbiddenContent}
                    maxLength={1200}
                    disabled={disabled}
                    onChange={(e) => setForbiddenContent(e.target.value)}
                    placeholder="例如：不加入未实现的支付、缓存或链上模块。"
                  />
                </label>
                <label>
                  绘图方向
                  <select
                    aria-label="生图方向"
                    value={layout}
                    disabled={disabled}
                    onChange={(e) => setLayout(e.target.value)}
                  >
                    <option value="auto">按结构自动安排</option>
                    <option value="top_down">由上至下</option>
                    <option value="left_right">由左至右</option>
                  </select>
                </label>
                {!nativeImage && (
                  <label className="pv-check">
                    <input
                      type="checkbox"
                      aria-label="生成时采用当前结构蓝图"
                      checked={useBlueprint}
                      disabled={disabled}
                      onChange={(e) => setUseBlueprint(e.target.checked)}
                    />
                    生成时采用当前结构蓝图（请先把示例改为实际设计）
                  </label>
                )}
              </div>
              {projectFigurePolicy?.ai_image_policy === "forbidden" && (
                <p className="pv-pending">
                  本项目已禁止 AI
                  图片生成与修改；可继续使用本机代码出图，或上传真实产物。
                </p>
              )}
              {projectFigurePolicy?.ai_image_policy === "unconfirmed" && (
                <p className="pv-source-note">
                  AI
                  图片仅作为待核对草图；正式使用前需确认本项目规范并核对图文。
                </p>
              )}
              <label>
                GPT 图片标题
                <input
                  aria-label="GPT 图片标题"
                  value={imageTitle}
                  maxLength={100}
                  disabled={disabled}
                  onChange={(e) => setImageTitle(e.target.value)}
                  placeholder="留空按章节与图类型命名"
                />
              </label>
              {!imageReady && (
                <p className="pv-pending">
                  请先展开专用设置，填写独立的生图 API key 并保存。
                </p>
              )}
              <div className="pv-actions">
                <button
                  type="button"
                  className="btn"
                  disabled={
                    disabled || protectedPurpose || (Boolean(sid) && !anchorId)
                  }
                  onClick={() => perform(() => previewImageBrief())}
                >
                  预览生成提示词（免费）
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={
                    disabled ||
                    protectedPurpose ||
                    !instruction.trim() ||
                    (Boolean(sid) && !anchorId)
                  }
                  onClick={() => perform(() => previewImageBrief(true))}
                >
                  预览修改提示词（免费）
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={
                    disabled ||
                    projectFigurePolicy?.ai_image_policy === "forbidden" ||
                    protectedPurpose ||
                    !imageReady ||
                    !briefIsCurrent(false) ||
                    (!sid && !instruction.trim())
                  }
                  onClick={() => perform(() => launchImage())}
                >
                  <Sparkles size={15} />
                  使用 gpt-image-2 生成（计费）
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={
                    disabled ||
                    projectFigurePolicy?.ai_image_policy === "forbidden" ||
                    protectedPurpose ||
                    !imageReady ||
                    !briefIsCurrent(true) ||
                    !instruction.trim()
                  }
                  onClick={() => perform(() => launchImage(true))}
                >
                  使用 gpt-image-2 修改（计费）
                </button>
                {task && (
                  <>
                    <span role="status">
                      {task.phase || "图片任务已排队"} · {task.progress ?? 0}%
                    </span>
                    <button
                      type="button"
                      className="btn"
                      onClick={() =>
                        perform(async () => {
                          await send(`/tasks/${task.id}/cancel`);
                          setTask(null);
                          forgetTask();
                          setMessage(
                            "已请求取消；已发送的生图请求请在任务中心核对结果。",
                          );
                        })
                      }
                    >
                      取消任务
                    </button>
                  </>
                )}
              </div>
              {briefPreview && (
                <details className="pv-brief" open>
                  <summary>本次绘图要求与完整提示词</summary>
                  {briefPreview.brief.warnings?.map(
                    (warning: string, index: number) => (
                      <p className="pv-pending" key={index}>
                        {warning}
                      </p>
                    ),
                  )}
                  {!briefIsCurrent(briefPreview.edit) && (
                    <p className="pv-pending">要求已修改，请重新预览后生成。</p>
                  )}
                  <p>
                    {briefPreview.brief.target
                      ? `位置：${briefPreview.brief.target.section_title} · 紧随所选段落`
                      : "此候选暂无论文插入位置，插入前需选择具体段落核对。"}
                  </p>
                  {briefPreview.brief.target && (
                    <blockquote>{briefPreview.brief.target.text}</blockquote>
                  )}
                  <p>
                    图后说明：
                    {briefPreview.brief.explanation ||
                      "尚未填写，插入正文前需补齐"}
                  </p>
                  <label>
                    实际发送的绘图提示词
                    <textarea
                      aria-label="绘图提示词预览"
                      readOnly
                      rows={12}
                      value={briefPreview.prompt}
                    />
                  </label>
                </details>
              )}
            </>
          )}
          {kind === "three_line_table" && (
            <div className="pv-actions">
              <button
                type="button"
                className="btn"
                disabled={disabled || !code || (!sid && !instruction.trim())}
                onClick={() =>
                  perform(async () => {
                    const next = await send(prefix + "/ai-tasks", {
                      kind,
                      instruction,
                      section_id: sid || undefined,
                    });
                    rememberTask(next);
                  })
                }
              >
                <Sparkles size={15} />
                根据正文生成（计费）
              </button>
              <button
                type="button"
                className="btn"
                disabled={disabled || !code || !instruction.trim()}
                onClick={() =>
                  perform(async () => {
                    const next = await send(prefix + "/ai-tasks", {
                      kind,
                      instruction,
                      section_id: sid || undefined,
                      current_spec: parseCode(),
                      source_context: context,
                      source_artifact_id:
                        saved?.id ?? context.source_artifact_id,
                    });
                    rememberTask(next);
                  })
                }
              >
                修改当前图表（计费）
              </button>
              {task && (
                <>
                  <span role="status">
                    {task.phase || "图表任务已排队"} · {task.progress ?? 0}%
                  </span>
                  <button
                    type="button"
                    className="btn"
                    onClick={() =>
                      perform(async () => {
                        await send(`/tasks/${task.id}/cancel`);
                        setTask(null);
                        forgetTask();
                        setMessage("已请求取消；任务详情保留在任务中心。");
                      })
                    }
                  >
                    取消任务
                  </button>
                </>
              )}
            </div>
          )}
          {!!context.sources?.length && (
            <p className="pv-source-note">
              当前图表依据：
              {context.sources
                .map(
                  (s: Row) =>
                    `${s.title}${s.current ? "" : "（正文已有更新，请重新核对）"}`,
                )
                .join("、")}
            </p>
          )}
          {rawResult && (
            <details className="pv-raw">
              <summary>查看保留的模型原始响应</summary>
              <pre>{rawResult}</pre>
            </details>
          )}
          {!!missing.length && (
            <div className="pv-pending">
              <strong>需要补充的材料</strong>
              <ul>
                {missing.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
        <section className="pv-library">
          <h3>
            本项目已保存的图表 <span>{library.length}</span>
          </h3>
          {library.length ? (
            <div className="pv-library-items">
              {library.map((asset) => (
                <button
                  type="button"
                  disabled={disabled}
                  key={asset.id}
                  onClick={() => perform(() => chooseAsset(asset))}
                >
                  <strong>{asset.name}</strong>
                  <small>
                    {labels[read(asset.metadata).spec?.kind] ?? asset.kind}
                    {read(asset.metadata).render_engine === "gpt-image-2"
                      ? " · gpt-image-2 图片"
                      : ""}{" "}
                    · 打开编辑与导出
                  </small>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">
              保存的图表会出现在这里，也会进入“研究与成果”。
            </p>
          )}
        </section>
        <FigureStandardsPanel
          pid={pid}
          sectionId={sid || undefined}
          onChange={onSaved}
          disabled={disabled}
          onPolicyChange={setProjectFigurePolicy}
        />
      </div>
    </WorkspaceDialog>
  );
}

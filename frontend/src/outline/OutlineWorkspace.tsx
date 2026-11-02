import { useRef, useState, type FormEvent } from "react";
import {
  ArrowUpRight,
  BookOpen,
  ListTree,
  LoaderCircle,
  Plus,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { OutlineManager } from "../Completion";
import { send } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import { useWorkspaceResource } from "../workspace/useWorkspaceResource";
import "./outlineWorkspace.css";
import { PaperWritingButton } from "../paper/PaperWriting";

type Section = {
  id: string;
  title: string;
  kind: string;
  goal: string;
  target_words: number;
  position: number;
  dependencies: string | string[];
  current_revision_id: string | null;
  current_text?: string | null;
  stale: number;
  state: string;
};

type Props = {
  pid: string;
  tick: number;
  act: (fn: () => Promise<any>, success?: string) => Promise<any>;
  onWrite: (sectionId: string) => void;
};

const sectionKinds = {
  body: "正文",
  abstract: "中文摘要",
  abstract_en: "英文摘要",
  introduction: "绪论",
  literature: "文献综述",
  requirements: "需求分析",
  system_design: "系统设计",
  implementation: "系统实现",
  methods: "研究方法",
  results: "结果/测试",
  discussion: "讨论",
  conclusion: "结论",
};

function sectionGoal(section: Partial<Section>) {
  if (section.kind === "abstract_en") {
    return "直接翻译已保存的中文摘要，保留原意、数据、结论与关键词；中文摘要修改后重新翻译。";
  }
  const goal = section.goal?.trim();
  if (goal && goal !== "按已确认规则组织本节内容，材料不足时列出缺口。") {
    return goal;
  }
  if (section.kind === "abstract") {
    return "用中文概述研究目的、方法、主要结果与结论，作为英文摘要的翻译原文。";
  }
  return goal || "还没有填写本节写作目标";
}

function dependenciesOf(section: Partial<Section>) {
  try {
    const value =
      typeof section.dependencies === "string"
        ? JSON.parse(section.dependencies)
        : section.dependencies;
    return Array.isArray(value) ? (value as string[]) : [];
  } catch {
    return [];
  }
}

function chineseAbstractFor(section: Section, sections: Section[]) {
  const abstracts = sections.filter((item) => item.kind === "abstract");
  const dependencies = dependenciesOf(section);
  const linked = abstracts.filter((item) => dependencies.includes(item.id));
  return linked.length === 1
    ? linked[0]
    : abstracts.length === 1
      ? abstracts[0]
      : null;
}

function SectionPlanDialog({
  section,
  sections,
  busy,
  onClose,
  onSave,
}: {
  section: Partial<Section>;
  sections: Section[];
  busy: boolean;
  onClose: () => void;
  onSave: (values: Record<string, unknown>) => Promise<void>;
}) {
  const selectedDependencies = dependenciesOf(section);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await onSave({
      title: String(form.get("title")).trim(),
      kind: form.get("kind"),
      target_words: Number(form.get("target_words")),
      position: Number(form.get("position")),
      goal: String(form.get("goal")).trim(),
      dependencies: form.getAll("dependencies"),
    });
  };
  return (
    <WorkspaceDialog
      title={section.id ? "编辑章节计划" : "新增章节"}
      closeLabel="关闭章节计划"
      busy={busy}
      onClose={onClose}
    >
      <form className="outline-plan-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          <label className="field">
            <span>章节标题</span>
            <input
              name="title"
              required
              maxLength={300}
              pattern=".*\S.*"
              title="请输入章节标题"
              placeholder="例如：第一章 绪论"
              defaultValue={section.title ?? ""}
            />
          </label>
          <div className="form-grid">
            <label className="field">
              <span>章节类型</span>
              <select name="kind" defaultValue={section.kind ?? "body"}>
                {Object.entries(sectionKinds).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>目标字数</span>
              <input
                name="target_words"
                type="number"
                required
                min={0}
                max={300000}
                step={1}
                defaultValue={section.target_words ?? 1500}
              />
            </label>
            <label className="field">
              <span>目录排序</span>
              <input
                name="position"
                type="number"
                required
                min={1}
                step={1}
                defaultValue={
                  section.position ??
                  Math.max(0, ...sections.map((item) => item.position)) + 1
                }
              />
            </label>
          </div>
          <label className="field">
            <span>写作目标 / 完成条件</span>
            <textarea
              name="goal"
              rows={4}
              placeholder="说明本节需要回答的问题、使用的材料与完成条件。"
              defaultValue={
                section.kind === "abstract" || section.kind === "abstract_en"
                  ? sectionGoal(section)
                  : (section.goal ?? "")
              }
            />
          </label>
          <fieldset className="outline-dependencies">
            <legend>依赖章节</legend>
            <p>可选择多个前置章节；不选表示无明确前置章节。</p>
            {sections
              .filter((item) => item.id !== section.id)
              .map((item) => (
                <label className="checkbox" key={item.id}>
                  <input
                    name="dependencies"
                    type="checkbox"
                    value={item.id}
                    defaultChecked={selectedDependencies.includes(item.id)}
                  />
                  <span>{item.title}</span>
                </label>
              ))}
            {sections.every((item) => item.id === section.id) && (
              <p>添加其他章节后可设置依赖。</p>
            )}
          </fieldset>
        </fieldset>
        <div className="outline-plan-actions">
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button type="submit" className="btn primary" disabled={busy}>
            {busy && <LoaderCircle size={16} className="spin" />}
            {busy ? "正在保存…" : "保存计划"}
          </button>
        </div>
      </form>
    </WorkspaceDialog>
  );
}

export default function OutlineWorkspace({ pid, tick, act, onWrite }: Props) {
  const {
    data: sections,
    error,
    loading,
    load,
  } = useWorkspaceResource<Section[]>("/projects/" + pid + "/sections", tick);
  const [editing, setEditing] = useState<Partial<Section> | null>(null);
  const [pending, setPending] = useState("");
  const [batchNotice, setBatchNotice] = useState("");
  const pendingRef = useRef(false);
  const total = sections?.reduce(
    (sum, section) =>
      sum + (section.kind === "abstract_en" ? 0 : section.target_words),
    0,
  );
  const run = async (name: string, fn: () => Promise<any>, message: string) => {
    if (pendingRef.current) return null;
    pendingRef.current = true;
    setPending(name);
    try {
      return await act(fn, message);
    } finally {
      pendingRef.current = false;
      setPending("");
    }
  };

  return (
    <section className="outline-workspace" aria-label="提纲与计划">
      <header className="page-title outline-heading">
        <div>
          <div className="eyebrow">PAPER WORKBENCH</div>
          <h1>
            让每一章都有<span>明确任务。</span>
          </h1>
          <p>明确写作目标、篇幅与前置材料，再从准备充分的章节开始。</p>
        </div>
        <div className="actions" aria-label="提纲操作">
          <PaperWritingButton pid={pid} act={act} onChanged={() => void load()} />
          <button
            className="btn"
            disabled={!!pending}
            onClick={() =>
              void run(
                "template",
                () => send("/projects/" + pid + "/outline/template"),
                "基础提纲已建立，可按实际要求修改",
              )
            }
          >
            {pending === "template" ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <ListTree size={17} />
            )}
            使用类型提纲
          </button>
          <button
            className="btn"
            disabled={!!pending}
            onClick={() =>
              void run(
                "ai",
                () => send("/projects/" + pid + "/outline/ai"),
                "提纲候选任务已创建，结果在任务中心",
              )
            }
          >
            {pending === "ai" ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <Sparkles size={18} />
            )}
            AI 规划候选
          </button>
          <button
            className="btn"
            disabled={!!pending || !sections?.length}
            onClick={async () => {
              if (
                confirm(
                  "将按章节顺序创建候选任务，逐次调用可能计费；英文摘要在中文摘要采用并保存后翻译。候选不会自动覆盖正文。确认继续？",
                )
              ) {
                const result = await run(
                  "batch",
                  () => send("/projects/" + pid + "/writing-batch"),
                  "连续生成请求已处理，请在任务中心查看候选",
                );
                if (result) {
                  setBatchNotice(
                    `已创建 ${result.tasks?.length ?? 0} 个候选任务。${(result.skipped_sections ?? []).map((item: { reason: string }) => item.reason).join("；")}`,
                  );
                }
              }
            }}
          >
            {pending === "batch" ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <Sparkles size={18} />
            )}
            连续生成候选
          </button>
          <button
            className="btn primary"
            disabled={!!pending || !sections}
            onClick={() => setEditing({})}
          >
            <Plus size={18} />
            新增章节
          </button>
        </div>
      </header>

      <OutlineManager pid={pid} tick={tick} act={act} />

      <div className="summary-strip" aria-label="章节统计">
        <span>
          共 <strong>{sections?.length ?? "—"}</strong> 个章节
        </span>
        <span>
          分配篇幅 <strong>{total?.toLocaleString("zh-CN") ?? "—"}</strong>
        </span>
        <span>
          待复查{" "}
          <strong>
            {sections?.filter((section) => section.stale).length ?? "—"}
          </strong>
        </span>
      </div>

      {batchNotice && (
        <p className="panel outline-batch-notice" role="status">
          {batchNotice}
        </p>
      )}

      {error ? (
        <div className="panel outline-feedback" role="alert">
          <BookOpen size={28} />
          <h2>暂时无法加载章节</h2>
          <p>{error}</p>
          <button
            className="btn"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw size={16} className={loading ? "spin" : ""} />
            重新加载
          </button>
        </div>
      ) : !sections ? (
        <div className="panel outline-feedback" role="status">
          <LoaderCircle size={24} className="spin" />
          <p>正在加载章节计划…</p>
        </div>
      ) : sections.length ? (
        <ol className="panel no-padding outline-chapters" aria-label="论文章节">
          {sections.map((section, index) => (
            <li
              className="outline-row"
              key={section.id}
              data-section-id={section.id}
            >
              <span className="outline-index" aria-hidden="true">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="grow outline-chapter-copy">
                <h3>
                  {section.title}
                  {(section.kind === "abstract" ||
                    section.kind === "abstract_en") && (
                    <span className="outline-abstract-language">
                      {section.kind === "abstract" ? "中文摘要" : "英文摘要"}
                    </span>
                  )}
                </h3>
                <p>{sectionGoal(section)}</p>
                <small>
                  {section.kind === "abstract_en"
                    ? chineseAbstractFor(
                        section,
                        sections,
                      )?.current_text?.trim()
                      ? `来源：${chineseAbstractFor(section, sections)?.title} · ${section.current_revision_id ? "已有译文" : "尚未翻译"}`
                      : "请先完成并保存中文摘要"
                    : `目标 ${section.target_words} 字 · ${section.current_revision_id ? "已有正文" : "尚未写作"}`}
                  {section.stale ? " · 需要复查" : ""}
                </small>
              </div>
              <div className="outline-row-actions">
                <span className={`badge ${section.stale ? "amber" : ""}`}>
                  {section.stale
                    ? "待复查"
                    : section.state === "empty"
                      ? section.kind === "abstract_en"
                        ? "待翻译"
                        : "待写作"
                      : "已有草稿"}
                </span>
                {section.kind === "abstract_en" ? (
                  <button
                    className="btn"
                    disabled={
                      !!pending ||
                      !chineseAbstractFor(
                        section,
                        sections,
                      )?.current_text?.trim()
                    }
                    title={
                      chineseAbstractFor(
                        section,
                        sections,
                      )?.current_text?.trim()
                        ? "将已保存的中文摘要翻译为英文候选"
                        : "请先完成并保存中文摘要"
                    }
                    onClick={() =>
                      void run(
                        "translate:" + section.id,
                        () =>
                          send("/sections/" + section.id + "/writing-tasks", {
                            operation: "translate",
                            base_revision_id: section.current_revision_id,
                            translation_source_section_id: chineseAbstractFor(
                              section,
                              sections,
                            )?.id,
                            translation_source_revision_id: chineseAbstractFor(
                              section,
                              sections,
                            )?.current_revision_id,
                          }),
                        "英文摘要翻译候选已创建，请在任务中心审阅后采用",
                      )
                    }
                  >
                    {pending === "translate:" + section.id && (
                      <LoaderCircle className="spin" size={16} />
                    )}
                    {section.current_revision_id ? "重新翻译" : "翻译中文摘要"}
                  </button>
                ) : (
                  <button className="btn" onClick={() => setEditing(section)}>
                    编辑计划
                  </button>
                )}
                <button
                  className="icon-button"
                  aria-label={"打开写作：" + section.title}
                  title={"打开写作：" + section.title}
                  onClick={() => onWrite(section.id)}
                >
                  <ArrowUpRight size={20} />
                </button>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <div className="panel outline-feedback">
          <BookOpen size={32} />
          <h2>建立论文的章节结构</h2>
          <p>使用类型提纲开始，或逐章添加。自动建议仍需按学校要求确认。</p>
          <button className="btn primary" onClick={() => setEditing({})}>
            <Plus size={17} />
            新增章节
          </button>
        </div>
      )}

      {editing && (
        <SectionPlanDialog
          section={editing}
          sections={sections ?? []}
          busy={pending === "save"}
          onClose={() => setEditing(null)}
          onSave={async (values) => {
            const result = await run(
              "save",
              () =>
                send(
                  editing.id
                    ? "/sections/" + editing.id
                    : "/projects/" + pid + "/sections",
                  values,
                  editing.id ? "PATCH" : "POST",
                ),
              "章节计划已保存",
            );
            if (result) setEditing(null);
          }}
        />
      )}
    </section>
  );
}

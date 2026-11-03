import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  FileText,
  LoaderCircle,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";
import { api, send } from "../client";
import WorkspaceDialog from "../workspace/WorkspaceDialog";
import "./paperWriting.css";
import DocumentPreview from "./DocumentPreview";
import EvidenceTrace from "../writing/EvidenceTrace";
import WritingFlowControls from "../writing/WritingFlowControls";

type Row = Record<string, any>;
type Props = {
  pid: string;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
  onChanged?: () => void;
};

const amount = (value: number) =>
  String(Math.floor(Math.max(0, value) * 1e6) / 1e6);
const resumable = [
  "failed",
  "budget_blocked",
  "cancelled",
  "outcome_unknown",
  "incomplete",
];

export function PaperWritingButton({ pid, act, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn primary" onClick={() => setOpen(true)}>
        <BookOpen size={17} />
        整篇论文写作
      </button>
      {open && (
        <PaperWorkspace
          pid={pid}
          act={act}
          onChanged={onChanged}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

export function PaperTaskDialog({
  task,
  onClose,
  ...props
}: Props & { task: Row; onClose: () => void }) {
  return <PaperWorkspace {...props} initialTask={task} onClose={onClose} />;
}

function PaperWorkspace({
  pid,
  act,
  onChanged,
  initialTask,
  onClose,
}: Props & { initialTask?: Row; onClose: () => void }) {
  const [task, setTask] = useState<Row | null>(initialTask ?? null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [study, setStudy] = useState<Row | null>(null);
  const [instruction, setInstruction] = useState("");
  const [route, setRoute] = useState("primary");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedFindings, setSelectedFindings] = useState<string[]>([]);
  const [selectedSections, setSelectedSections] = useState<string[]>([]);
  const [revisionInstruction, setRevisionInstruction] = useState("");
  const [revisionBudget, setRevisionBudget] = useState("");
  const fresh = useRef(false);
  const pending = useRef(false);
  const drafted = useRef(false);
  const adoptionKey = useRef(crypto.randomUUID());
  const startKey = useRef(crypto.randomUUID());
  const revisionKey = useRef(crypto.randomUUID());
  const initial = useRef(initialTask);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api(`/projects/${pid}/paper-writing/preflight?provider_profile=${route}`)
      .then(async (data) => {
        if (!active) return;
        setPreview(data);
        if (!initial.current && !fresh.current && data.latest_task_id) {
          const previous = await api(`/tasks/${data.latest_task_id}`);
          if (active) setTask(previous);
        } else if (!initial.current) setBudget(amount(data.budget.available));
      })
      .catch((failure) => {
        if (active) setError((failure as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    // The study only reads local records; a failure here must not block writing.
    api(`/projects/${pid}/paper-writing/study?provider_profile=${route}`)
      .then((data) => {
        if (!active) return;
        setStudy(data);
        if (!drafted.current && data?.requirement_draft) {
          drafted.current = true;
          setInstruction((current) => (current.trim() ? current : data.requirement_draft));
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [pid, route]);

  useEffect(() => {
    if (!task) return;
    setBudget(String(task.payload?.budget_limit ?? ""));
    setReviewed(false);
    setAcknowledged(false);
    adoptionKey.current = crypto.randomUUID();
    setSelectedFindings([]);
    setSelectedSections([]);
    setRevisionInstruction("");
    revisionKey.current = crypto.randomUUID();
  }, [task?.id, task?.result?.paper_hash]);

  useEffect(() => {
    if (!task || task.status !== "succeeded" || !task.result?.complete) return;
    let active = true;
    api(`/projects/${pid}/paper-writing/${task.id}/review`)
      .then((review) => {
        if (active) setTask((current) => current && current.id === task.id && current.result?.paper_hash === review.paper_hash
          ? { ...current, result: { ...current.result, review } } : current);
      })
      .catch((failure) => { if (active) setError((failure as Error).message); });
    api(`/projects/${pid}/paper-writing/preflight?provider_profile=${task.payload?.provider_profile ?? "primary"}`)
      .then((data) => {
        if (active) { setPreview(data); setRevisionBudget(amount(data.budget.available)); }
      })
      .catch((failure) => { if (active) setError((failure as Error).message); });
    return () => { active = false; };
  }, [pid, task?.id, task?.status, task?.result?.paper_hash]);

  // Poll the running task with a self-scheduling loop: a failing read must not
  // stop the loop or freeze the view on a stale snapshot, and a hung read must
  // not pile up requests every two seconds.
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const poll = async () => {
      if (!active) return;
      try {
        const next = await api(`/tasks/${task.id}`, { timeout: 10000 });
        if (!active) return;
        failures = 0;
        setPollError("");
        setTask(next);
        if (!["queued", "running"].includes(next.status)) {
          onChanged?.();
          return;
        }
        timer = setTimeout(() => void poll(), 2000);
      } catch (failure) {
        if (!active) return;
        failures += 1;
        const status = (failure as { status?: number }).status;
        setPollError(
          status === 401
            ? "与本机服务的连接已失效（服务可能重启过），请通过启动器链接重新打开页面查看进度"
            : `进度刷新失败：${(failure as Error).message}，正在自动重试…`,
        );
        timer = setTimeout(() => void poll(), Math.min(10000, 2000 * failures));
      }
    };
    timer = setTimeout(() => void poll(), 2000);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [task?.id, task?.status]);

  const perform = async (fn: () => Promise<any>, message: string) => {
    if (pending.current) return null;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      return await act(async () => {
        try {
          return await fn();
        } catch (failure) {
          setError((failure as Error).message);
          throw failure;
        }
      }, message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  const start = () =>
    perform(async () => {
      setPollError("");
      const created = await api(`/projects/${pid}/paper-writing`, {
        method: "POST",
        headers: { "Idempotency-Key": startKey.current },
        body: JSON.stringify({
          instruction,
          provider_profile: route,
          budget_limit: Number(budget),
          outline_revision: preview?.outline_revision,
          section_versions: preview?.section_versions,
        }),
      });
      setTask(created);
      onChanged?.();
      return created;
    }, "整篇写作任务已创建；完成后可统一审阅并整体写入");

  const result = task?.result ?? {};
  const sections: Row[] = result.paper_sections ?? [];
  const plan: Row[] =
    task?.payload?.snapshot?.sections ?? preview?.sections ?? [];
  const running = !!task && ["queued", "running"].includes(task.status);
  const complete = task?.status === "succeeded" && result.complete;
  const adopted = result.adoption;
  const superseded = result.superseded_by_task_id;
  const findings: Row[] = result.review?.findings ?? [];
  const structureErrors = findings.filter((f) => f.severity === "error");
  const toggle = (values: string[], value: string) => values.includes(value)
    ? values.filter((v) => v !== value) : [...values, value];
  const locate = (sid: string) => document.getElementById(`paper-section-${sid}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const references: Row[] = task?.payload?.snapshot?.references ?? [];
  const cited = new Map<string, number>();
  for (const section of sections) {
    for (const match of String(section.text).matchAll(/\[cite:([^\]]+)\]/g)) {
      if (!cited.has(match[1])) cited.set(match[1], cited.size + 1);
    }
  }
  const readable = (text: string) =>
    text.replace(
      /\[cite:([^\]]+)\]/g,
      (_, id: string) => `[${cited.get(id) ?? "引用"}]`,
    );
  const chapterInfo = (sid: string) =>
    (study?.chapters ?? []).find((c: Row) => c.section_id === sid);
  const estimate = study?.estimate;
  const tokens = (n: number) =>
    n >= 10000 ? `${(n / 10000).toFixed(1)} 万` : String(n);
  const fillEstimate = () => {
    if (!estimate?.total) return;
    const available = Number(preview?.budget?.available ?? 0);
    const planned = Math.min(available, Math.ceil(estimate.total * 1.25 * 1e6) / 1e6);
    if (planned > 0) setBudget(String(planned));
  };
  const newTask = async () => {
    fresh.current = true;
    initial.current = undefined;
    startKey.current = crypto.randomUUID();
    drafted.current = false;
    setTask(null);
    setError("");
    setPollError("");
    setLoading(true);
    try {
      const data = await api(
        `/projects/${pid}/paper-writing/preflight?provider_profile=${route}`,
      );
      setPreview(data);
      setBudget(amount(data.budget.available));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setLoading(false);
    }
    try {
      const data = await api(
        `/projects/${pid}/paper-writing/study?provider_profile=${route}`,
      );
      setStudy(data);
      if (data?.requirement_draft) {
        drafted.current = true;
        setInstruction(data.requirement_draft);
      }
    } catch {
      /* 研判只是建议，读取失败不影响发起写作。 */
    }
  };

  return (
    <WorkspaceDialog
      title="整篇论文写作"
      wide
      busy={busy}
      closeLabel="关闭整篇写作"
      onClose={onClose}
    >
      <div className="paper-writing">
        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}
        {!task ? (
          <>
            <div className="paper-intro">
              <BookOpen size={25} />
              <div>
                <h3>一次发起，完成整篇候选。</h3>
                <p>
                  按全篇提纲自动完成正文、总结及中英文摘要，统一审阅后一次写入。
                </p>
              </div>
            </div>
            {loading ? (
              <p className="muted">
                <LoaderCircle size={16} className="spin" />
                正在读取论文计划与预算…
              </p>
            ) : (
              <>
                <div className="paper-summary">
                  <span>
                    <strong>{preview?.sections?.length ?? 0}</strong> 个章节
                  </span>
                  <span>
                    目标约{" "}
                    <strong>
                      {(preview?.target_words ?? 0).toLocaleString()}
                    </strong>{" "}
                    字
                  </span>
                  <span>
                    可用预算{" "}
                    <strong>
                      {preview?.budget?.available?.toFixed(4) ?? "—"}
                    </strong>{" "}
                    {preview?.budget?.currency}
                  </span>
                </div>
                {!!preview?.blockers?.length && (
                  <div className="alert">
                    {preview?.blockers.map((text: string) => (
                      <p key={text}>{text}</p>
                    ))}
                  </div>
                )}
                {study && (
                  <section className="paper-study" aria-label="写作研判">
                    <div className="paper-study-head">
                      <div>
                        <h3>写作研判</h3>
                        <p className="paper-note">{study.summary?.line}</p>
                      </div>
                      <span
                        className={`paper-badge ${
                          study.attention ? "warn" : "ok"
                        }`}
                      >
                        {study.attention
                          ? `${study.attention} 项需要先处理`
                          : "依据基本齐备"}
                      </span>
                    </div>
                    {!!study.risks?.length && (
                      <ul className="paper-risks">
                        {study.risks.map((risk: Row) => (
                          <li key={risk.code} className={`level-${risk.level}`}>
                            <strong>
                              {risk.level === "high"
                                ? "先处理"
                                : risk.level === "medium"
                                  ? "建议处理"
                                  : "提示"}
                            </strong>
                            <span>
                              {risk.message}
                              {risk.advice ? `　${risk.advice}` : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="paper-note">{study.basis}</p>
                  </section>
                )}
                <label className="field">
                  <span>整篇写作要求</span>
                  <textarea
                    rows={6}
                    maxLength={20000}
                    aria-label="整篇写作要求"
                    value={instruction}
                    onChange={(event) => setInstruction(event.target.value)}
                    placeholder="说明整篇论文要回答的问题、采用的方法、章节侧重点和表达要求。"
                  />
                </label>
                {!!study?.requirement_draft && (
                  <p className="paper-note paper-draft-note">
                    已按提纲、研究事实与资料范围起草，可直接修改。
                    <button
                      className="btn"
                      onClick={() => {
                        drafted.current = true;
                        setInstruction(study.requirement_draft);
                      }}
                    >
                      恢复研判草案
                    </button>
                  </p>
                )}
                <div className="paper-controls">
                  <label className="field">
                    <span>写作模型</span>
                    <select
                      value={route}
                      onChange={(event) => {
                        fresh.current = true;
                        setRoute(event.target.value);
                      }}
                    >
                      <option value="primary">主模型</option>
                      <option value="gpt">GPT 备选</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>
                      本次整篇写作总预算（{preview?.budget?.currency ?? "USD"}）
                    </span>
                    <input
                      type="number"
                      min="0.000001"
                      step="any"
                      value={budget}
                      onChange={(event) => setBudget(event.target.value)}
                    />
                  </label>
                </div>
                {!!estimate && (
                  <p className="paper-note paper-estimate">
                    <span>
                      {estimate.priced
                        ? `预计花费 ${estimate.currency} ${Number(estimate.total).toFixed(4)}，`
                        : "接口未登记单价，只能给出 token 预计，"}
                      共{" "}
                      {estimate.per_section?.length ??
                        preview?.sections?.length ??
                        0}{" "}
                      次请求，输入约 {tokens(estimate.input_tokens)} token、输出约{" "}
                      {tokens(estimate.output_tokens)} token。{estimate.note}
                    </span>
                    {estimate.priced && (
                      <button
                        className="btn"
                        onClick={fillEstimate}
                        disabled={busy}
                      >
                        按预计填入预算
                      </button>
                    )}
                  </p>
                )}
                {preview?.warnings?.map((text: string) => (
                  <p className="paper-note" key={text}>
                    {text}
                  </p>
                ))}
                <details className="paper-plan">
                  <summary>查看整篇章节计划</summary>
                  <ol>
                    {plan.map((s) => {
                      const info = chapterInfo(s.id);
                      const share =
                        info && preview?.target_words
                          ? Math.round(
                              (s.target_words * 100) / preview.target_words,
                            )
                          : null;
                      return (
                        <li key={s.id}>
                          <span>{s.title}</span>
                          <small>
                            {s.target_words.toLocaleString()} 字
                            {share !== null ? ` · 占 ${share}%` : ""}
                            {(() => {
                              const step = preview?.writing_plan?.find(
                                (item: Row) => item.section_id === s.id,
                              );
                              return step?.paragraphs ? ` · ${step.paragraphs} 段` : "";
                            })()}
                            {info
                              ? ` · 依据 ${info.contract_evidence} 条 · 事实 ${info.contract_facts} 条`
                              : ""}
                            {info?.has_content
                              ? ` · 已有 ${info.existing_words} 字`
                              : ""}
                          </small>
                          <p className="paper-note">
                            {info?.goal_is_generic
                              ? preview?.writing_plan?.find(
                                  (item: Row) => item.section_id === s.id,
                                )?.role
                              : info?.goal}
                          </p>
                        </li>
                      );
                    })}
                  </ol>
                </details>
                <WritingFlowControls pid={pid} sections={plan.map((section) => ({ id: String(section.id), title: String(section.title) }))} disabled={busy} onChanged={onChanged} />
                <div className="paper-footer">
                  <p className="muted">
                    生成过程保留为候选，现有正文在统一采用前保持原样。
                  </p>
                  <button
                    className="btn primary"
                    disabled={
                      busy ||
                      !preview?.can_start ||
                      !Number.isFinite(Number(budget)) ||
                      Number(budget) <= 0
                    }
                    onClick={() => void start()}
                  >
                    {busy ? (
                      <LoaderCircle className="spin" size={17} />
                    ) : (
                      <Play size={17} />
                    )}
                    生成整篇候选
                  </button>
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <div className="paper-progress-heading">
              <div>
                <h3>
                  {adopted
                    ? "整篇已写入正文"
                    : complete
                      ? "整篇候选已完成"
                      : running
                        ? "正在完成整篇论文"
                        : "整篇进度已保留"}
                </h3>
                <p>{task.phase || "等待执行"}</p>
              </div>
              <span className="paper-count">
                {result.completed_sections ?? sections.length} /{" "}
                {result.total_sections ?? plan.length} 章
              </span>
            </div>
            {!!task.payload?.revision_source_task_id && (
              <p className="paper-note">
                第 {task.payload.revision_number} 轮整篇修订：保留 {result.retained_sections ?? 0} 章，
                已修订 {result.revised_sections ?? 0} / {task.payload.revision_section_ids?.length ?? 0} 章。
                结论、摘要与依赖章节会随受影响正文更新。
              </p>
            )}
            <div
              className="paper-progress"
              role="progressbar"
              aria-label="整篇写作进度"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={task.progress ?? 0}
            >
              <span
                style={{
                  width: `${Math.min(100, Math.max(0, task.progress ?? 0))}%`,
                }}
              />
            </div>
            {running && Number(task.progress ?? 0) <= 5 && (
              <p className="paper-note">
                首次启动会核对并按需重新采集绑定项目的代码文件，项目文件较多时需要几分钟；下方进度与阶段文字会持续更新。
              </p>
            )}
            {pollError && (
              <div className="paper-poll-alert" role="alert">
                <span>{pollError}</span>
                <button
                  className="btn"
                  onClick={() =>
                    void api(`/tasks/${task.id}`, { timeout: 10000 })
                      .then((next) => {
                        setTask(next);
                        setPollError("");
                      })
                      .catch((failure) =>
                        setPollError((failure as Error).message),
                      )
                  }
                >
                  立即刷新
                </button>
              </div>
            )}
            {task.error && (
              <div className="alert" role="alert">
                {task.error}
              </div>
            )}
            {!!result.invalid_output && (
              <section className="paper-validation-log" aria-label="章节响应校验记录">
                <strong>章节响应校验记录</strong>
                {result.invalid_output.validation_attempts?.length ? (
                  <ol>
                    {result.invalid_output.validation_attempts.map((attempt: Row) => (
                      <li key={attempt.attempt}>
                        第 {attempt.attempt} 次：
                        {attempt.valid ? "校验通过" : attempt.error}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>
                    {result.invalid_output.validation_error ||
                      "旧版任务未保存逐次校验明细，原始响应仍可查看。"}
                  </p>
                )}
                {!!result.invalid_output.raw_text && (
                  <details>
                    <summary>查看最后一次原始响应</summary>
                    <pre className="paper-raw-response">{result.invalid_output.raw_text}</pre>
                  </details>
                )}
              </section>
            )}
            {!!result.candidate_rejections?.length && (
              <section className="paper-validation-log" aria-label="已排除的旧章节候选">
                <strong>旧章节响应校验记录</strong>
                {result.candidate_rejections.map((rejection: Row, index: number) => (
                  <details key={index}>
                    <summary>{rejection.title + "：" + rejection.validation_error}</summary>
                    <p>
                      {rejection.removed_from_checkpoint
                        ? "此候选未通过契约校验，已从完成进度移除；核实费用后继续会重新生成。"
                        : "此响应没有作为完成候选；原始响应已重新解析，重试前请核实费用。"}
                    </p>
                    {!!rejection.raw_text && (
                      <pre className="paper-raw-response">{rejection.raw_text}</pre>
                    )}
                  </details>
                ))}
              </section>
            )}
            {!!result.section_checks?.length && (
              <details className="paper-plan" open>
                <summary>
                  各章自检（{result.section_checks.length} 章已写出）
                </summary>
                <ul className="paper-checks">
                  {result.section_checks.map((check: Row) => {
                    const short =
                      check.ratio !== null && check.ratio !== undefined
                        ? check.ratio < 0.6
                        : false;
                    return (
                      <li
                        key={check.section_id}
                        className={short ? "short" : ""}
                      >
                        <span>{check.title}</span>
                        <small>
                          {check.degraded && "未按JSON契约返回，无引用标注 · "}
                          {check.response_format_retries > 0 &&
                            "响应格式修复重试 " + check.response_format_retries + " 次 · 校验错误：" +
                              (check.response_validation_errors || []).join("；") + " · "}
                          {check.words.toLocaleString()} /{" "}
                          {check.target_words.toLocaleString()} 字 ·{" "}
                          {check.citations} 处引用 · {check.visuals} 个图表
                          {check.points
                            ? ` · 覆盖 ${check.points.covered}/${check.points.required} 点`
                            : ""}
                          {check.placeholders
                            ? ` · ${check.placeholders} 处待补`
                            : ""}
                          {check.brief_findings
                            ? ` · ${check.brief_findings} 项自检提示`
                            : ""}
                          {short ? " · 明显不足，建议修订" : ""}
                        </small>
                      </li>
                    );
                  })}
                </ul>
              </details>
            )}
            {running && (
              <div className="paper-footer">
                <p className="muted">
                  可以关闭窗口，任务会在后台继续；再次打开可查看进度。
                </p>
                <button
                  className="btn"
                  disabled={busy || !!task.cancel_requested}
                  onClick={() =>
                    void perform(async () => {
                      await send(`/tasks/${task.id}/cancel`);
                      setTask(await api(`/tasks/${task.id}`));
                    }, "已请求停止整篇写作，完成的候选会保留")
                  }
                >
                  <Square size={14} />
                  {task.cancel_requested ? "已请求停止" : "停止并保留进度"}
                </button>
              </div>
            )}
            {resumable.includes(task.status) && (
              <div className="paper-resume">
                <p>继续后会复用已完成内容，自动完成剩余部分。</p>
                <label className="field">
                  <span>本任务总预算（包含此前请求）</span>
                  <input
                    type="number"
                    min="0.000001"
                    step="any"
                    value={budget}
                    onChange={(event) => setBudget(event.target.value)}
                  />
                </label>
                {task.resume_requires_ack && (
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(event) =>
                        setAcknowledged(event.target.checked)
                      }
                    />
                    我已核对上次远端执行情况，知悉继续未完成部分可能再次计费
                  </label>
                )}
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      const updated = await send(
                        `/projects/${pid}/paper-writing/${task.id}/resume`,
                        {
                          budget_limit: Number(budget),
                          acknowledge_possible_duplicate_charge: acknowledged,
                        },
                      );
                      setTask(updated);
                      onChanged?.();
                      return updated;
                    }, "整篇任务已继续，已完成候选将复用")
                  }
                >
                  <RefreshCw size={16} />
                  继续完成整篇
                </button>
              </div>
            )}
            {result.review?.placeholder_count > 0 && (
              <div className="alert">
                全文包含 {result.review.placeholder_count}{" "}
                处待补内容，请先核对研究事实与材料。写入后仍是待完善草稿。
              </div>
            )}
            {!!result.review?.repeated_paragraphs?.length && (
              <div className="alert">
                检测到 {result.review.repeated_paragraphs.length}{" "}
                组跨章节重复段落，请在全文审阅时检查。
              </div>
            )}
            {!!result.review?.missing_materials?.length && (
              <details className="paper-gaps">
                <summary>查看整篇缺失材料</summary>
                {result.review.missing_materials.map((item: Row) => (
                  <div key={item.section_id}>
                    <strong>{item.title}</strong>
                    <ul>
                      {item.items.map((text: string, index: number) => (
                        <li key={index}>{text}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </details>
            )}
            {complete && (
              <div className="paper-review" aria-label="全文审阅问题">
                <h3>全文审阅</h3>
                <p className="paper-note">{result.review?.note}</p>
                <p>发现 {findings.length} 项问题，其中 {structureErrors.length} 项影响完整写入。</p>
                {findings.length > 0 && (
                  <details className="paper-gaps" open={findings.length <= 8}>
                    <summary>查看并选择需要修订的问题</summary>
                    <div className="paper-findings">
                      {findings.map((finding) => (
                        <div className="paper-finding" key={finding.id}>
                          <label className="checkbox">
                            <input type="checkbox" disabled={!!adopted || !!superseded || busy}
                              checked={selectedFindings.includes(finding.id)}
                              onChange={() => setSelectedFindings(toggle(selectedFindings, finding.id))} />
                            <span><strong>{finding.severity === "error" ? "需修复" : "需核对"}</strong> · {finding.message}
                              <small>{finding.section_ids.map((sid: string) => plan.find((s) => s.id === sid)?.title ?? "未知章节").join("、")}</small>
                            </span>
                          </label>
                          {finding.excerpt && <blockquote>{finding.excerpt}</blockquote>}
                          {finding.suggested_action && <p className="paper-note">{finding.suggested_action}</p>}
                          <button className="btn" onClick={() => locate(finding.section_ids[0])}>定位章节</button>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                {!!result.review?.section_metrics?.length && (
                  <details className="paper-plan">
                    <summary>查看各章篇幅与引用图表</summary>
                    <ul>{result.review.section_metrics.map((metric: Row) => (
                      <li key={metric.section_id}>{metric.title}：约 {metric.words} 字 / 目标 {metric.target_words} 字，
                        {metric.citations} 处引用，{metric.visuals} 个图表</li>
                    ))}</ul>
                    <p className="paper-note">篇幅按中文字与英文单词估算，目标字数只用于发现明显不足。</p>
                  </details>
                )}
              </div>
            )}
            {!!sections.length && (
              <article className="paper-manuscript" aria-label="整篇候选稿">
                {sections.map((section) => (
                  <section key={section.section_id} id={`paper-section-${section.section_id}`}>
                    <h3>{section.title}</h3>
                    <EvidenceTrace pid={pid} sectionId={section.section_id} bundle={section.evidence_bundle} quality={section.writing_quality} argumentPlan={section.argument_plan} coverage={section.context_coverage} />
                  {section.document?<DocumentPreview document={section.document} sectionId={section.section_id} figures={result.figure_index??[]} assets={result.preview_assets??[]} citations={Object.fromEntries(cited)}/>:<div className="paper-prose">{readable(section.text)}</div>}
                    {!complete && !!section.missing_materials?.length && (
                      <details>
                        <summary>本节待补材料</summary>
                        <ul>
                          {section.missing_materials.map(
                            (text: string, index: number) => (
                              <li key={index}>{text}</li>
                            ),
                          )}
                        </ul>
                      </details>
                    )}
                  </section>
                ))}
                {cited.size > 0 && (
                  <section>
                    <h3>候选引用文献</h3>
                    <ol>
                      {[...cited.keys()].map((id) => {
                        const reference = references.find((r) => r.id === id);
                        let metadata: Row = {};
                        try {
                          metadata =
                            typeof reference?.metadata === "string"
                              ? JSON.parse(reference.metadata)
                              : (reference?.metadata ?? {});
                        } catch {
                          /* Keep unknown metadata explicit. */
                        }
                        return (
                          <li key={id}>
                            {metadata.title ||
                              metadata.citation_raw ||
                              "待核对文献信息"}
                          </li>
                        );
                      })}
                    </ol>
                  </section>
                )}
              </article>
            )}
            {complete && !adopted && !superseded && (
              <div className="paper-revision" aria-label="统一修订全文">
                <h3>按审阅结果统一修订</h3>
                <p className="paper-note">选择上方问题，或指定章节并填写要求。程序会保留其他章节，联动更新依赖章节、结论和摘要，完成后重新检查整篇。</p>
                <details className="paper-plan">
                  <summary>手动选择修订章节（已选 {selectedSections.length} 章）</summary>
                  <div className="paper-section-choices">{plan.map((s) => (
                    <label className="checkbox" key={s.id}>
                      <input type="checkbox" checked={selectedSections.includes(s.id)}
                        onChange={() => setSelectedSections(toggle(selectedSections, s.id))} />{s.title}
                    </label>
                  ))}</div>
                </details>
                {!!result.review?.advice && (
                  <p className="paper-note paper-estimate">
                    <span>
                      可按本次审阅结论起草修订要求，起草结果会带入下面输入框，可再修改。
                    </span>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() =>
                        setRevisionInstruction(result.review?.advice ?? "")
                      }
                    >
                      按审阅结果起草要求
                    </button>
                  </p>
                )}
                <label className="field"><span>整篇修订要求</span>
                  <textarea rows={3} maxLength={20000} aria-label="整篇修订要求" value={revisionInstruction}
                    onChange={(event) => setRevisionInstruction(event.target.value)}
                    placeholder="例如：实现章应据代码解释关键流程；删除无实测依据的性能结论，并相应修订结论与摘要。" />
                </label>
                <label className="field"><span>本轮修订预算（{preview?.budget?.currency ?? "USD"}）</span>
                  <input type="number" min="0.000001" step="any" value={revisionBudget}
                    onChange={(event) => setRevisionBudget(event.target.value)} />
                </label>
                {!!preview?.blockers?.length && <div className="alert">{preview.blockers.map((text: string) => <p key={text}>{text}</p>)}</div>}
                <button className="btn primary" disabled={busy || !preview?.can_start ||
                  !Number.isFinite(Number(revisionBudget)) || Number(revisionBudget) <= 0 ||
                  (!selectedFindings.length && (!selectedSections.length || !revisionInstruction.trim()))}
                  onClick={() => void perform(async () => {
                    const updated = await api(`/projects/${pid}/paper-writing/${task.id}/revise`, {
                      method: "POST", headers: { "Idempotency-Key": revisionKey.current },
                      body: JSON.stringify({ paper_hash: result.paper_hash, section_ids: selectedSections,
                        finding_ids: selectedFindings, instruction: revisionInstruction,
                        budget_limit: Number(revisionBudget) }),
                    });
                    setTask(updated); onChanged?.(); return updated;
                  }, "已开始整篇修订，完成后可再次统一审阅") }>
                  <RefreshCw size={16} />修订并重新审阅整篇
                </button>
              </div>
            )}
            {superseded && (
              <div className="paper-adoption"><p>此候选已有后续整篇修订稿，请继续审阅最新候选。</p>
                <button className="btn primary" disabled={busy} onClick={() => void perform(async () => {
                  const next = await api(`/tasks/${superseded}`); setTask(next); return next;
                }, "已打开后续整篇修订稿")}>打开后续修订稿</button>
              </div>
            )}
            {complete && !adopted && !superseded && (
              <div className="paper-adoption">
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={reviewed}
                    onChange={(event) => setReviewed(event.target.checked)}
                  />
                  我已统一审阅整篇候选，同意将全部章节写入为草稿
                </label>
                <p className="muted">
                  将统一检查章节版本并保存全部内容。候选中的待补证据与未核验状态会保留。
                </p>
                <button
                  className="btn primary"
                  disabled={!reviewed || busy || structureErrors.length > 0}
                  onClick={() =>
                    void perform(async () => {
                      const saved = await api(
                        `/projects/${pid}/paper-writing/${task.id}/accept`,
                        {
                          method: "POST",
                          headers: { "Idempotency-Key": adoptionKey.current },
                          body: JSON.stringify({
                            paper_hash: result.paper_hash,
                            reviewed,
                            accept_policy: "draft",
                          }),
                        },
                      );
                      setTask(await api(`/tasks/${task.id}`));
                      onChanged?.();
                      return saved;
                    }, "整篇论文已统一写入正文，可在章节视图继续修改")
                  }
                >
                  <FileText size={17} />
                  整体写入正文
                </button>
              </div>
            )}
            {adopted && (
              <div className="success-box">
                <CheckCircle2 size={20} />
                <span>
                  已保存 {Object.keys(adopted.section_revisions ?? {}).length}{" "}
                  个章节。可在写作工作台继续修改，历史版本保留。
                </span>
              </div>
            )}
            {!running && (
              <div className="paper-footer">
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void newTask()}
                >
                  <BookOpen size={16} />
                  新建整篇任务
                </button>
                <button className="btn" onClick={onClose}>
                  关闭
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </WorkspaceDialog>
  );
}

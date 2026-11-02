import { useEffect, useState } from "react";
import { api, send } from "../client";
import { ResearchTools, ReferenceInspector } from "./ReferenceWorkbenchParts";
type Row = Record<string, any>;
const read = (value: any) =>
  typeof value === "string" ? JSON.parse(value) : value;

export default function KnowledgeOperations({
  pid,
  paidBlocked,
  selectedClaim,
  namespace = "literature",
  fileIds,
}: {
  pid: string;
  paidBlocked: boolean;
  selectedClaim?: string;
  namespace?: string;
  fileIds?: string[];
}) {
  const base = `/projects/${pid}/knowledge`;
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [job, setJob] = useState<Row | null>(null),
    [output, setOutput] = useState<Row | null>(null);
  const [question, setQuestion] = useState(""),
    [backend, setBackend] = useState("auto"),
    [allowModel, setAllowModel] = useState(false),
    [budget, setBudget] = useState(0.1);
  const [route, setRoute] = useState<Row | null>(null),
    [queue, setQueue] = useState<Row | null>(null);
  const [conflicts, setConflicts] = useState<Row[]>([]),
    [conflict, setConflict] = useState<Row | null>(null),
    [choices, setChoices] = useState<Row>({}),
    [order, setOrder] = useState("");
  const [reviewer, setReviewer] = useState(""),
    [note, setNote] = useState(""),
    [compared, setCompared] = useState(false);
  const [claimId, setClaimId] = useState(selectedClaim || ""),
    [claim, setClaim] = useState<Row | null>(null),
    [support, setSupport] = useState("not_checked");
  const [comparisonId, setComparisonId] = useState(""),
    [report, setReport] = useState<Row | null>(null),
    [official, setOfficial] = useState(false),
    [generateAnswers, setGenerateAnswers] = useState(false);
  const [paper, setPaper] = useState<Row | null>(null),
    [paperConfirmed, setPaperConfirmed] = useState(false),
    [paperCompared, setPaperCompared] = useState(false),
    [paperResult, setPaperResult] = useState("needs_revision");
  const [bundle, setBundle] = useState<Row | null>(null),
    [importMode, setImportMode] = useState("isolated"),
    [title, setTitle] = useState("");
  const [cloud, setCloud] = useState({
    url: "",
    username: "",
    cloud_password: "",
    sync_password: "",
    etag: "",
  });
  useEffect(() => {
    setClaimId(selectedClaim || "");
  }, [selectedClaim]);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const timer = setInterval(() => {
      void api(`/tasks/${job.id}`)
        .then((value) => {
          setJob(value);
          if (value.result) setOutput(value.result);
          if (!["queued", "running"].includes(value.status)) {
            setNotice(`任务状态：${value.status}`);
            if (value.error) setError(value.error);
          }
        })
        .catch((e) => setError(e.message));
    }, 2000);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);
  async function act(work: () => Promise<any>, message = "操作已保存") {
    if (busy) return null;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await work();
      setNotice(message);
      if (result?.id && result?.status) setJob(result);
      return result;
    } catch (e) {
      setError((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function loadQueue() {
    const value = await api(
      base +
        "/maintenance?namespace=" +
        encodeURIComponent(namespace) +
        (fileIds?.[0] ? "&file_id=" + encodeURIComponent(fileIds[0]) : ""),
    );
    setQueue(value);
    return value;
  }
  async function loadConflicts() {
    const value = await api(base + "/wiki-conflicts");
    setConflicts(value);
    return value;
  }
  const routeRequest = {
    query: question,
    backend,
    allow_model: allowModel,
    budget_limit: budget,
    namespace,
    file_ids: fileIds,
  };
  return (
    <div className="knowledge-operations">
      {error && (
        <p role="alert" className="knowledge-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="knowledge-notice">
          {notice}
        </p>
      )}
      <ResearchTools
        inspector={
          <>
            <ReferenceInspector title="本次操作">
              {" "}
              <div className="knowledge-form">
                <label>
                  核对人
                  <input
                    value={reviewer}
                    onChange={(e) => setReviewer(e.target.value)}
                  />
                </label>
                <label>
                  处理说明
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
                <label>
                  本次任务预算
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={budget}
                    onChange={(e) => setBudget(Number(e.target.value))}
                  />
                </label>
              </div>
            </ReferenceInspector>
            <ReferenceInspector title="操作说明">
              <p>先预览范围和预算，再执行所选工具。</p>
              <p>
                维护、合并、裁决和迁移均保留历史记录。人工核对需要实际对照原件。
              </p>
              {paidBlocked && (
                <p className="research-inline-warning">
                  收费操作暂停，本地查询和查看仍可使用。
                </p>
              )}
            </ReferenceInspector>
          </>
        }
        result={
          <>
            {" "}
            {output && (
              <details open>
                <summary>最近任务结果</summary>
                {output.download && (
                  <a className="btn" href={output.download} download>
                    下载完整知识包
                  </a>
                )}
                {output.argument_plan && (
                  <p>
                    候选论点 {output.argument_plan.candidate_arguments.length}{" "}
                    条；比较项{" "}
                    {output.argument_plan.comparability_checks.length}{" "}
                    组；来源覆盖 {output.argument_plan.source_coverage} 份。
                  </p>
                )}
                <details className="research-advanced">
                  <summary>查看完整结果数据</summary>
                  <pre className="knowledge-text">
                    {JSON.stringify(output, null, 2)}
                  </pre>
                </details>
              </details>
            )}
          </>
        }
      >
        <details open>
          <summary>自动路由与论证计划</summary>
          <div className="knowledge-form">
            <label>
              研究问题
              <input
                aria-label="统一查询问题"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
            </label>
            <label>
              查询后端
              <select
                aria-label="统一查询后端"
                value={backend}
                onChange={(e) => setBackend(e.target.value)}
              >
                <option value="auto">按问题、索引和预算自动选择</option>
                <option value="native">原生图谱与Wiki</option>
                <option value="global">微软 Global</option>
                <option value="local">微软 Local</option>
                <option value="drift">微软 DRIFT</option>
              </select>
            </label>
          </div>
          <label className="knowledge-check">
            <input
              type="checkbox"
              checked={allowModel}
              disabled={paidBlocked}
              onChange={(e) => setAllowModel(e.target.checked)}
            />
            允许本次查询调用收费模型
          </label>
          <div className="actions">
            <button
              className="btn"
              disabled={busy || !question}
              onClick={() =>
                void act(async () => {
                  const value = await send(
                    base + "/query/route-preview",
                    routeRequest,
                  );
                  setRoute(value);
                  return value;
                }, "已解释本次路由选择，未调用模型")
              }
            >
              预览路由
            </button>
            <button
              className="btn primary"
              disabled={busy || !question || (allowModel && paidBlocked)}
              onClick={() =>
                void act(
                  () => send(base + "/query/execute", routeRequest),
                  "统一查询已进入任务中心",
                )
              }
            >
              执行统一查询
            </button>
          </div>
          {route && (
            <p>
              推荐 {route.recommended_backend} · 当前选择{" "}
              {route.selected_backend} ·{" "}
              {route.reasons.join("；") || "当前条件可用"}
            </p>
          )}
        </details>

        <details>
          <summary>完整增量维护队列</summary>
          <p>
            按支持性核对、Wiki综合、社区报告顺序保存检查点。预算不足时保留剩余项，不重复发送执行不明的请求。
          </p>
          <div className="actions">
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const value = await send(base + "/maintenance/preview", {
                    namespace,
                    file_ids: fileIds,
                    support_checks: true,
                    discover_topics: true,
                    reports: true,
                  });
                  setQueue(value);
                  return value;
                }, "完整维护计划已保存，尚未发送收费请求")
              }
            >
              准备当前范围维护计划
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() => void act(loadQueue, "已读取维护检查点")}
            >
              刷新队列
            </button>
            <button
              className="btn primary"
              disabled={busy || paidBlocked}
              onClick={() =>
                void act(
                  () =>
                    send(base + "/maintenance/run", {
                      namespace,
                      file_ids: fileIds,
                      budget_limit: budget,
                      reports: true,
                    }),
                  "已提交维护任务",
                )
              }
            >
              运行或续接维护（计费）
            </button>
          </div>
          {queue && (
            <>
              <p>
                {Object.entries(queue.counts)
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ")}
              </p>
              {queue.items.slice(0, 80).map((item: Row) => (
                <p key={item.id}>
                  {item.kind} · {item.target_id} · {item.status}
                  {item.error ? `：${item.error}` : ""}
                </p>
              ))}
              {queue.items.length > 80 && (
                <p>已显示80项，其余检查点可通过任务结果导出。</p>
              )}
            </>
          )}
        </details>

        <details>
          <summary>Wiki并发编辑合并</summary>
          <button
            className="btn"
            disabled={busy}
            onClick={() => void act(loadConflicts, "冲突列表已读取")}
          >
            读取编辑冲突
          </button>
          {conflicts
            .filter((c) => c.state === "open")
            .map((c) => (
              <button
                className="btn"
                key={c.id}
                onClick={() =>
                  void act(async () => {
                    const value = await api(base + `/wiki-conflicts/${c.id}`);
                    setConflict(value);
                    setChoices({});
                    setOrder("");
                    return value;
                  }, "请逐块比较双方内容")
                }
              >
                {c.page_id} · {c.created_at}
              </button>
            ))}
          {conflict && (
            <>
              <p>
                {conflict.conflict_count}
                处需要处理；合并只生成候选，发布仍需独立执行。
              </p>
              {conflict.blocks.map((b: Row) => (
                <div className="knowledge-block" key={b.key}>
                  <strong>
                    {b.key} · {b.conflict ? "内容冲突" : "可自动合并"}
                  </strong>
                  <p>基础：{b.base?.text || "无"}</p>
                  <p>本地：{b.local?.text || "已删除"}</p>
                  <p>候选：{b.incoming?.text || "已删除"}</p>
                  {b.conflict && (
                    <>
                      <label>
                        处理选择
                        <select
                          value={choices[b.key]?.choice || ""}
                          onChange={(e) =>
                            setChoices({
                              ...choices,
                              [b.key]: {
                                choice: e.target.value,
                                block: {
                                  ...(b.local || b.incoming),
                                  text:
                                    choices[b.key]?.block?.text ||
                                    b.local?.text ||
                                    b.incoming?.text ||
                                    "",
                                },
                              },
                            })
                          }
                        >
                          <option value="">请选择</option>
                          <option value="local">保留本地</option>
                          <option value="incoming">采用候选</option>
                          <option value="manual">
                            手工合并（保留所选块的出处）
                          </option>
                        </select>
                      </label>
                      {choices[b.key]?.choice === "manual" && (
                        <textarea
                          value={choices[b.key].block.text}
                          onChange={(e) =>
                            setChoices({
                              ...choices,
                              [b.key]: {
                                ...choices[b.key],
                                block: {
                                  ...choices[b.key].block,
                                  text: e.target.value,
                                },
                              },
                            })
                          }
                        />
                      )}
                    </>
                  )}
                </div>
              ))}
              {conflict.order_conflict && (
                <label>
                  合并后的块顺序，以逗号分隔
                  <input
                    value={order}
                    onChange={(e) => setOrder(e.target.value)}
                    placeholder={conflict.local_order.join(",")}
                  />
                </label>
              )}
              <button
                className="btn primary"
                disabled={busy || !reviewer || !note}
                onClick={() =>
                  void act(async () => {
                    const value = await send(
                      base + `/wiki-conflicts/${conflict.id}/resolve`,
                      {
                        expected_fingerprint: conflict.fingerprint,
                        choices,
                        reviewer,
                        note,
                        ...(order
                          ? { order: order.split(",").map((v) => v.trim()) }
                          : {}),
                      },
                    );
                    setConflict(null);
                    await loadConflicts();
                    return value;
                  }, "合并候选已保存，原版本完整保留")
                }
              >
                保存合并候选
              </button>
            </>
          )}
        </details>

        <details>
          <summary>论断支持性裁决</summary>
          <div className="knowledge-form">
            <label>
              论断版本ID
              <input
                aria-label="裁决论断ID"
                value={claimId}
                onChange={(e) => setClaimId(e.target.value)}
              />
            </label>
            <button
              className="btn"
              disabled={busy || !claimId}
              onClick={() =>
                void act(async () => {
                  const value = await api(base + `/assertions/${claimId}`);
                  setClaim(value);
                  setSupport(value.support_state);
                  return value;
                }, "已读取各方判断及其原文版本")
              }
            >
              读取判断记录
            </button>
          </div>
          {claim && (
            <>
              <p>{claim.proposition.statement}</p>
              <p>
                当前采用：{claim.support_state} · {claim.decision_basis}
                {claim.requires_adjudication ? " · 尚有冲突需裁决" : ""}
              </p>
              {claim.assessments.map((a: Row) => (
                <p key={a.id}>
                  {a.method} · {a.reviewer} · {a.support} · 引句：{a.quote} ·{" "}
                  {a.note}
                </p>
              ))}
              <label>
                裁决结果
                <select
                  value={support}
                  onChange={(e) => setSupport(e.target.value)}
                >
                  {[
                    "supported",
                    "partial",
                    "contradicted",
                    "insufficient",
                    "not_checked",
                  ].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
              <label className="knowledge-check">
                <input
                  type="checkbox"
                  checked={compared}
                  onChange={(e) => setCompared(e.target.checked)}
                />
                已实际对照所列原文，并核对条件和结论强度
              </label>
              <button
                className="btn primary"
                disabled={
                  busy ||
                  !compared ||
                  !reviewer ||
                  !note ||
                  !claim.active_assessment_ids?.length
                }
                onClick={() =>
                  void act(async () => {
                    const value = await send(
                      base + `/assertions/${claimId}/decisions`,
                      {
                        expected_assessment_fingerprint:
                          claim.assessment_fingerprint,
                        assessment_ids: claim.active_assessment_ids,
                        support,
                        reviewer,
                        note,
                        compared_original: compared,
                      },
                    );
                    setClaim(await api(base + `/assertions/${claimId}`));
                    return value;
                  }, "裁决已追加保存，旧判断未覆盖")
                }
              >
                登记采用裁决
              </button>
            </>
          )}
        </details>

        <details>
          <summary>完整对照评测与统计</summary>
          <p>
            纯检索对照提供召回与定位指标。语义质量验收需要实际回答、80项真实任务人工标注和独立盲审；来源或标注变化后须重新核对。
          </p>
          <label className="knowledge-check">
            <input
              type="checkbox"
              checked={generateAnswers}
              disabled={paidBlocked && !generateAnswers}
              onChange={(e) => setGenerateAnswers(e.target.checked)}
            />
            生成供逐句盲审的实际回答（计费）
          </label>
          <label className="knowledge-check">
            <input
              type="checkbox"
              checked={official}
              disabled={paidBlocked && !official}
              onChange={(e) => setOfficial(e.target.checked)}
            />
            增加D组启用官方社区的子对照（计费）
          </label>
          <div className="actions">
            <button
              className="btn"
              disabled={busy || ((official || generateAnswers) && paidBlocked)}
              onClick={() =>
                void act(
                  () =>
                    send(base + "/evaluation/comparisons", {
                      include_official_subcomparison: official,
                      official_mode: "global",
                      generate_answers: generateAnswers,
                      budget_limit: budget,
                    }),
                  "已提交相同输入下的对照评测",
                )
              }
            >
              运行已标注任务对照
            </button>
            <button
              className="btn"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const value = await api(
                    base + "/evaluation/annotation-packet",
                  );
                  const blob = new Blob([JSON.stringify(value, null, 2)], {
                    type: "application/json",
                  });
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = "knowledge-human-annotation-draft.json";
                  a.click();
                  URL.revokeObjectURL(a.href);
                  return { cases: value.cases.length, human_acceptance: false };
                }, "已导出人工标注草稿；未填入人工结论")
              }
            >
              下载人工标注草稿
            </button>
            <label>
              对照记录ID
              <input
                value={comparisonId}
                onChange={(e) => setComparisonId(e.target.value)}
              />
            </label>
            <button
              className="btn"
              disabled={busy || !comparisonId}
              onClick={() =>
                void act(async () => {
                  const value = await api(
                    base + `/evaluation/comparisons/${comparisonId}/report`,
                  );
                  setReport(value);
                  return value;
                }, "已读取统计，未完成的人工评审不会记为通过")
              }
            >
              读取完整统计报告
            </button>
          </div>
          {report && (
            <>
              <p>
                输入仍匹配：{report.input_still_current ? "是" : "否"}
                ；人工质量目标：
                {report.semantic_quality_verified ? "当前样本达标" : "尚未证实"}
              </p>
              <pre className="knowledge-text">
                {JSON.stringify(report.groups, null, 2)}
              </pre>
              <button
                className="btn"
                onClick={() => {
                  const blob = new Blob([JSON.stringify(report, null, 2)], {
                    type: "application/json",
                  });
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = "knowledge-evaluation.json";
                  a.click();
                  URL.revokeObjectURL(a.href);
                }}
              >
                下载评测报告
              </button>
            </>
          )}
        </details>

        <details>
          <summary>真实论文效果验收</summary>
          <p>
            保存真实论文正文和实际引用后，由实际评审者对照原件登记结论。正文、引用或来源变化后须重新验收。
          </p>
          <button
            className="btn"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                const value = await api(base + "/real-paper/acceptance");
                setPaper(value);
                setPaperConfirmed(false);
                setPaperCompared(false);
                return value;
              }, "已检查真实正文、引用和验收版本")
            }
          >
            检查正文与引用
          </button>
          {paper && (
            <>
              <p>
                非空正文版本：{paper.nonempty_current_revisions}；实际引用：
                {paper.current_citations}；
                {paper.human_quality_accepted
                  ? "当前版本已登记人工验收"
                  : "尚未完成真实论文验收"}
              </p>
              {!paper.has_acceptance_target && (
                <p>当前没有可验收的正文及引用，请先保存真实论文。</p>
              )}
              <div className="knowledge-form">
                <label>
                  验收结论
                  <select
                    value={paperResult}
                    onChange={(e) => setPaperResult(e.target.value)}
                  >
                    <option value="needs_revision">需要修订</option>
                    <option value="accepted">当前版本通过</option>
                  </select>
                </label>
              </div>
              <label className="knowledge-check">
                <input
                  type="checkbox"
                  checked={paperConfirmed}
                  onChange={(e) => setPaperConfirmed(e.target.checked)}
                />
                这是本次待验收的真实论文正文
              </label>
              <label className="knowledge-check">
                <input
                  type="checkbox"
                  checked={paperCompared}
                  onChange={(e) => setPaperCompared(e.target.checked)}
                />
                我已对照实际引用的原件核查正文，并在上方填写核对人及具体说明
              </label>
              <button
                className="btn primary"
                disabled={
                  busy ||
                  !paper.has_acceptance_target ||
                  !paperConfirmed ||
                  !paperCompared ||
                  !reviewer.trim() ||
                  !note.trim()
                }
                onClick={() =>
                  void act(async () => {
                    const value = await send(base + "/real-paper/acceptance", {
                      expected_snapshot: paper.snapshot,
                      reviewer,
                      note,
                      real_paper_confirmed: paperConfirmed,
                      compared_original: paperCompared,
                      result: paperResult,
                    });
                    setPaper(value);
                    setPaperConfirmed(false);
                    setPaperCompared(false);
                    return value;
                  }, "人工验收结论已按当前正文与引用版本追加保存")
                }
              >
                登记真实论文验收
              </button>
            </>
          )}
        </details>

        <details>
          <summary>完整知识包与跨项目导入</summary>
          <p>
            包含原件、证据、图谱、Wiki全部历史和资源清单；已有项目不会被覆盖。隔离恢复保留原ID，新项目分支映射ID并重验依赖。
          </p>
          <button
            className="btn"
            disabled={busy}
            onClick={() =>
              void act(
                () => send(base + "/transfers/export"),
                "完整知识包正在打包，任务完成后可下载",
              )
            }
          >
            导出完整知识包
          </button>
          <label className="btn upload-label">
            预览知识包
            <input
              type="file"
              accept=".zip"
              hidden
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file)
                  void act(async () => {
                    const data = new FormData();
                    data.append("file", file);
                    const value = await api(base + "/transfers/preview", {
                      method: "POST",
                      body: data,
                    });
                    setBundle(value);
                    return value;
                  }, "知识包完整性已检查，尚未导入");
              }}
            />
          </label>
          {bundle && (
            <>
              <p>
                {bundle.title} · {bundle.records}条记录 · SHA-256{" "}
                {bundle.sha256}
              </p>
              <label>
                导入方式
                <select
                  value={importMode}
                  onChange={(e) => setImportMode(e.target.value)}
                >
                  {bundle.strategies.map((m: string) => (
                    <option key={m} value={m}>
                      {
                        (
                          {
                            isolated: "恢复隔离副本",
                            fork: "导入新项目分支",
                            restore: "恢复尚不存在的原项目",
                          } as Row
                        )[m]
                      }
                    </option>
                  ))}
                </select>
              </label>
              <label>
                新项目名称
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <button
                className="btn primary"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const value = await send(base + "/transfers/restore", {
                      package_blob: bundle.package_blob,
                      expected_sha256: bundle.sha256,
                      mode: importMode,
                      title,
                    });
                    setOutput(value);
                    return value;
                  }, "已恢复完整知识副本，现有项目保持完整")
                }
              >
                按所选方式导入
              </button>
            </>
          )}
        </details>

        <details>
          <summary>完整知识云同步</summary>
          <p>
            使用独立加密口令和WebDAV
            ETag。须先在协作设置明确开启项目分享；仅本地资料会阻止上传。拉取只准备预览，发生分歧时使用隔离副本或新项目分支保留双方。
          </p>
          <div className="knowledge-form">
            {(
              [
                "url",
                "username",
                "cloud_password",
                "sync_password",
                "etag",
              ] as const
            ).map((k) => (
              <label key={k}>
                {
                  {
                    url: "HTTPS云文件地址",
                    username: "云账号",
                    cloud_password: "云密码",
                    sync_password: "知识包加密口令",
                    etag: "上次ETag（首次留空）",
                  }[k]
                }
                <input
                  type={k.includes("password") ? "password" : "text"}
                  autoComplete="off"
                  value={cloud[k]}
                  onChange={(e) => setCloud({ ...cloud, [k]: e.target.value })}
                />
              </label>
            ))}
          </div>
          <div className="actions">
            <button
              className="btn"
              disabled={busy || !cloud.url || cloud.sync_password.length < 12}
              onClick={() =>
                void act(async () => {
                  const value = await send(base + "/cloud/push", cloud);
                  setCloud({ ...cloud, etag: value.etag || "" });
                  return value;
                }, "完整加密知识包已推送，凭据未写入知识包")
              }
            >
              推送完整知识包
            </button>
            <button
              className="btn"
              disabled={busy || !cloud.url || cloud.sync_password.length < 12}
              onClick={() =>
                void act(async () => {
                  const value = await send(base + "/cloud/pull", cloud);
                  setBundle({
                    ...value.preview,
                    package_blob: value.package_blob,
                  });
                  setCloud({ ...cloud, etag: value.etag || "" });
                  return value;
                }, "已拉取并校验，请在知识包导入中选择恢复方式")
              }
            >
              拉取并预览
            </button>
          </div>
        </details>

        {job && (
          <p role="status">
            任务 {job.id} · {job.status} · {job.phase || ""}
          </p>
        )}
      </ResearchTools>
    </div>
  );
}

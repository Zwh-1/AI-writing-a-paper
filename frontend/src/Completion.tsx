import RuleEditor from "./RuleEditor";
import { useEffect, useRef, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;
type Props = {
  pid: string;
  tick: number;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
  refresh?: () => void;
};
function useData(path: string, tick: number, hold = false) {
  const [data, set] = useState<any>(null);
  useEffect(() => {
    if (hold) return;
    let alive = true;
    api(path)
      .then((d) => {
        if (alive) set(d);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [path, tick, hold]);
  return [data, set] as const;
}
const safe = (v: any, f: any = {}) => {
  try {
    return typeof v === "string" ? JSON.parse(v) : (v ?? f);
  } catch {
    return f;
  }
};
const labels: Row = {
  manual: "人工核对",
  word_min: "最少字数",
  word_max: "最多字数",
  word_range: "字数范围",
  reference_min: "最少引用文献",
  reference_recent: "近年文献比例",
  reference_foreign_ratio: "外文文献比例",
  required_sections: "必需章节",
  required_terms: "必需词项",
  forbidden_terms: "禁止词项",
  heading_min: "最少标题数量",
  figure_min: "最少图数量",
  table_min: "最少表数量",
  format_value: "排版参数",
  rendered_page_min: "实际页数（人工核对）",
};
export function RuleExecutionPanel({ pid, tick, act }: Props) {
  const [data] = useData("/projects/" + pid + "/rule-execution", tick);
  const [editing, setEditing] = useState<Row | null>(null);
  return (
    <details className="panel workflow-panel mb rule-execution-panel">
      <summary>
        <strong>规则执行设置</strong>
        <span>{data?.rules?.length ?? 0} 条规则 · 配置检查器与适用范围</span>
        <span className="rule-expand-hint">展开配置</span>
      </summary>
      <p className="muted mb">
        支持字数、词项、文献、章节结构与排版参数检查。也可以直接在“核对与编辑”中配置；修改后需重新发布。
      </p>
      {data?.rules?.length ? (
        <div className="workflow-list">
          {data.rules.map((r: Row) => (
            <div key={r.id}>
              <div className="grow">
                <p>{r.statement}</p>
                <small>
                  {labels[r.checker] ?? r.checker} · {r.scope} ·{" "}
                  {r.operations.join(" / ")}
                </small>
              </div>
              <button className="btn" onClick={() => setEditing(r)}>
                配置执行方式
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted">整理或新增规则后可配置。</p>
      )}
      {editing && (
        <RuleEditor
          rule={editing}
          pid={pid}
          act={act}
          executionOnly
          onClose={() => setEditing(null)}
        />
      )}
    </details>
  );
}

export function OutlineManager({ pid, tick, act }: Props) {
  const [dirty, setDirty] = useState(false);
  const [data, setData] = useData(
    "/projects/" + pid + "/outline/tree",
    tick,
    dirty,
  );
  const [editing, setEditing] = useState<string | null>(null);
  const update = (id: string, key: string, value: any) => {
    setData((d: Row) => ({
      ...d,
      nodes: d.nodes.map((n: Row) =>
        n.id === id ? { ...n, [key]: value } : n,
      ),
    }));
    setDirty(true);
  };
  const move = (i: number, delta: number) => {
    const nodes = [...data.nodes];
    const j = i + delta;
    if (j < 0 || j >= nodes.length) return;
    [nodes[i], nodes[j]] = [nodes[j], nodes[i]];
    setData({
      ...data,
      nodes: nodes.map((n: Row, k: number) => ({ ...n, position: k + 1 })),
    });
    setDirty(true);
  };
  return (
    <details className="panel mb workflow-panel">
      <summary>
        <strong>提纲层级、排序与依赖管理</strong>
        <span>修改后统一保存；归档保留正文，可恢复</span>
      </summary>
      {data?.nodes?.map((s: Row, i: number) => (
        <div
          className="tree-item"
          key={s.id}
          style={{ paddingLeft: Math.max(0, s.level - 1) * 18 }}
        >
          <div className="inline">
            <span className="grow">{s.title}</span>
            <small>
              {s.word_count}/{s.target_words} 字
            </small>
            <button
              className="btn"
              disabled={i === 0}
              onClick={() => move(i, -1)}
            >
              ↑
            </button>
            <button
              className="btn"
              disabled={i === data.nodes.length - 1}
              onClick={() => move(i, 1)}
            >
              ↓
            </button>
            <button
              className="btn"
              onClick={() => setEditing(editing === s.id ? null : s.id)}
            >
              层级与依赖
            </button>
            <button
              className="btn"
              disabled={dirty}
              onClick={() => {
                if (
                  confirm(
                    "归档将从当前目录隐藏，正文和版本保留。若有子章节或依赖会阻止操作。",
                  )
                )
                  act(
                    () =>
                      send("/sections/" + s.id + "/archive", {
                        base_revision_id: s.current_revision_id,
                      }),
                    "章节已归档，正文保留",
                  );
              }}
            >
              归档
            </button>
          </div>
          {editing === s.id && (
            <div className="tree-settings">
              <label className="field">
                <span>父章节</span>
                <select
                  aria-label="父章节"
                  value={s.parent_id ?? ""}
                  onChange={(e) =>
                    update(s.id, "parent_id", e.target.value || null)
                  }
                >
                  <option value="">一级章节</option>
                  {data.nodes
                    .filter((n: Row) => n.id !== s.id)
                    .map((n: Row) => (
                      <option key={n.id} value={n.id}>
                        {n.title}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field">
                <span>目标字数</span>
                <input
                  aria-label="目标字数"
                  type="number"
                  value={s.target_words}
                  min={0}
                  onChange={(e) =>
                    update(s.id, "target_words", Number(e.target.value))
                  }
                />
              </label>
              <div className="field">
                <span>前置章节（可选多个，程序拒绝循环依赖）</span>
                {data.nodes
                  .filter((n: Row) => n.id !== s.id)
                  .map((n: Row) => (
                    <label key={n.id} className="checkbox">
                      <input
                        type="checkbox"
                        checked={safe(s.dependencies, []).includes(n.id)}
                        onChange={(e) => {
                          const values = safe(s.dependencies, []);
                          update(
                            s.id,
                            "dependencies",
                            e.target.checked
                              ? [...values, n.id]
                              : values.filter((x: string) => x !== n.id),
                          );
                        }}
                      />
                      {n.title}
                    </label>
                  ))}
              </div>
            </div>
          )}
        </div>
      ))}
      {data && (
        <div className="actions mt">
          <button
            className="btn primary"
            disabled={!dirty}
            onClick={async () => {
              const result = await act(
                () =>
                  send(
                    "/projects/" + pid + "/outline/tree",
                    { version: data.version, nodes: data.nodes },
                    "PUT",
                  ),
                "提纲层级和依赖已保存",
              );
              if (result) {
                setData(result);
                setDirty(false);
              }
            }}
          >
            保存提纲结构
          </button>
          {dirty && (
            <button
              className="btn"
              onClick={async () => {
                setData(await api("/projects/" + pid + "/outline/tree"));
                setDirty(false);
              }}
            >
              放弃未保存调整
            </button>
          )}
        </div>
      )}
      {data?.archives?.length > 0 && (
        <section className="mt">
          <h4>已归档章节</h4>
          {data.archives.map((s: Row) => (
            <div key={s.id} className="inline mb">
              <span className="grow">{s.title}</span>
              <button
                className="btn"
                onClick={() =>
                  act(
                    () => send("/sections/" + s.id + "/unarchive"),
                    "章节与正文已恢复",
                  )
                }
              >
                恢复章节
              </button>
            </div>
          ))}
        </section>
      )}
    </details>
  );
}

export function FormatPlans({ pid, tick, act }: Props) {
  const [preview] = useData("/projects/" + pid + "/format/rules", tick);
  const [profiles] = useData("/projects/" + pid + "/format-profiles", tick);
  return (
    <section className="panel mb">
      <div className="section-heading">
        <h2>规则与排版联动</h2>
        <span>只有生效强制规则参与配置</span>
      </div>
      {preview?.sources?.length ? (
        <>
          <div className="format-diff">
            {preview.sources.map((r: Row) => (
              <div key={r.rule_id}>
                <span>{r.key}</span>
                <span>
                  {String(preview.current[r.key])} →{" "}
                  <strong>{String(r.expected)}</strong>
                </span>
              </div>
            ))}
          </div>
          {preview.conflicts.length > 0 ? (
            <div className="alert">
              规则冲突：{preview.conflicts.join("、")}
            </div>
          ) : (
            <button
              className="btn primary mt"
              onClick={() =>
                act(
                  () =>
                    send("/projects/" + pid + "/format/apply-rules", {
                      package_id: preview.package_id,
                    }),
                  "已按生效规则更新排版参数",
                )
              }
            >
              应用这些排版要求
            </button>
          )}
        </>
      ) : (
        <p className="muted">
          尚无可执行排版条款。可在论文规则中设置“排版参数”检查器，核对后重新发布规则包。
        </p>
      )}
      <div className="section-heading mt">
        <h3>保存与切换排版方案</h3>
        <button
          className="btn"
          onClick={() => {
            const name = prompt("为当前格式与DOCX模板保存一个方案名称");
            if (name)
              act(
                () => send("/projects/" + pid + "/format-profiles", { name }),
                "排版方案已保存",
              );
          }}
        >
          保存当前方案
        </button>
      </div>
      {profiles?.map((p: Row) => (
        <div key={p.id} className="inline mb">
          <strong className="grow">{p.name}</strong>
          <small>
            {p.verified ? "有人工作为样本验收记录" : "尚未人工验收"}
          </small>
          <button
            className="btn"
            onClick={() =>
              act(
                () =>
                  send("/format-profiles/" + p.id + "/activate", {
                    project_id: pid,
                  }),
                "排版方案已启用",
              )
            }
          >
            启用
          </button>
          <button
            className="btn"
            onClick={() => {
              const note = prompt("记录实际验收软件、样本和版式检查结果");
              if (note)
                act(
                  () => send("/format-profiles/" + p.id + "/verify", { note }),
                  "人工验收依据已记录",
                );
            }}
          >
            记录人工验收
          </button>
        </div>
      ))}
    </section>
  );
}

export function ManualRuleReview({ pid, tick, act }: Props) {
  const [findings] = useData("/projects/" + pid + "/findings", tick);
  const manual = (findings ?? []).filter(
    (f: Row) => f.rule_id && f.method === "human",
  );
  const unique = manual.filter(
    (f: Row, i: number) =>
      manual.findIndex(
        (x: Row) => x.rule_id === f.rule_id && x.section_id === f.section_id,
      ) === i,
  );
  return unique.length ? (
    <details className="panel mb workflow-panel">
      <summary>
        <strong>人工规则核对与版本签署</strong>
        <span>记录依据；正文、资料或格式变化后自动失效</span>
      </summary>
      {unique.map((f: Row) => (
        <div key={f.id} className="workflow-list">
          <p>{f.message}</p>
          <div className="actions">
            {[
              ["pass", "确认符合"],
              ["fail", "确认不符合"],
              ["not_applicable", "不适用"],
            ].map(([result, label]) => (
              <button
                className="btn"
                key={result}
                onClick={() => {
                  const note = prompt("请填写核对依据或不适用原因（至少3字）");
                  if (note)
                    act(
                      () =>
                        send("/findings/" + f.id + "/review", { result, note }),
                      "已按当前版本记录人工核对并重新检查",
                    );
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </details>
  ) : null;
}

export function CandidateMerge({
  task,
  act,
  onClose,
}: {
  task: Row;
  act: Props["act"];
  onClose: () => void;
}) {
  const [result] = useData("/tasks/" + task.id + "/diff", 0);
  const [chosen, setChosen] = useState<string[]>([]);
  const [old, setOld] = useState<string[]>([]);
  const changes = (result?.changes ?? []).filter(
    (c: Row) => c.kind !== "equal",
  );
  return (
    <details className="panel mb workflow-panel">
      <summary>
        <strong>按原文位置接受修改</strong>
        <span>逐段对比；选择被替换原文和新段落，可保留其他内容</span>
      </summary>
      {changes.map((c: Row, i: number) => (
        <div className="merge-diff" key={i}>
          <div className="merge-old">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={
                  c.old_node_ids.length > 0 &&
                  c.old_node_ids.every((x: string) => old.includes(x))
                }
                disabled={!c.old_node_ids.length}
                onChange={(e) =>
                  setOld((v) =>
                    e.target.checked
                      ? Array.from(new Set([...v, ...c.old_node_ids]))
                      : v.filter((x) => !c.old_node_ids.includes(x)),
                  )
                }
              />
              替换这组原文
            </label>
            <pre>{c.old_text || "（新增位置）"}</pre>
          </div>
          <div className="merge-new">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={
                  c.new_node_ids.length > 0 &&
                  c.new_node_ids.every((x: string) => chosen.includes(x))
                }
                disabled={!c.new_node_ids.length}
                onChange={(e) =>
                  setChosen((v) =>
                    e.target.checked
                      ? Array.from(new Set([...v, ...c.new_node_ids]))
                      : v.filter((x) => !c.new_node_ids.includes(x)),
                  )
                }
              />
              采用这组候选
            </label>
            <pre>{c.new_text || "（删除建议）"}</pre>
          </div>
        </div>
      ))}
      <button
        className="btn primary"
        disabled={!old.length || !chosen.length}
        onClick={async () => {
          const d = await act(
            () =>
              send("/sections/" + task.result.section_id + "/accept", {
                task_id: task.id,
                base_revision_id: task.result.base_revision_id,
                node_ids: chosen,
                source_node_ids: old,
                mode: "replace_selected",
              }),
            "已在原位置替换所选段落",
          );
          if (d) onClose();
        }}
      >
        仅替换所选连续原文
      </button>
    </details>
  );
}
export function PageSourceTools({
  pageId,
  tick,
  act,
}: {
  pageId: string;
  tick: number;
  act: Props["act"];
}) {
  const [data] = useData("/pages/" + pageId, tick);
  const [capabilities] = useData("/ocr/local/capabilities", 0);
  return (
    <section className="panel mb">
      <div className="section-heading">
        <h3>本地识别与资料单元分类</h3>
        <button
          className="btn"
          disabled={!capabilities?.available}
          onClick={() =>
            act(
              () => send("/pages/" + pageId + "/ocr-local", {}),
              "本地OCR已排队，不发送资料到网络；识别后仍需核对",
            )
          }
        >
          本地 OCR（不计 API 费用）
        </button>
      </div>
      {!capabilities?.available && (
        <small>{capabilities?.reason ?? "正在检测Windows OCR语言包"}</small>
      )}
      <details>
        <summary>逐个资料单元区分规则、研究证据、正例与反例</summary>
        {data?.sources?.map((s: Row) => (
          <div className="source-annotation" key={s.id}>
            <select
              aria-label="资料单元用途"
              value={s.kind}
              onChange={(e) =>
                act(
                  () =>
                    send(
                      "/source-units/" + s.id + "/usage",
                      { kind: e.target.value },
                      "PATCH",
                    ),
                  "用途已更新，后续检索按此分类",
                )
              }
            >
              <option value="rule_candidate">规则原文</option>
              <option value="evidence">研究证据</option>
              <option value="example">正确示例</option>
              <option value="counterexample">错误示例</option>
              <option value="commentary">说明与点评</option>
            </select>
            <small>
              {s.method === "windows_ocr"
                ? "本地OCR候选"
                : s.method === "manual"
                  ? "人工转录"
                  : "原文提取"}
            </small>
            <pre>{s.text}</pre>
          </div>
        ))}
      </details>
    </section>
  );
}
export function MaterialAutomation({ pid, tick, act }: Props) {
  const [files] = useData("/projects/" + pid + "/files", tick);
  const [capabilities] = useData("/ocr/local/capabilities", 0);
  const [fileId, setFileId] = useState("");
  return (
    <details className="panel mb workflow-panel">
      <summary>
        <strong>批量图片识别</strong>
        <span>使用本机Windows OCR，保留页码；不会自动确认规则</span>
      </summary>
      <p className="muted mb">
        {capabilities?.available
          ? "可用语言：" +
            capabilities.languages.map((x: Row) => x.name).join("、")
          : (capabilities?.reason ?? "检测本地OCR环境…")}
      </p>
      <div className="inline">
        <select
          aria-label="选择本地OCR文件"
          value={fileId}
          onChange={(e) => setFileId(e.target.value)}
        >
          <option value="">选择需要处理图片的资料</option>
          {files
            ?.filter(
              (f: Row) =>
                f.status === "ready" && [".pptx", ".pdf"].includes(f.extension),
            )
            .map((f: Row) => (
              <option value={f.id} key={f.id}>
                {f.name}
              </option>
            ))}
        </select>
        <button
          className="btn primary"
          disabled={!fileId || !capabilities?.available}
          onClick={() =>
            act(
              () => send("/files/" + fileId + "/ocr-local", {}),
              "本地批量识别已排队，结果保留为待核对候选",
            )
          }
        >
          处理未核对图片页
        </button>
      </div>
    </details>
  );
}
export function AIRuleExtraction({ pid, tick, act, refresh }: Props) {
  const [files] = useData("/projects/" + pid + "/files", tick);
  const [fileId, setFileId] = useState("__all__");
  const [limit, setLimit] = useState(40);
  const [offset, setOffset] = useState(0);
  const [automatic, setAutomatic] = useState(true);
  const [budgetLimit, setBudgetLimit] = useState(2);
  const [allowGpt, setAllowGpt] = useState(false);
  const [gptBudget, setGptBudget] = useState(0.4);
  const [plan, setPlan] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobs, setJobs] = useState<Row[]>([]);
  const [jobTick, setJobTick] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const budgetProject = useRef("");
  useEffect(() => {
    if (plan?.budget && budgetProject.current !== pid) {
      budgetProject.current = pid;
      setBudgetLimit(Math.max(0, Math.floor(plan.budget.available * 100) / 100));
    }
  }, [pid, plan]);
  const lastProgress = useRef("");
  useEffect(() => {
    const signature = jobs.map((t) => t.id + ":" + t.status + ":" + (safe(t.result).completed_batches ?? "")).join("|");
    if (lastProgress.current && signature !== lastProgress.current) refresh?.();
    lastProgress.current = signature;
  }, [jobs, refresh]);
  useEffect(() => {
    let alive = true;
    api("/tasks?project_id=" + pid)
      .then((d: Row[]) => {
        if (alive)
          setJobs(
            d
              .filter((t) => ["extract_rules", "rules_ai"].includes(t.kind))
              .slice(0, 30),
          );
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [pid, tick, jobTick]);
  const running = jobs.some((t) => ["queued", "running"].includes(t.status));
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setJobTick((n) => n + 1), 2000);
    return () => clearInterval(id);
  }, [running]);
  useEffect(() => {
    setOffset(0);
  }, [pid, fileId]);
  useEffect(() => {
    setPlan(null);
    setError("");
    if (!fileId) return;
    let alive = true;
    api(
      "/projects/" +
        pid +
        "/rules/ai-sources?file_id=" +
        encodeURIComponent(fileId === "__all__" ? "" : fileId),
    )
      .then((d) => {
        if (alive) setPlan(d);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [pid, fileId, tick]);
  const currentFile = files?.find((f: Row) => f.id === fileId);
  const canExtract =
    !!fileId &&
    !!plan?.total &&
    !plan?.local_only &&
    (automatic || (Number.isInteger(limit) &&
    limit >= 1 &&
    limit <= 120 &&
    Number.isInteger(offset) &&
    offset >= 0 &&
    offset < plan.total)) &&
    Number.isFinite(budgetLimit) && budgetLimit >= 0 &&
    (!automatic || !allowGpt || (Number.isFinite(gptBudget) && gptBudget >= 0 && gptBudget <= budgetLimit)) &&
    !busy &&
    !running;
  const extract = async (start = offset) => {
    if (!canExtract) return;
    setBusy(true);
    setError("");
    try {
      await act(async () => {
        try {
          const d = await send("/projects/" + pid + "/rules/ai-extract", {
            file_ids: fileId === "__all__" ? [] : [fileId],
            source_limit: limit,
            source_offset: start,
            automatic,
            budget_limit: budgetLimit,
            allow_gpt: allowGpt,
            gpt_budget: allowGpt ? gptBudget : 0,
            auto_publish: automatic,
          });
          setJobTick((n) => n + 1);
          return d;
        } catch (e) {
          setError((e as Error).message);
          throw e;
        }
      }, "自动整理已排队；系统会续接完整范围、校验依据并整理可用规则");
    } finally {
      setBusy(false);
    }
  };
  const completed = jobs.find(
    (t) =>
      t.kind === "rules_ai" &&
      t.status === "succeeded" &&
      !safe(t.payload).review_existing_only &&
      (!automatic || safe(t.payload).automatic) &&
      (fileId === "__all__" ? !safe(t.payload).file_ids?.length : safe(t.payload).file_ids?.includes(fileId)),
  );
  const result = completed ? safe(completed.result) : null;
  const scopedJobs = jobs.filter((t) => t.kind !== "rules_ai" || !fileId || fileId === "__all__" || !safe(t.payload).file_ids?.length || safe(t.payload).file_ids.includes(fileId));
  const activeJobs = scopedJobs.filter((t) => ["queued", "running"].includes(t.status));
  const currentJobs = activeJobs.length ? activeJobs : scopedJobs.slice(0, 1);
  const historyJobs = scopedJobs.filter((t) => !currentJobs.some((j) => j.id === t.id));
  const visibleJobs = showHistory ? [...currentJobs, ...historyJobs] : currentJobs;
  const retryJob = async (t: Row, profile?: string) => {
    if (busy) return;
    const retryInvalid=(safe(t.result).review?.unreviewed_count??0)>0;
    if(retryInvalid&&!confirm("只补齐没有有效结论的条款，已成功审核的结果将复用；补齐会新增计费。确认继续？"))return;
    if (!safe(t.payload).automatic && ["outcome_unknown", "incomplete"].includes(t.status) && !confirm("此前远端可能已经执行，请先核实账单。确认继续未完成批次，并知悉可能再次计费？")) return;
    setBusy(true); setError("");
    try {
      await act(() => send("/tasks/" + t.id + "/retry", {acknowledge_possible_duplicate_charge:true,...(retryInvalid?{retry_invalid:true}:{}),...(profile ? {provider_profile:profile} : {})}), "已续接规则整理；已保存的批次会复用");
      setJobTick((n) => n + 1);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const statusLabels: Row = {
    queued: "排队中",
    running: "整理中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    budget_blocked: "预算不足",
    outcome_unknown: "调用结果待核实",
    incomplete: "未完成",
  };
  return (
    <details className="panel mb workflow-panel rule-extraction-panel" open>
      <summary>
        <strong>AI 结构化整理规则候选</strong>
        <span>自动分批、断点续跑、机器审核 · 按预算执行</span>
      </summary>
      <p className="muted mb">
        上方“从课件整理候选”使用本地关键词整理，不调用模型。AI
        整理会保留原文依据，自动分类、校验和归并。通过机器审核的要求可进入规则包；实际冲突按组汇总。
      </p>
      <div className="rule-extraction-fields">
        <label className="field">
          <span>规则材料</span>
          <select
            value={fileId}
            onChange={(e) => setFileId(e.target.value)}
            aria-label="选择AI整理的规则文件"
          >
            <option value="">选择一份规则材料</option>
            <option value="__all__">全部规则材料</option>
            {files
              ?.filter((f: Row) => f.role === "rules" && f.status === "ready")
              .map((f: Row) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                  {f.egress === "local_only" ? "（仅限本地）" : ""}
                </option>
              ))}
          </select>
        </label>
        <label className="field">
          <span>处理方式</span>
          <select aria-label="规则整理处理方式" value={automatic ? "automatic" : "manual"} onChange={e => setAutomatic(e.target.value === "automatic")}>
            <option value="automatic">整份材料自动整理</option>
            <option value="manual">指定片段范围（高级）</option>
          </select>
        </label>
        {automatic && <label className="field"><span>本次总预算（USD）</span><input type="number" min={0} step={0.1} aria-label="规则整理总预算" value={budgetLimit} onChange={e => setBudgetLimit(Number(e.target.value))}/></label>}
        {!automatic && <><label className="field">
          <span>本次来源片段上限</span>
          <input
            type="number"
            min={1}
            max={120}
            aria-label="本次来源片段上限"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>从第几个片段开始</span>
          <input
            type="number"
            min={1}
            max={plan?.total || 1}
            aria-label="起始来源片段"
            value={offset + 1}
            onChange={(e) => setOffset(Number(e.target.value) - 1)}
          />
        </label></>}
        <button
          className="btn primary"
          disabled={!canExtract}
          onClick={() => extract()}
        >
          {busy ? "正在提交…" : automatic ? "自动整理全部材料" : "整理候选（计费）"}
        </button>
      </div>
      {automatic && <div className="actions mb"><label className="check"><input type="checkbox" checked={allowGpt} onChange={e => setAllowGpt(e.target.checked)}/>允许在子预算内使用 GPT 复核疑难片段</label>{allowGpt && <label>GPT 子预算（USD）<input aria-label="GPT疑难复核子预算" type="number" min={0} max={budgetLimit} step={0.1} value={gptBudget} onChange={e=>setGptBudget(Number(e.target.value))}/></label>}</div>}
      {plan?.budget && <p className="muted mb">项目预算 {plan.budget.limit.toFixed(2)} USD · 已核对 {plan.budget.reconciled_amount.toFixed(2)} · 用量估算 {plan.budget.estimated_cost.toFixed(2)} · 已完成待对账占用 {plan.budget.pending_bill_reserved.toFixed(2)} · 执行中或结果未知占用 {plan.budget.unresolved_reserved.toFixed(2)} · 当前可用 {plan.budget.available.toFixed(2)}</p>}
      {plan && (
        <p className="muted mb">
          共 {plan.total} 个可整理来源片段；{automatic ? "系统将自动推进完整范围，无需选择下一批。" : <>本批为第{" "}
          {Math.min(offset + 1, plan.total)} 至{" "}
          {Math.min(offset + limit, plan.total)} 个。已排除的材料页不参与 AI
          整理。</>}
        </p>
      )}
      {currentFile?.egress === "local_only" && (
        <p className="alert">此材料仅限本地，请使用上方的本地候选整理。</p>
      )}
      {files &&
        !files.some((f: Row) => f.role === "rules" && f.status === "ready") && (
          <p className="muted">
            暂无已完成解析的规则材料。请在资料中心将相关文件设为规则来源。
          </p>
        )}
      {result && (
        <div className="rule-batch-result">
          <span>
            上次处理 {result.processed_source_units} 个片段，本轮累计新增 {result.added}{" "}
            条候选。{result.reused_batches ? "本次续接新增 " + result.added_this_attempt + " 条，其余来自已保存批次。" : ""}
          </span>
          {result.next_offset != null && result.next_offset < plan?.total ? (
            <button
              className="btn"
              disabled={busy || running}
              onClick={() => setOffset(result.next_offset)}
            >
              选择下一批（第 {result.next_offset + 1} 个起）
            </button>
          ) : (
            <small>该次整理已到达材料末尾。</small>
          )}
        </div>
      )}
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {jobs.length > 0 && (
        <div className="rule-jobs" aria-label="规则整理任务">
          {visibleJobs.map((t) => {
            const result = safe(t.result);
            return (
              <div key={t.id}>
                <div className="grow">
                  <strong>
                    {safe(t.payload).review_existing_only ? "疑难规则自动复核" : t.kind === "rules_ai" ? "AI 规则整理" : "本地候选整理"} ·{" "}
                    {statusLabels[t.status] ?? t.status}
                  </strong>
                  <small>{new Date(t.created_at).toLocaleString("zh-CN")}{result.models_used?.length ? " · 结果模型（含复用批次）：" + result.models_used.join("、") : ""}{historyJobs.some((j) => j.id === t.id) ? " · 历史任务" : ""}</small>
                  <p>
                    {["queued", "running"].includes(t.status)
                      ? (t.phase || "等待任务执行") + " · " + t.progress + "%"
                      : t.status === "succeeded"
                        ? result.review_only ? `${result.review?.coverage_complete===undefined?"旧版返回":"有效审核结论"} ${result.review?.semantic_checked ?? 0}/${result.review?.semantic_total ?? 0} 条${t.review_receipt?.integrity_findings?"；部分排除结论未通过事后证据校验":"，结果已保存"}。` : (t.kind === "rules_ai" ? "本轮累计新增 " : "新增 ") +
                          (result.added ?? 0) +
                          " 条规则；" + (result.processed_source_units ?? "") + (result.automatic ? " 个片段已自动处理。" : result.processed_source_units != null ? " 个片段已处理。" : "")
                        : t.error || (result.automatic ? "可用规则已保存，未完成范围已登记，可续接；结果未知的请求不会自动重发。" : "任务已结束")}
                  </p>
                  {result.total_batches != null && <p className="muted">已保存 {result.completed_batches ?? 0}/{result.total_batches} 批 · 已处理 {result.processed_source_units ?? 0}/{result.selected_source_units ?? ""} 个片段{result.reused_batches ? " · 复用 " + result.reused_batches + " 个已完成批次" : ""}</p>}
                  {result.review && <p className="muted">{result.review.accepted_count!==undefined ? `本次通过 ${result.review.accepted_count} 条 · 参考 ${result.review.reference_count} 条 · 不确定 ${result.review.uncertain_count} 条 · 未审核 ${result.review.unreviewed_count} 条` : `全项目机器审核通过 ${result.review.machine_verified??0} 条（非本次新增通过数）`} · 复用判定 {result.review.reused_decisions??0} 条</p>}
                  {result.review?.unreviewed_count > 0 && <p className="alert">仍有 {result.review.unreviewed_count} 条没有有效审核结论，未计为完成，原候选保留。</p>}
                  {(result.review?.recovery_requests??result.recovery_requests??0)>0&&<p className="muted">已使用主模型缩小批次补齐 {result.review?.recovery_requests??result.recovery_requests} 次，完成结果逐批保存。</p>}
                  {(result.review?.pause_reason||result.pause_reason)&&<p className="alert">{result.review?.pause_reason||result.pause_reason}</p>}
                  {result.review?.verification_errors?.length>0&&!result.review?.pause_reason&&<p className="alert">审核未完成的原因：{[...new Set<string>(result.review.verification_errors)].slice(0,3).join("；")}</p>}
                  {t.review_receipt?.integrity_findings>0&&<p className="alert">此旧版任务有 {t.review_receipt.integrity_findings} 条排除结论未通过证据回放校验；已保留原候选待复核。</p>}
                  {t.review_receipt && <div className="rule-review-receipt"><p>{t.review_receipt.calls ? "本次请求模型："+t.review_receipt.models.map((m:Row)=>`${m.model}（${m.calls}次请求）`).join("、") : "本次没有新增模型请求，使用本地或历史结果。"}</p>{t.review_receipt.calls>0 && <p>输入 Token {t.review_receipt.input_tokens} · 输出 {t.review_receipt.output_tokens} · 缓存输入 {t.review_receipt.cached_input_tokens}{!t.review_receipt.usage_complete ? " · 部分用量未返回" : ""}</p>}<p>{t.review_receipt.fee_state==="reconciled" ? `已核对${t.review_receipt.fee_scope==="task_chain"?"任务链":"本次"}账单增量 ${t.review_receipt.confirmed_amount.toFixed(6)} ${t.review_receipt.currency??""}` : t.review_receipt.fee_state==="no_calls" ? "本次未新增模型费用。" : "费用尚不能逐任务归属；账户总额及预留不代表本次审核费用。"}</p></div>}
                  {["queued", "running"].includes(t.status) && (
                    <progress max={100} value={t.progress} />
                  )}
                </div>
                {["queued", "running"].includes(t.status) && (
                  <button
                    className="btn"
                    onClick={async () => {
                      await act(
                        () => send("/tasks/" + t.id + "/cancel", {}),
                        "已请求取消规则整理",
                      );
                      setJobTick((n) => n + 1);
                    }}
                  >
                    取消任务
                  </button>
                )}
                {t.kind === "rules_ai" && ["failed","cancelled","budget_blocked","incomplete","outcome_unknown"].includes(t.status) && (
                  <div className="actions"><button className="btn" disabled={busy || running} onClick={() => retryJob(t)}>{(result.review?.unreviewed_count??0)>0 ? "补齐未完成审核（计费）" : safe(t.payload).automatic ? "继续自动整理" : "继续未完成批次（计费）"}</button>{!safe(t.payload).automatic && <button className="btn" disabled={busy || running} onClick={() => retryJob(t,"gpt")}>改用 GPT 继续（计费）</button>}</div>
                )}
              </div>
            );
          })}
          {historyJobs.length > 0 && <button className="btn" aria-expanded={showHistory} onClick={() => setShowHistory((value) => !value)}>{showHistory ? "收起历史整理记录" : "查看历史整理记录（" + historyJobs.length + "）"}</button>}
        </div>
      )}
    </details>
  );
}

export function ReadinessPanel({ pid, tick }: Props) {
  const [data] = useData("/projects/" + pid + "/readiness", tick);
  return data ? (
    <section className="panel mb">
      <div className="section-heading">
        <h2>项目准备与交付缺口</h2>
        <span>按实际记录展示，不以测试通过代替论文验收</span>
      </div>
      <div className="summary-strip">
        <span>
          规则待核对页 <strong>{data.counts.pending_rule_pages}</strong>
        </span>
        <span>
          已有正文{" "}
          <strong>
            {data.counts.drafted_sections}/{data.counts.sections}
          </strong>
        </span>
        <span>
          人工审阅 <strong>{data.counts.reviewed_sections}</strong>
        </span>
      </div>
      {data.blockers.map((b: Row) => (
        <p className="readiness-item" key={b.kind}>
          • {b.message}
        </p>
      ))}
      {!data.blockers.length && (
        <p className="muted">
          已记录的准备项已齐备；请继续核对研究与实际版面。
        </p>
      )}
      <small>{data.message}</small>
    </section>
  ) : null;
}
export function ImpactPanel({ pid, tick }: Props) {
  const [data] = useData("/projects/" + pid + "/impacts", tick);
  return data?.length ? (
    <details className="panel mb workflow-panel">
      <summary>
        <strong>修改影响与待复查章节</strong>
        <span>{data.length}个章节</span>
      </summary>
      {data.map((s: Row) => (
        <div className="tree-item" key={s.section_id}>
          <h4>{s.title}</h4>
          <p className="muted">{s.reason}</p>
          <small>
            关联上游对象 {s.dependencies.length}{" "}
            项；复查后在写作工作台记录审阅。
          </small>
        </div>
      ))}
    </details>
  ) : null;
}
export { default as RetrievalEvaluation } from "./RetrievalEvaluation";

import RuleDuplicates from "./RuleDuplicates";
import AutomaticRuleReview from "./AutomaticRuleReview";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  FileText,
  LoaderCircle,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { api, send } from "./client";
import { AIRuleExtraction, RuleExecutionPanel } from "./Completion";
import RuleEditor, {
  checkerLabels,
  scopeLabels,
  stateLabels,
} from "./RuleEditor";
type Row = Record<string, any>;
type Props = {
  pid: string;
  tick: number;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
  refresh: () => void;
  onMaterials: () => void;
  onTasks: () => void;
  renderSource: (file: Row, onClose: () => void) => ReactNode;
};
export default function RulesWorkspace({
  pid,
  tick,
  act,
  refresh,
  onMaterials,
  onTasks,
  renderSource,
}: Props) {
  const [data, setData] = useState<Row | null>(null),
    [loadError, setLoadError] = useState("");
  const [editing, setEditing] = useState<Row | null>(null),
    [source, setSource] = useState<Row | null>(null),
    [original, setOriginal] = useState<Row | null>(null),
    [sourceBusy, setSourceBusy] = useState("");
  const [filter, setFilter] = useState("all"),
    [query, setQuery] = useState(""),
    [fileFilter, setFileFilter] = useState(""),
    [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState("");
  const actionLock = useRef(false);
  const sourceVersion = useRef(0);
  const requestVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    try {
      const d = await api("/projects/" + pid + "/rules");
      if (version === requestVersion.current) {
        setData(d);
        setLoadError("");
      }
    } catch (e) {
      if (version === requestVersion.current)
        setLoadError((e as Error).message);
    }
  }, [pid]);
  useEffect(() => {
    setData(null);
    setEditing(null);
    setSource(null);
    setOriginal(null);
    setSelected([]);
    setFileFilter("");
    setQuery("");
    setFilter("all");
    return () => {
      requestVersion.current++;
      sourceVersion.current++;
    };
  }, [pid]);
  useEffect(() => {
    load();
  }, [load, tick]);
  const run = async (key: string, fn: () => Promise<any>, message: string) => {
    if (actionLock.current) return null;
    actionLock.current = true;
    setBusy(key);
    try {
      const result = await act(fn, message);
      if (result) await load();
      return result;
    } finally {
      actionLock.current = false;
      setBusy("");
    }
  };
  const openSource = async (r: Row) => {
    const version = ++sourceVersion.current;
    setSourceBusy(r.id);
    try {
      const d = await act(() => api("/rules/" + r.id + "/source"), "");
      if (d && version === sourceVersion.current) setSource({ ...d, rule: r });
    } finally {
      if (version === sourceVersion.current) setSourceBusy("");
    }
  };
  const all: Row[] = data?.rules ?? [];
  const ready = data?.readiness;
  const current = all.filter(
    (r) =>
      (filter === "all" || r.state === filter) &&
      (fileFilter === "" ||
        (fileFilter === "manual" && !r.source_id) ||
        r.source_file_id === fileFilter) &&
      (!query.trim() ||
        [r.statement, r.source_file, r.decision_note, checkerLabels[r.checker]]
          .join(" ")
          .toLowerCase()
          .includes(query.trim().toLowerCase())),
  );
  const visibleIds = current.map((r) => r.id);
  const chosen = selected.filter((id) => visibleIds.includes(id));
  const batch = async (state: string) => {
    await run(
      "batch",
      () =>
        send("/projects/" + pid + "/rules/batch-review", {
          rule_ids: chosen,
          state,
        }),
      state === "rejected"
        ? "所选规则已排除，发布后更新生效内容"
        : "所选规则已恢复待确认",
    );
    setSelected([]);
  };
  const files = Array.from(
    new Map(
      all
        .filter((r) => r.source_file_id)
        .map((r) => [r.source_file_id, r.source_file]),
    ).entries(),
  );
  return (
    <>
      <div className="page-title">
        <div>
          <div className="eyebrow">PAPER WORKBENCH</div>
          <h1>把要求，变成可执行的规则。</h1>
          <p>自动整理材料、校验依据并归并条款，按适用范围生成论文规则包。</p>
        </div>
        <div className="actions">
          <button
            className="btn"
            disabled={!!busy}
            onClick={() =>
              run(
                "extract",
                () => send("/projects/" + pid + "/rules/extract", {}),
                "本地候选整理已排队；下方显示处理结果",
              )
            }
          >
            <Sparkles size={15} />
            {busy === "extract" ? "正在提交…" : "从课件整理候选"}
          </button>
          <button className="btn" onClick={() => setEditing({})}>
            <Plus size={15} />
            手工添加
          </button>
          <button
            className="btn primary"
            disabled={!!busy || !data}
            onClick={() => {
              document
                .getElementById("rule-publication")
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
              if (ready?.can_publish)
                run(
                  "publish",
                  () => send("/projects/" + pid + "/rule-packages", {}),
                  "规则包已发布，后续写作自动加载",
                );
            }}
          >
            <ShieldCheck size={16} />
            {busy === "publish" ? "正在发布…" : "发布规则包"}
          </button>
        </div>
      </div>
      {loadError && (
        <div className="alert" role="alert">
          {loadError}
          <button className="btn" onClick={load}>
            重新加载规则
          </button>
        </div>
      )}
      {!data && !loadError && (
        <p className="rule-loading" role="status">
          <LoaderCircle size={18} />
          正在加载规则与发布条件…
        </p>
      )}
      {ready && (
        <section
          className="panel mb rule-publication"
          id="rule-publication"
          aria-label="发布前检查"
        >
          <div className="section-heading">
            <h2>发布前检查</h2>
            <span>
              {ready.can_publish
                ? "准备就绪，可以发布"
                : ready.blockers.length + "项待处理"}
            </span>
          </div>
          <div className="rule-counters">
            <span>
              待确认 <strong>{ready.counts.candidate}</strong>
            </span>
            <span>
              已确认 <strong>{ready.counts.confirmed}</strong>
            </span>
            <span>机器审核通过 <strong>{ready.counts.machine_verified ?? 0}</strong></span>
            <span>参考与条件规范 <strong>{ready.counts.reference_only ?? 0}</strong></span>
            <span>
              已排除 <strong>{ready.counts.rejected}</strong>
            </span>
            <span>
              本次可发布 <strong>{ready.publishable_count}</strong>
            </span>
            <span>
              待核对资料页 <strong>{ready.pending_pages.length}</strong>
            </span>
          </div>
          <div className="rule-package-status">
            <ShieldCheck size={23} />
            <div>
              <strong>
                {data?.package
                  ? "当前生效：规则包 V" + data.package.version
                  : "还没有生效规则包"}
              </strong>
              <p>
                {data?.package
                  ? ready.dirty
                    ? "有尚未发布的修改；当前写作仍加载已发布版本。"
                    : "当前已确认的要求与生效版本一致，共 " +
                      ready.package_rule_count +
                      " 条。"
                  : "核对资料页、确认适用要求后即可发布。"}
              </p>
            </div>
            {data?.package && (
              <button
                className="btn"
                onClick={() => setSource({ package: data.package })}
              >
                查看生效快照
              </button>
            )}
          </div>
          {ready.blockers.map((b: Row) => (
            <div className="rule-blocker" key={b.code}>
              <AlertTriangle size={16} />
              <span>{b.message}</span>
              {["RULE_CONFLICT", "RULE_AUTOMATIC_CONFLICTS", "RULE_DUPLICATES"].includes(b.code) ? (
                <button
                  className="text-button"
                  onClick={() =>
                    document
                      .getElementById(
                        b.code === "RULE_DUPLICATES"
                          ? "rule-duplicates"
                          : "rule-conflicts",
                      )
                      ?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  处理冲突
                </button>
              ) : b.code === "RULE_CONFIRMATION_REQUIRED" ? (
                <button
                  className="text-button"
                  onClick={() => setFilter("candidate")}
                >
                  查看待确认
                </button>
              ) : b.code === "RULE_SOURCE_INACTIVE" ? (
                <button
                  className="text-button"
                  onClick={() => {
                    const r = all.find((r) => b.rule_ids.includes(r.id));
                    if (r) setEditing(r);
                  }}
                >
                  复核条款
                </button>
              ) : (
                <button className="text-button" onClick={onMaterials}>
                  核对资料
                </button>
              )}
            </div>
          ))}
          {ready.pending_pages.length > 0 && (
            <details className="rule-advanced">
              <summary>查看待核对页面（{ready.pending_pages.length}）</summary>
              <div className="rule-pending-pages">
                {ready.pending_pages.map((p: Row) => (
                  <button
                    key={p.id}
                    className="text-button"
                    onClick={() =>
                      setOriginal({
                        id: p.file_id,
                        name: p.name,
                        project_id: pid,
                        extension: p.extension,
                        role: p.role,
                        initialPage: p.page_number,
                      })
                    }
                  >
                    {p.name} · 第{p.page_number}页<ArrowUpRight size={13} />
                  </button>
                ))}
              </div>
            </details>
          )}
          {ready.can_publish && (
            <p className="rule-ready">
              <CheckCircle2 size={16} />
              发布条件已满足。待确认条款及正反例不进入规则包。
            </p>
          )}
        </section>
      )}
      <AIRuleExtraction pid={pid} tick={tick} act={act} refresh={refresh} />
      <AutomaticRuleReview pid={pid} data={data} busy={!!busy} run={run}/>
      <RuleExecutionPanel pid={pid} tick={tick} act={act} />
      {data && (
        <RuleDuplicates
          pid={pid}
          data={data}
          busy={busy}
          run={run}
          onEdit={setEditing}
          onSource={openSource}
          onScan={load}
        />
      )}
      {!!data?.conflicts?.length && (
        <section
          className="panel mb rule-conflicts"
          id="rule-conflicts"
          aria-label="规则冲突"
        >
          <div className="section-heading">
            <h2>不同取值与冲突处理</h2>
            <span>{data.conflicts.length}组需核对 · 重复条款已折叠</span>
          </div>
          <p className="muted mb">
            对照出处，编辑正确取值或排除不适用条款；仅填写采用依据不会解除强制规则冲突。
          </p>
          {data.conflicts.map((group: Row) => (
            <div className="rule-conflict-group" key={group.key + group.scope}>
              <strong>
                {scopeLabels[group.scope] ?? group.scope} · {group.key}
              </strong>
              {group.rules.map((r: Row) => (
                <div className="rule-conflict-row" key={r.id}>
                  <div className="grow">
                    <p>{r.statement}</p>
                    {r.duplicate_count > 1 && (
                      <small>
                        相同条款 {r.duplicate_count}{" "}
                        条，已合并展示；可在上方先归并重复记录。
                      </small>
                    )}
                    <small>
                      {stateLabels[r.state]} · {stateLabels[r.modality]} · 参数{" "}
                      {r.value}
                    </small>
                  </div>
                  <button className="btn" disabled={!!busy} onClick={()=>run("resolve-conflict",()=>send("/projects/"+pid+"/rules/resolve-conflict",{rule_id:r.id}),"已采用所选条款并排除组内其他冲突要求")}>采用此条并解决整组</button>
                  <button
                    className="btn"
                    onClick={() =>
                      setEditing(all.find((v) => v.id === r.id) ?? r)
                    }
                  >
                    核对此条
                  </button>
                </div>
              ))}
            </div>
          ))}
        </section>
      )}
      <div className="rule-list-heading">
        <h2>
          规则条款 <small>{all.length} 条</small>
        </h2>
        <div className="rule-filters">
          <label className="rule-search">
            <Search size={16} />
            <input
              aria-label="搜索规则"
              placeholder="搜索条款、出处或采用依据"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected([]);
              }}
            />
          </label>
          <select
            aria-label="按规则出处筛选"
            value={fileFilter}
            onChange={(e) => {
              setFileFilter(e.target.value);
              setSelected([]);
            }}
          >
            <option value="">全部出处</option>
            <option value="manual">用户新增规则</option>
            {files.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="tabs rule-state-tabs" role="tablist" aria-label="规则状态">
        {[
          ["all", "全部规则"],
          ["candidate", "待确认"],
          ["confirmed", "已确认"],
          ["machine_verified", "机器审核通过"],
          ["reference_only", "参考与条件规范"],
          ["rejected", "已排除"],
        ].map(([key, label]) => (
          <button
            role="tab"
            aria-selected={filter === key}
            key={key}
            className={filter === key ? "active" : ""}
            onClick={() => {
              setFilter(key);
              setSelected([]);
            }}
          >
            {label}{" "}
            <small>
              {key === "all" ? all.length : (ready?.counts[key] ?? 0)}
            </small>
          </button>
        ))}
      </div>
      <div className="rule-batch-bar">
        <label className="checkbox">
          <input
            type="checkbox"
            aria-label="选择当前筛选规则"
            checked={!!current.length && chosen.length === current.length}
            disabled={!current.length}
            onChange={(e) => setSelected(e.target.checked ? visibleIds : [])}
          />
          选择当前筛选结果
        </label>
        <span>
          {chosen.length
            ? "已选择 " + chosen.length + " 条"
            : "显示 " + current.length + " 条"}
        </span>
        {chosen.length > 0 && (
          <>
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => batch("rejected")}
            >
              排除所选
            </button>
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => batch("candidate")}
            >
              恢复待确认
            </button>
          </>
        )}
      </div>
      <div className="panel no-padding rule-list">
        {current.map((r) => (
          <article className="rule-row" key={r.id}>
            <input
              type="checkbox"
              aria-label={"选择规则：" + r.statement}
              checked={selected.includes(r.id)}
              onChange={(e) =>
                setSelected((ids) =>
                  e.target.checked
                    ? [...ids, r.id]
                    : ids.filter((id) => id !== r.id),
                )
              }
            />
            <div className="grow">
              <div className="inline">
                <span
                  className={
                    "badge " + (r.modality === "mandatory" ? "success" : "")
                  }
                >
                  {stateLabels[r.modality] ?? r.modality}
                </span>
                <span className="badge">{stateLabels[r.state] ?? r.state}</span>
                <small>
                  {scopeLabels[r.scope] ?? r.scope} ·{" "}
                  {checkerLabels[r.checker] ?? r.checker}
                </small>
              </div>
              <p>{r.statement}</p>
              {r.source_id ? (
                <button
                  className="text-button rule-source-link"
                  disabled={sourceBusy === r.id}
                  onClick={() => openSource(r)}
                >
                  <FileText size={13} />
                  {sourceBusy === r.id
                    ? "正在读取原文…"
                    : (r.source_file ?? "来源已移除") +
                      " · 第" +
                      (r.page_number ?? "?") +
                      "页"}
                  <ArrowUpRight size={13} />
                </button>
              ) : (
                <small>用户新增规则</small>
              )}
              {r.merged_sources?.length > 0 && (
                <details className="rule-merged-sources">
                  <summary>已保留 {r.merged_sources.length} 条归并来源</summary>
                  {r.merged_sources.map((member: Row) => (
                    <div key={member.id}>
                      {member.source_id ? (
                        <button
                          className="text-button rule-source-link"
                          onClick={() => openSource(member)}
                        >
                          {member.source_file ?? "来源文件"} · 第
                          {member.page_number ?? "?"}页
                          <ArrowUpRight size={12} />
                        </button>
                      ) : (
                        <small>用户新增记录 · 原始条款已保留</small>
                      )}
                    </div>
                  ))}
                </details>
              )}
              {r.decision_note && (
                <p className="rule-decision-note">
                  采用 / 排除依据：{r.decision_note}
                </p>
              )}
              {r.state === "confirmed" &&
                ["pending", "example", "counterexample"].includes(
                  r.modality,
                ) && (
                  <small className="rule-warning">
                    此性质不进入规则包，请确认其用途。
                  </small>
                )}
            </div>
            <div className="rule-row-actions">
              <button className="btn" onClick={() => setEditing(r)}>
                核对与编辑
              </button>
              {r.state !== "rejected" && (
                <button
                  className="text-button"
                  disabled={!!busy}
                  onClick={() =>
                    run(
                      r.id,
                      () =>
                        send("/rules/" + r.id, { state: "rejected" }, "PATCH"),
                      "条款已排除，可在已排除列表恢复",
                    )
                  }
                >
                  排除
                </button>
              )}
              {r.state === "rejected" && (
                <button
                  className="text-button"
                  disabled={!!busy}
                  onClick={() =>
                    run(
                      r.id,
                      () =>
                        send("/rules/" + r.id, { state: "candidate" }, "PATCH"),
                      "条款已恢复为待确认",
                    )
                  }
                >
                  恢复待确认
                </button>
              )}
            </div>
          </article>
        ))}
        {!current.length && data && (
          <div className="empty">
            <ShieldCheck size={30} />
            <h3>{all.length ? "没有匹配的规则" : "从已有要求开始整理"}</h3>
            <p>
              {all.length
                ? "调整筛选条件，或搜索其他关键词。"
                : "导入规则材料后整理候选，也可以手工录入导师提出的要求。"}
            </p>
          </div>
        )}
      </div>
      {editing && (
        <RuleEditor
          key={editing.id ?? "new"}
          rule={editing}
          pid={pid}
          act={act}
          onSaved={() => {
            setSelected([]);
            load();
          }}
          onClose={() => setEditing(null)}
        />
      )}
      {source && (
        <div className="overlay">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label={source.package ? "生效规则快照" : "规则原文依据"}
          >
            <header>
              <h2>
                {source.package
                  ? "生效规则快照 V" + source.package.version
                  : "规则原文依据"}
              </h2>
              <button
                className="icon-button"
                aria-label="关闭原文依据"
                onClick={() => setSource(null)}
              >
                <X size={19} />
              </button>
            </header>
            <div className="modal-body">
              {source.package ? (
                <>
                  <p className="muted mb">
                    发布于{" "}
                    {new Date(source.package.created_at).toLocaleString()}
                    。以下条款为发布时的固定快照。
                  </p>
                  {JSON.parse(source.package.rules).map((r: Row) => (
                    <div className="rule-snapshot-row" key={r.id}>
                      <p>{r.statement}</p>
                      <small>
                        {stateLabels[r.modality]} ·{" "}
                        {scopeLabels[r.scope] ?? r.scope} ·{" "}
                        {checkerLabels[r.checker]}
                      </small>
                    </div>
                  ))}
                </>
              ) : (
                <>
                  <h3>{source.rule.statement}</h3>
                  {source.source ? (
                    <>
                      <p className="muted mb">
                        {source.file.name} · 第{source.page?.page_number ?? "?"}
                        页 ·{" "}
                        {source.page?.review_state === "reviewed"
                          ? "原文已核对"
                          : source.page?.review_state === "not_applicable"
                            ? "此页已排除"
                            : "原文待核对"}
                      </p>
                      <pre className="rule-source-text">
                        {source.source.text}
                      </pre>
                      <div className="actions">
                        <button
                          className="btn primary"
                          onClick={() => {
                            setOriginal({
                              ...source.file,
                              initialPage: source.page?.page_number ?? 1,
                            });
                            setSource(null);
                          }}
                        >
                          打开原页核对
                        </button>
                        <a
                          className="btn"
                          href={"/api/v1/files/" + source.file.id + "/download"}
                        >
                          下载原文件
                        </a>
                      </div>
                    </>
                  ) : (
                    <p>此条为用户手工新增，尚未关联材料来源。</p>
                  )}
                </>
              )}
            </div>
          </section>
        </div>
      )}
      {original &&
        renderSource(original, () => {
          setOriginal(null);
          refresh();
          load();
        })}
      <p className="rule-footer">
        <button className="text-button" onClick={onMaterials}>
          进入资料中心核对原件
          <ArrowUpRight size={14} />
        </button>
        <button className="text-button" onClick={onTasks}>
          查看全部任务
          <ArrowUpRight size={14} />
        </button>
      </p>
    </>
  );
}

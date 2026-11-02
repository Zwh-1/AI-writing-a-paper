import { useEffect, useState } from "react";
import { apiUrl, api, send } from "./client";
import { useSourceBillingGate } from './workspace/SourceWorkspace';
type Row = Record<string, any>;

export function MaterialSummarySettings({
  onSaved,
}: { onSaved?: () => void } = {}) {
  const [saved, setSaved] = useState<Row | null>(null);
  const [model, setModel] = useState("deepseek-chat");
  const [options, setOptions] = useState<Row>({
    max_request_cost: 2,
    max_output_tokens: 4096,
    thinking_enabled: false,
  });
  const [key, setKey] = useState("");
  const [models, setModels] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    api("/material-summary/settings")
      .then((d) => {
        if (active) {
          setSaved(d);
          setModel(d.provider.model);
          setOptions(d.provider);
        }
      })
      .catch((e) => {
        if (active) {
          setMessage(e.message);
          setError(true);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  const persist = async (clear = false) => {
    setBusy(true);
    setMessage("");
    setError(false);
    try {
      if (!clear && key && !key.trim())
        throw new Error("请输入完整的 API key，或留空保留已有密钥。");
      const d = await send(
        "/material-summary/settings",
        {
          provider: {
            model: model.trim(),
            max_request_cost: Number(options.max_request_cost),
            max_output_tokens: Number(options.max_output_tokens),
            thinking_enabled: options.thinking_enabled,
          },
          ...(clear
            ? { api_key: "" }
            : key.trim()
              ? { api_key: key.trim() }
              : {}),
        },
        "PUT",
      );
      setSaved(d);
      setModel(d.provider.model);
      setKey("");
      onSaved?.();
      setMessage(
        clear
          ? "DeepSeek 密钥已移除。"
          : d.credential.configured
            ? "DeepSeek 资料总结设置已保存。"
            : "模型设置已保存，请填写 x5m5x 的 API key。",
      );
    } catch (e) {
      setError(true);
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="panel summary-settings" data-testid="summary-settings">
      <summary>
        DeepSeek 资料总结设置{" "}
        <span className="muted">
          {saved?.credential.configured ? "密钥已配置" : "待填写 API key"}
        </span>
      </summary>
      <p>
        用于概括、分点和主题归纳。接口：
        {saved?.provider.base_url ?? "https://api.x5m5x.com"}。资料总结固定使用
        DeepSeek。
      </p>
      <div className="summary-settings-fields">
        <label>
          DeepSeek 模型 ID
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            list="material-deepseek-models"
            placeholder="deepseek-chat"
            disabled={busy}
          />
        </label>
        <datalist id="material-deepseek-models">
          {models.map((m) => (
            <option key={m.id} value={m.id} />
          ))}
        </datalist>
        <label>
          x5m5x API key
          <input
            type="password"
            autoComplete="new-password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={
              saved?.credential.configured
                ? "已保存 " + saved.credential.masked + "，留空保留"
                : "粘贴 x5m5x 的 API key"
            }
            disabled={busy}
          />
        </label>
      </div>
      <details className="summary-budget-options">
        <summary>总结速度与预算设置</summary>
        <div className="summary-settings-fields">
          <label>
            每次调用费用上限
            <input
              type="number"
              min="0.000001"
              step="any"
              value={options.max_request_cost}
              disabled={busy}
              onChange={(e) =>
                setOptions({ ...options, max_request_cost: e.target.value })
              }
            />
          </label>
          <label>
            单次输出 Token 上限
            <input
              type="number"
              min="512"
              max="8192"
              step="1"
              value={options.max_output_tokens}
              disabled={busy}
              onChange={(e) =>
                setOptions({ ...options, max_output_tokens: e.target.value })
              }
            />
          </label>
        </div>
        <label className="summary-thinking-option">
          <input
            type="checkbox"
            checked={!!options.thinking_enabled}
            disabled={busy}
            onChange={(e) =>
              setOptions({ ...options, thinking_enabled: e.target.checked })
            }
          />
          启用深度推理（较慢，可能增加用量）
        </label>
        <p className="muted">
          普通资料总结默认关闭深度推理。费用预留是本地预算控制值，不是实际报价；服务商账单可严格匹配时会以汇总账单替代预留。
        </p>
      </details>
      <div className="action-row">
        <button
          className="btn primary"
          disabled={busy || !saved || !model.trim()}
          onClick={() => persist()}
        >
          保存 DeepSeek 设置
        </button>
        <button
          className="btn"
          disabled={
            busy ||
            !saved?.credential.configured ||
            !!key ||
            model !== saved?.provider.model ||
            Number(options.max_request_cost) !==
              saved?.provider.max_request_cost ||
            Number(options.max_output_tokens) !==
              saved?.provider.max_output_tokens ||
            options.thinking_enabled !== saved?.provider.thinking_enabled
          }
          onClick={async () => {
            setBusy(true);
            setError(false);
            try {
              const d = await send("/material-summary/models");
              setModels(d.models);
              setMessage(
                d.models.length
                  ? "已读取 DeepSeek 模型，可在模型输入框中选择。"
                  : "接口没有返回 DeepSeek 模型，请核对模型权限。",
              );
            } catch (e) {
              setError(true);
              setMessage((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          查询 DeepSeek 模型
        </button>
        <button
          className="btn"
          disabled={busy || !saved?.credential.configured}
          onClick={() => persist(true)}
        >
          移除 DeepSeek 密钥
        </button>
      </div>
      <p className="muted">
        密钥单独加密保存在本机。长资料分段整理，已有总结直接读取缓存；实际费用以中转站账单为准。留空保留已有密钥，查询模型前请先保存。
      </p>
      {message && (
        <p
          role={error ? "alert" : "status"}
          className={error ? "error-text" : "muted"}
        >
          {message}
        </p>
      )}
    </details>
  );
}

export function MaterialSummary({
  file,
  tick,
  onPage,
}: {
  file: Row;
  tick: number;
  onPage: (page: number) => void;
}) {
  const gate=useSourceBillingGate();
  const [data, setData] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const reload = () =>
      api("/files/" + file.id + "/summary")
        .then((d) => {
          if (active) setData(d);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    reload();
    const timer = ["queued", "running"].includes(data?.task?.status)
      ? window.setInterval(reload, 2500)
      : undefined;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [file.id, tick, data?.task?.status]);
  const queued = data?.task?.status === "queued";
  const running = data?.task?.status === "running";
  const pending = queued || running;
  const summary = data?.summary;
  const refs = (nums: number[]) => (
    <span className="summary-page-links">
      {nums.map((n) => (
        <button
          className="summary-page-link"
          key={n}
          onClick={() => onPage(n)}
          aria-label={"查看第" + n + "页原文"}
        >
          第 {n} 页
        </button>
      ))}
    </span>
  );
  return (
    <section className="material-summary" data-testid="material-summary">
      <div className="summary-heading">
        <div>
          <h3>资料概括与分点</h3>
          <p className="muted">
            {summary?.reading_profile
              ? "文献原文重新阅读 · 研究问题、方法、条件、结果与局限"
              : summary?.source_review
                ? "Codex 结合原文与已保存分段复核整理 · 来源记录已保留"
                : "DeepSeek 根据提取文字生成 · 全篇概括、重点整理、主题归纳"}
          </p>
        </div>
        <button
          className="btn primary"
          disabled={
            gate.blocked ||
            busy ||
            pending ||
            (!!summary && !data?.stale) ||
            file.egress === "local_only"
          }
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await send("/files/" + file.id + "/summary");
              setData(await api("/files/" + file.id + "/summary"));
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {queued
            ? "已排队，等待处理"
            : running
              ? "DeepSeek 正在整理…"
              : data?.task?.status === "budget_blocked"
                ? "预算待核对"
                : summary && !data?.stale
                  ? "总结已保存"
                  : summary
                    ? "更新总结"
                    : "生成概括与分点"}
        </button>
      </div>
      <p className="muted summary-review-note">
        原文核对进度不代表 AI
        总结已审核。请另行点击页码，对照原文审阅总结的准确性与完整性。
      </p>
      {file.egress === "local_only" && (
        <p className="muted">此资料仅限本地，暂不能使用云端总结。</p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {queued && (
        <p role="status">
          已排队，前面还有 {data.task.queue_ahead ?? 0}{" "}
          个任务。后台会依次处理，无需重复提交。
        </p>
      )}
      {running && (
        <div role="status">
          <p>
            {data.task.phase || "DeepSeek 正在整理"} · {data.task.progress}%
          </p>
          <progress
            className="summary-progress"
            value={data.task.progress}
            max="100"
          />
        </div>
      )}
      {data?.budget && !data.budget.can_call && !summary && !pending && (
        <p className="summary-warning">
          项目本地预算占用 {Number(data.budget.used).toFixed(2)} /{" "}
          {Number(data.budget.limit).toFixed(2)}
          ，可在资料中心“核对账单并继续”释放可核实的多余预留。此数值不代表实际
          API 余额。
        </p>
      )}
      {data?.task?.error && !pending && (!summary || data?.stale) && (
        <p className="error-text" role="alert">
          {data.task.error}
          {data.task.status === "outcome_unknown"
            ? " 请核对中转站账单后再决定是否重试。"
            : ""}
        </p>
      )}
      {data?.stale && (
        <p className="summary-warning">
          {data?.task?.kind === "literature_refresh"
            ? "文献正在按新版流程重新生成，以下为旧版总结。"
            : "资料内容或总结配置已变化，以下为旧版总结，请更新后核对。"}
        </p>
      )}
      {summary ? (
        <div className="summary-result">
          <a
            className="btn summary-download"
            href={apiUrl("/files/" + file.id + "/summary/markdown")}
          >
            下载总结 Markdown
          </a>
          <h4>整体概括</h4>
          <p>{summary.overview}</p>
          {!!summary.reading_gaps?.length && (
            <details className="summary-warning">
              <summary>阅读中待核对的事项（不作为已核实结论）</summary>
              {summary.reading_gaps.map((gap: Row, i: number) => (
                <p key={i}>
                  {gap.text} · {gap.reason}
                </p>
              ))}
            </details>
          )}
          <h4>核心要点</h4>
          <ol>
            {summary.key_points.map((point: Row, i: number) => (
              <li key={i}>
                {point.text}
                {refs(point.pages)}
                {point.evidence_status === "needs_review" && (
                  <small className="summary-warning">原文引句待核对</small>
                )}
                {point.quote && (
                  <details>
                    <summary>查看对应原文引句</summary>
                    <blockquote>{point.quote}</blockquote>
                  </details>
                )}
              </li>
            ))}
          </ol>
          <h4>主题归纳</h4>
          <div className="summary-topics">
            {summary.topics.map((topic: Row, i: number) => (
              <article key={i}>
                <h5>{topic.title}</h5>
                <ul>
                  {topic.points.map((point: Row, j: number) => (
                    <li key={j}>
                      {point.text}
                      {refs(point.pages)}
                      {point.evidence_status === "needs_review" && (
                        <small className="summary-warning">
                          原文引句待核对
                        </small>
                      )}
                      {point.quote && (
                        <details>
                          <summary>查看对应原文引句</summary>
                          <blockquote>{point.quote}</blockquote>
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
          <p className="muted">
            模型 {summary.model} · 已整理 {summary.covered_pages.length}/
            {summary.total_pages} 页 ·{" "}
            {new Date(summary.created_at).toLocaleString("zh-CN")}
          </p>
          {!!summary.empty_pages.length && (
            <p className="summary-warning">
              第 {summary.empty_pages.join("、")}{" "}
              页无可用文字，未纳入总结，请核对原件或补充识别。
            </p>
          )}
          {!!summary.excluded_pages?.length && (
            <p className="muted">
              未纳入正文归纳：
              {summary.excluded_pages
                .map((p: Row) => `第 ${p.page} 页（${p.reason}）`)
                .join("；")}
              。
            </p>
          )}
          <p className="muted">
            点击页码回查原文，核实概括与要点是否忠实、完整。
          </p>
        </div>
      ) : (
        !pending && (
          <p className="muted">
            文字提取完成后，点击“生成概括与分点”，将该资料的已提取文字发送到
            DeepSeek 整理。
          </p>
        )
      )}
    </section>
  );
}

export const summaryStateLabels: Row = {
  completed: "总结已完成",
  queued: "总结排队中",
  running: "DeepSeek 整理中",
  budget_blocked: "预算待核对",
  failed: "总结失败",
  outcome_unknown: "远端状态待核实",
  incomplete: "结果待恢复",
  not_started: "尚未总结",
  stale: "总结待更新",
  cancelled: "总结已取消",
};

export function MaterialSummaryBatch({
  pid,
  tick,
  onOpen,
  onUpdated,
}: {
  pid: string;
  tick: number;
  onOpen: (fileId: string) => void;
  onUpdated: () => void;
}) {
  const gate=useSourceBillingGate();
  const [data, setData] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const pending = (data?.totals.queued ?? 0) + (data?.totals.running ?? 0);
  useEffect(() => {
    let active = true;
    const load = () =>
      api("/projects/" + pid + "/materials/summary-status")
        .then((d) => {
          if (active) setData(d);
        })
        .catch((e) => {
          if (active) {
            setMessage(e.message);
            setError(true);
          }
        });
    load();
    const timer = pending ? window.setInterval(load, 2500) : undefined;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [pid, tick, !!pending]);
  const action = async (path: string) => {
    setBusy(true);
    setError(false);
    setMessage("");
    try {
      const result = await send("/projects/" + pid + "/materials/" + path);
      setData(await api("/projects/" + pid + "/materials/summary-status"));
      onUpdated();
      setMessage(
        result.message ??
          (path === "summarize"
            ? "已提交 " +
              result.count +
              " 份资料；复用 " +
              result.cached +
              " 份已完成总结" +
              (result.skipped.length
                ? "；跳过 " +
                  result.skipped.length +
                  " 份：" +
                  result.skipped[0].reason
                : "")
            : "已继续 " +
              result.count +
              " 份资料。" +
              (result.billing?.reconciled
                ? "账单已核对。"
                : (result.billing?.reason ?? ""))),
      );
    } catch (e) {
      setMessage((e as Error).message);
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const totals = data?.totals ?? {};
  const waiting =
    data?.files?.filter((f: Row) =>
      ["failed", "budget_blocked", "outcome_unknown", "incomplete"].includes(
        f.state,
      ),
    ) ?? [];
  return (
    <section className="panel summary-batch" data-testid="summary-batch">
      <div className="summary-heading">
        <div>
          <h2>DeepSeek 资料整理</h2>
          <p className="muted">概括、分点和主题归纳，保留原文页码。</p>
        </div>
        <div className="summary-actions">
          <button
            className="btn primary"
            disabled={busy || gate.blocked || !!pending || !data?.total}
            onClick={() => action("summarize")}
          >
            {pending ? "批量整理进行中" : "DeepSeek 批量总结"}
          </button>
          <button
            className="btn"
            disabled={busy || !!pending}
            onClick={() => action("resume-summaries")}
          >
            核对账单并继续
          </button>
        </div>
      </div>
      <div className="summary-counters">
        <span>
          已完成{" "}
          <strong>
            {totals.completed ?? 0} / {data?.total ?? 0}
          </strong>
        </span>
        <span>排队 {totals.queued ?? 0}</span>
        <span>执行中 {totals.running ?? 0}</span>
        <span>预算待核对 {totals.budget_blocked ?? 0}</span>
        <span>失败 {totals.failed ?? 0}</span>
        {!!totals.outcome_unknown && (
          <span>远端待核实 {totals.outcome_unknown}</span>
        )}
      </div>
      <progress
        className="summary-progress"
        max={Math.max(1, data?.total ?? 1)}
        value={totals.completed ?? 0}
      />
      {data?.files
        ?.filter((f: Row) => f.state === "running")
        .map((f: Row) => (
          <p key={f.file_id} role="status">
            正在整理：
            <button
              className="summary-inline-link"
              onClick={() => onOpen(f.file_id)}
            >
              {f.name}
            </button>{" "}
            · {f.task.phase} · {f.task.progress}%
          </p>
        ))}
      {data?.budget && (
        <p className="summary-budget-caption">
          本地项目预算占用 {Number(data.budget.used).toFixed(4)} /{" "}
          {Number(data.budget.limit).toFixed(2)}；其中未核实的费用预留{" "}
          {Number(data.budget.unknown_reserved).toFixed(2)}。
          {data.account_usage?.balance_known
            ? "服务商余额 " +
              data.account_usage.remaining_exact +
              " " +
              (data.account_usage.unit_source === "response" ? data.account_usage.unit : "（币种未报告）") +
              "（查询于 " +
              new Date(data.account_usage.fetched_at).toLocaleTimeString(
                "zh-CN",
              ) +
              "）。"
            : "本地预算占用与服务商余额分别记录。"}
        </p>
      )}
      {!!waiting.length && (
        <details className="summary-blocked-files">
          <summary>{waiting.length} 份资料需要处理，展开查看原因</summary>
          {waiting.map((f: Row) => (
            <div key={f.file_id}>
              <button
                className="summary-inline-link"
                onClick={() => onOpen(f.file_id)}
              >
                {f.name}
              </button>
              <span>{summaryStateLabels[f.state]}</span>
              <p>{f.task?.error}</p>
            </div>
          ))}
        </details>
      )}
      {!!totals.completed && (
        <details className="summary-completed-files">
          <summary>查看已完成的 {totals.completed} 份总结</summary>
          {data?.files
            ?.filter((f: Row) => f.state === "completed")
            .map((f: Row) => (
              <button
                key={f.file_id}
                className="summary-inline-link"
                onClick={() => onOpen(f.file_id)}
              >
                {f.name}
              </button>
            ))}
        </details>
      )}
      {message && (
        <p
          role={error ? "alert" : "status"}
          className={error ? "error-text" : "muted"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

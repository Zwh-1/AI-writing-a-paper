import { useEffect, useRef, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;

const EMPTY_STRATEGY: Row = {
  mode: "priority",
  retry_same_endpoint: 4,
  rotate_delay_base: 0.4,
  rotate_delay_max: 12.0,
  failover_on: [
    "connect_error", "rate_limit", "http_401", "http_402",
    "http_403", "http_404", "http_429", "timeout",
  ],
  circuit_breaker: {
    enabled: true,
    failure_threshold: 3,
    cooldown_seconds: 120,
  },
};

function emptyEndpoint(): Row {
  return {
    id: "",
    name: "",
    enabled: true,
    priority: 1,
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai",
    model: "deepseek-chat",
    max_output_tokens: 8192,
    timeout_seconds: 600,
    context_limit: 100000,
    max_request_cost: 2.0,
    input_price: null,
    output_price: null,
    currency: "USD",
    json_mode: true,
    supports_images: false,
    thinking_enabled: false,
    streaming: false,
    max_attempts: 5,
    credential_ref: "primary",
    api_key: "",
  };
}

export default function DeepSeekPoolSettings() {
  const [saved, setSaved] = useState<Row | null>(null);
  const [strategy, setStrategy] = useState<Row>(EMPTY_STRATEGY);
  const [endpoints, setEndpoints] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const pending = useRef(false);

  useEffect(() => {
    let alive = true;
    api("/deepseek-pool/settings")
      .then((data) => {
        if (!alive) return;
        setSaved(data);
        setStrategy(data.strategy);
        setEndpoints(data.endpoints);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, []);

  const updateEndpoint = (index: number, patch: Row) => {
    setEndpoints((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  };

  async function save() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const cleaned = endpoints.map((ep) => {
        const { api_key, credential, health, ...rest } = ep;
        return rest;
      });
      const endpoint_keys: Record<string, string> = {};
      for (const ep of endpoints) {
        if (ep.api_key?.trim()) endpoint_keys[ep.id || ep.name] = ep.api_key.trim();
      }
      const body: Row = {
        strategy,
        endpoints: cleaned,
        ...(Object.keys(endpoint_keys).length ? { endpoint_keys } : {}),
      };
      const data = await send("/deepseek-pool/settings", body, "PUT");
      setSaved(data);
      setStrategy(data.strategy);
      setEndpoints(data.endpoints);
      setMessage("多端点池设置已保存。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  const toggleStrategy = (key: string, value: any) =>
    setStrategy((s) => ({ ...s, [key]: value }));
  const toggleBreaker = (key: string, value: any) =>
    setStrategy((s) => ({
      ...s,
      circuit_breaker: { ...s.circuit_breaker, [key]: value },
    }));

  return (
    <details className="panel mb" open={expanded}>
      <summary onClick={() => setExpanded(!expanded)}>
        DeepSeek 多端点池
        <span className="muted">
          {saved?.endpoints?.length
            ? `已配置 ${saved.endpoints.length} 个端点`
            : "未启用（使用单端点配置）"}
        </span>
      </summary>
      {error && (
        <p className="settings-feedback error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="settings-feedback success" role="status">
          {message}
        </p>
      )}
      <p className="muted">DeepSeek 单条请求链最多尝试 5 次（含首次）；连续 5 次明确且可安全重试的失败后，自动切换到已启用并配置就绪的 GPT 备选，GPT 独立最多尝试 5 次。同一模型路线中的接口切换、网络重试和格式修复共用该路线的尝试上限，不会逐层增加次数。</p>
      <div className="form-grid">
        <label className="field">
          切换模式
          <select
            value={strategy.mode}
            onChange={(e) => toggleStrategy("mode", e.target.value)}
            disabled={busy}
          >
            <option value="priority">优先级（先高后低）</option>
            <option value="round_robin">轮询（round-robin）</option>
          </select>
        </label>
        <label className="field">
          同端点重试次数
          <input
            type="number"
            min="0"
            max="10"
            value={strategy.retry_same_endpoint}
            onChange={(e) =>
              toggleStrategy("retry_same_endpoint", Number(e.target.value))
            }
            disabled={busy}
          />
        </label>
      </div>
      <div className="form-grid">
        <label className="field">
          熔断失败阈值
          <input
            type="number"
            min="1"
            max="20"
            value={strategy.circuit_breaker?.failure_threshold ?? 3}
            onChange={(e) =>
              toggleBreaker("failure_threshold", Number(e.target.value))
            }
            disabled={busy}
          />
        </label>
        <label className="field">
          熔断冷却秒数
          <input
            type="number"
            min="1"
            max="86400"
            value={strategy.circuit_breaker?.cooldown_seconds ?? 120}
            onChange={(e) =>
              toggleBreaker("cooldown_seconds", Number(e.target.value))
            }
            disabled={busy}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={strategy.circuit_breaker?.enabled ?? true}
            onChange={(e) => toggleBreaker("enabled", e.target.checked)}
            disabled={busy}
          />
          启用熔断器
        </label>
      </div>
      <div className="endpoint-list">
        {endpoints.map((ep, index) => (
          <details className="endpoint-card" key={ep.id || index}>
            <summary>
              <span>{ep.name || "（未命名端点）"}</span>
              <span className="muted">{ep.base_url}</span>
            </summary>
            <div className="form-grid">
              <label className="field">
                名称
                <input
                  value={ep.name}
                  onChange={(e) => updateEndpoint(index, { name: e.target.value })}
                  disabled={busy}
                />
              </label>
              <label className="field">
                优先级（1 最高）
                <input
                  type="number"
                  min="1"
                  value={ep.priority}
                  onChange={(e) =>
                    updateEndpoint(index, { priority: Number(e.target.value) })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                端点地址
                <input
                  value={ep.base_url}
                  onChange={(e) =>
                    updateEndpoint(index, { base_url: e.target.value })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                模型 ID
                <input
                  value={ep.model}
                  onChange={(e) =>
                    updateEndpoint(index, { model: e.target.value })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                接口协议
                <select
                  value={ep.protocol}
                  onChange={(e) =>
                    updateEndpoint(index, { protocol: e.target.value })
                  }
                  disabled={busy}
                >
                  <option value="openai">Chat Completions</option>
                  <option value="responses">Responses</option>
                </select>
              </label>
              <label className="field">
                密钥来源
                <select
                  value={ep.credential_ref}
                  onChange={(e) =>
                    updateEndpoint(index, { credential_ref: e.target.value })
                  }
                  disabled={busy}
                >
                  <option value="primary">使用主密钥</option>
                  <option value="pool">使用端点专属密钥</option>
                </select>
              </label>
              {ep.credential_ref === "pool" && (
                <label className="field">
                  端点专属 API key
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={ep.api_key ?? ""}
                    placeholder={
                      ep.credential?.configured
                        ? `已保存 ${ep.credential.masked}，留空保留`
                        : "填写此端点专属密钥"
                    }
                    onChange={(e) =>
                      updateEndpoint(index, { api_key: e.target.value })
                    }
                    disabled={busy}
                  />
                </label>
              )}
              {ep.credential_ref === "primary" && ep.credential?.configured && (
                <span className="muted">使用主密钥 {ep.credential.masked}</span>
              )}
            </div>
            <div className="form-grid">
              <label className="field">
                输出上限
                <input
                  type="number"
                  value={ep.max_output_tokens}
                  onChange={(e) =>
                    updateEndpoint(index, {
                      max_output_tokens: Number(e.target.value),
                    })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                超时秒数
                <input
                  type="number"
                  value={ep.timeout_seconds}
                  onChange={(e) =>
                    updateEndpoint(index, {
                      timeout_seconds: Number(e.target.value),
                    })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                上下文上限
                <input
                  type="number"
                  value={ep.context_limit}
                  onChange={(e) =>
                    updateEndpoint(index, {
                      context_limit: Number(e.target.value),
                    })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                单次费用上限
                <input
                  type="number"
                  step="any"
                  value={ep.max_request_cost}
                  onChange={(e) =>
                    updateEndpoint(index, {
                      max_request_cost: Number(e.target.value),
                    })
                  }
                  disabled={busy}
                />
              </label>
              <label className="field">
                最多尝试次数（含首次）
                <input
                  type="number"
                  min="1"
                  max="5"
                  step="1"
                  value={ep.max_attempts ?? 5}
                  onChange={(e) => updateEndpoint(index, { max_attempts: Number(e.target.value) })}
                  disabled={busy}
                />
              </label>
            </div>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={ep.enabled}
                onChange={(e) =>
                  updateEndpoint(index, { enabled: e.target.checked })
                }
                disabled={busy}
              />
              启用此端点
            </label>
            {ep.health && (
              <p className="muted">
                状态：{ep.health.status === "open" ? "熔断" : ep.health.status}
                {ep.health.consecutive_failures
                  ? `，连续失败 ${ep.health.consecutive_failures} 次`
                  : ""}
              </p>
            )}
            <button
              type="button"
              className="btn"
              onClick={() => setEndpoints((prev) => prev.filter((_, i) => i !== index))}
              disabled={busy}
            >
              移除端点
            </button>
          </details>
        ))}
      </div>
      <div className="actions">
        <button
          type="button"
          className="btn"
          onClick={() => setEndpoints((prev) => [...prev, emptyEndpoint()])}
          disabled={busy || endpoints.length >= (saved?.max_endpoints ?? 6)}
        >
          添加端点
        </button>
        <button type="button" className="btn primary" onClick={save} disabled={busy}>
          {busy ? "保存中…" : "保存多端点池"}
        </button>
      </div>
    </details>
  );
}

import { useEffect, useRef, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;

export default function ImageGenerationSettings({
  embedded = false,
  onChange,
}: {
  embedded?: boolean;
  onChange?: (settings: Row) => void;
}) {
  const [saved, setSaved] = useState<Row | null>(null),
    [cfg, setCfg] = useState<Row | null>(null);
  const [key, setKey] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState("");
  const pending = useRef(false),
    change = useRef(onChange);
  change.current = onChange;
  useEffect(() => {
    let alive = true;
    api("/image-generation/settings")
      .then((data) => {
        if (!alive) return;
        setSaved(data);
        setCfg(data.provider);
        change.current?.(data);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, []);
  async function save(clear = false) {
    if (pending.current || !cfg) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (!clear && key && !key.trim())
        throw new Error("生图 key 不能只包含空格");
      const data = await send(
        "/image-generation/settings",
        {
          provider: cfg,
          ...(clear
            ? { clear_key: true }
            : key.trim()
              ? { api_key: key.trim() }
              : {}),
        },
        "PUT",
      );
      setSaved(data);
      setCfg(data.provider);
      setKey("");
      change.current?.(data);
      setMessage(
        clear
          ? "已清除独立生图密钥。"
          : data.ready
            ? "gpt-image-2 专用配置已保存。"
            : "设置已保存，请填写独立生图 key。",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const content = (
    <>
      <p className="muted mb">
        图片生成和编辑固定使用 gpt-image-2。生图 key 单独通过 Windows DPAPI
        加密保存；留空保存会保留原 key。
      </p>
      {error && (
        <p className="pv-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="pv-message" role="status">
          {message}
        </p>
      )}
      {cfg && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset className="settings-fieldset" disabled={busy}>
            <div className="form-grid">
              <label className="field">
                生图 API 地址
                <input
                  aria-label="生图 API 地址"
                  value={cfg.base_url}
                  onChange={(e) => setCfg({ ...cfg, base_url: e.target.value })}
                />
              </label>
              <label className="field">
                独立生图 API key
                <input
                  aria-label="独立生图 API key"
                  type="password"
                  autoComplete="new-password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder={
                    saved?.credential.configured
                      ? `已保存 ${saved.credential.masked}，留空保留`
                      : "填写此中转站的生图专用 key"
                  }
                />
              </label>
              <label className="field">
                生图模型
                <input aria-label="生图模型" value="gpt-image-2" readOnly />
              </label>
              <label className="field">
                图片尺寸
                <select
                  aria-label="图片尺寸"
                  value={cfg.size}
                  onChange={(e) => setCfg({ ...cfg, size: e.target.value })}
                >
                  <option value="2048x1152">2048 × 1152 横图</option>
                  <option value="1152x2048">1152 × 2048 竖图</option>
                  <option value="1536x1536">1536 × 1536 方图</option>
                  <option value="1536x1024">1536 × 1024 横图</option>
                  <option value="1024x1536">1024 × 1536 竖图</option>
                  <option value="1024x1024">1024 × 1024 方图</option>
                </select>
              </label>
              <label className="field">
                生图画质
                <select
                  aria-label="生图画质"
                  value={cfg.quality}
                  onChange={(e) => setCfg({ ...cfg, quality: e.target.value })}
                >
                  <option value="high">高</option>
                  <option value="medium">中</option>
                  <option value="low">低</option>
                  <option value="auto">自动</option>
                </select>
              </label>
            </div>
            <details className="mb">
              <summary>生图费用与超时</summary>
              <div className="form-grid">
                <label className="field">
                  生图费用币种
                  <select
                    aria-label="生图费用币种"
                    value={cfg.currency}
                    onChange={(e) =>
                      setCfg({ ...cfg, currency: e.target.value })
                    }
                  >
                    <option value="USD">USD</option>
                    <option value="CNY">CNY</option>
                  </select>
                </label>
                <label className="field">
                  单次费用上限
                  <input
                    aria-label="生图单次费用上限"
                    type="number"
                    min="0.000001"
                    step="any"
                    value={cfg.max_request_cost}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        max_request_cost: Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label className="field">
                  中转站每张固定金额（可留空）
                  <input
                    aria-label="生图每张固定金额"
                    type="number"
                    min="0"
                    step="any"
                    value={cfg.image_price ?? ""}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        image_price:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </label>
                <label className="field">
                  生图超时秒数
                  <input
                    aria-label="生图超时秒数"
                    type="number"
                    min="30"
                    max="1800"
                    value={cfg.timeout_seconds}
                    onChange={(e) =>
                      setCfg({
                        ...cfg,
                        timeout_seconds: Number(e.target.value),
                      })
                    }
                  />
                </label>
              </div>
              <p className="muted">
                仅当中转站按张固定收费时填写单张金额，并在费用依据账本导入报价；缺少依据时阻止收费。当前项目预算币种：
                {saved?.budget_currency ?? "USD"}。
              </p>
            </details>
            <div className="pv-actions">
              <button type="submit" className="btn primary" disabled={busy}>
                保存生图设置
              </button>
              {saved?.credential.configured && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => save(true)}
                >
                  清除独立生图 key
                </button>
              )}
              <span className="muted">
                {saved?.credential.configured
                  ? `独立密钥已保存 ${saved.credential.masked}`
                  : "尚未配置独立生图密钥"}
              </span>
            </div>
          </fieldset>
        </form>
      )}
    </>
  );
  return embedded ? (
    <details className="pv-image-settings">
      <summary>
        gpt-image-2 专用接口与独立 key · {saved?.ready ? "已就绪" : "待配置"}
      </summary>
      {content}
    </details>
  ) : (
    <section className="panel mb" aria-label="GPT 生图专用设置">
      <div className="section-heading">
        <h2>GPT 生图专用设置</h2>
        <span>gpt-image-2 · {saved?.ready ? "已就绪" : "待配置"}</span>
      </div>
      {content}
    </section>
  );
}

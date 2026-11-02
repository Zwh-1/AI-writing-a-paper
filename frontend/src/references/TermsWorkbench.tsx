import { useEffect, useState, type FormEvent } from "react";
import { Languages, Plus, Search } from "lucide-react";
import { send } from "../client";
import type { Row, WorkspaceAction } from "../workspace/types";
import { ReferenceInspector, ResearchColumns } from "./ReferenceWorkbenchParts";

export default function TermsWorkbench({
  pid,
  terms,
  loading,
  act,
}: {
  pid: string;
  terms: Row[];
  loading: boolean;
  act: WorkspaceAction;
}) {
  const [query, setQuery] = useState(""),
    [selected, setSelected] = useState<string | null>(null),
    [creating, setCreating] = useState(false),
    [zh, setZh] = useState(""),
    [en, setEn] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const filtered = terms.filter((term) =>
    `${term.zh} ${term.en}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const active = creating
    ? undefined
    : terms.find((term) => term.id === selected) || terms[0];
  useEffect(() => {
    setZh(active?.zh || "");
    setEn(active?.en || "");
    setError("");
    setNotice("");
  }, [active?.id, active?.zh, active?.en, creating]);
  const dirty = zh !== (active?.zh || "") || en !== (active?.en || "");
  const choose = (id: string | null) => {
    if (id && active?.id === id) return;
    if (dirty && !window.confirm("当前术语尚未保存，是否切换？")) return;
    if (!id) {
      setZh("");
      setEn("");
      setNotice("");
      setError("");
    }
    setCreating(id === null);
    setSelected(id);
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await act(
        () =>
          send(
            `/projects/${pid}/terms${active ? `/${active.id}` : ""}`,
            { zh: zh.trim(), en: en.trim() },
            active ? "PATCH" : "POST",
          ),
        "术语已保存",
      );
      if (value) {
        const saved = (value as Row[]).find((term) => term.zh === zh.trim());
        setSelected(saved?.id || null);
        setCreating(false);
        setNotice("术语已保存，摘要、正文与翻译可共用这一译法。");
      } else setError("术语未保存，请核对提示后重试。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="中英文术语表" className="terms-workbench">
      <ResearchColumns
        directory={
          <>
            <div className="research-directory-heading">
              <h2>术语目录</h2>
              <span>{terms.length} 项</span>
            </div>
            <label className="research-search">
              <Search size={16} />
              <input
                aria-label="搜索中英文术语"
                placeholder="搜索中文或英文"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <button
              className="btn research-add-term"
              onClick={() => choose(null)}
              disabled={busy}
            >
              <Plus size={15} />
              新增术语
            </button>
            <div className="reference-term-list">
              {filtered.map((term) => (
                <button
                  key={term.id}
                  className={`research-directory-item ${active?.id === term.id ? "is-selected" : ""}`}
                  onClick={() => choose(term.id)}
                  disabled={busy}
                  aria-pressed={active?.id === term.id}
                >
                  <strong>{term.zh}</strong>
                  <span lang="en">{term.en}</span>
                </button>
              ))}
            </div>
            {!filtered.length && (
              <p className="research-directory-empty">
                {loading
                  ? "正在读取术语…"
                  : terms.length
                    ? "没有匹配的术语。"
                    : "尚无术语，在右侧填写第一组中英文译法。"}
              </p>
            )}
          </>
        }
        inspector={
          <>
            <ReferenceInspector title="统一译法">
              <Languages size={22} />
              <p>为同一概念使用一致的中文名称和英文译法。</p>
              <dl className="reference-metadata">
                <div>
                  <dt>使用范围</dt>
                  <dd>摘要、正文、翻译</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>
                    {dirty ? "有未保存的修改" : active ? "已保存" : "待登记"}
                  </dd>
                </div>
              </dl>
            </ReferenceInspector>
            <ReferenceInspector title="填写建议">
              <p>英文术语可保留规范缩写，例如 Verifiable Credential (VC)。</p>
              <p>修改术语后，已保存的正文仍需检查是否使用了旧译法。</p>
            </ReferenceInspector>
          </>
        }
      >
        <article className="reference-document">
          <header className="reference-document-heading">
            <span className="research-kicker">统一术语</span>
            <h2>中英文术语表</h2>
            <p>
              {active
                ? "编辑当前术语的中英文译法。"
                : "登记已确认的译法，供摘要、正文与翻译共用。"}
            </p>
          </header>
          <form onSubmit={save} className="term-inline-form">
            <fieldset disabled={busy}>
              <label>
                中文术语
                <input
                  name="zh"
                  required
                  maxLength={300}
                  value={zh}
                  onChange={(e) => setZh(e.target.value)}
                  placeholder="例如：可验证凭证"
                />
              </label>
              <label>
                对应英文
                <input
                  name="en"
                  required
                  maxLength={600}
                  value={en}
                  onChange={(e) => setEn(e.target.value)}
                  placeholder="例如：Verifiable Credential (VC)"
                />
              </label>
              <div className="term-translation-preview">
                <small>译法预览</small>
                <strong>{zh || "中文术语"}</strong>
                <span aria-hidden="true">↔</span>
                <p lang="en">{en || "English term"}</p>
              </div>
              {error && (
                <p role="alert" className="error-text">
                  {error}
                </p>
              )}
              {notice && <p role="status">{notice}</p>}
              <div className="actions">
                <button
                  className="btn primary"
                  disabled={busy || !zh.trim() || !en.trim()}
                >
                  {busy ? "正在保存…" : "保存术语"}
                </button>
                {active && (
                  <button
                    type="button"
                    className="btn danger-outline"
                    aria-label={`删除术语：${active.zh}`}
                    onClick={async () => {
                      if (
                        !window.confirm(
                          `删除术语“${active.zh}”？已有正文会保留。`,
                        )
                      )
                        return;
                      setBusy(true);
                      try {
                        const result = await act(
                          () =>
                            send(
                              `/projects/${pid}/terms/${active.id}`,
                              {},
                              "DELETE",
                            ),
                          "术语已删除",
                        );
                        if (result) {
                          setSelected(null);
                          setCreating(false);
                        }
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    删除术语
                  </button>
                )}
              </div>
            </fieldset>
          </form>
        </article>
      </ResearchColumns>
    </section>
  );
}

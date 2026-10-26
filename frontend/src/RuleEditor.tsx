import { useEffect, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { api, send } from "./client";
type Row = Record<string, any>;
export const checkerLabels: Record<string, string> = {
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
export const scopeLabels: Record<string, string> = {
  global: "全文",
  abstract: "中文摘要",
  abstract_en: "英文摘要",
  introduction: "绪论",
  literature: "文献综述",
  system_design: "系统设计",
  implementation: "系统实现",
  results: "结果 / 测试",
  conclusion: "结论",
  methods: "研究方法",
  body: "正文",
};
export const stateLabels: Record<string, string> = {
  mandatory: "强制要求",
  recommended: "建议",
  pending: "待判断",
  example: "正例",
  counterexample: "反例",
  candidate: "待确认",
  confirmed: "已确认",
  machine_verified: "机器审核通过",
  reference_only: "知识、示例与条件规范",
  rejected: "已排除",
};
export const operationLabels: Record<string, string> = {
  generate: "生成",
  revise: "改写",
  polish: "润写",
  expand: "扩写",
  compress: "压缩",
  translate: "翻译",
};
const formatLabels: Record<string, string> = {
  font: "正文字体",
  latin_font: "西文字体",
  font_size: "正文字号（pt）",
  line_spacing: "行距（倍）",
  first_line_chars: "首行缩进（字）",
  margin_top: "上边距（mm）",
  margin_bottom: "下边距（mm）",
  margin_left: "左边距（mm）",
  margin_right: "右边距（mm）",
  heading_font: "标题字体",
  reference_superscript: "引用编号右上标",
};
const decode = (value: any, fallback: any = null) => {
  try {
    return typeof value === "string" ? JSON.parse(value) : (value ?? fallback);
  } catch {
    return fallback;
  }
};
export function ruleKey(checker: string, value: any) {
  return checker === "manual"
    ? ""
    : checker === "format_value"
      ? "format:" + value.key
      : ["word_min", "word_max", "word_range"].includes(checker)
        ? "word_count"
        : checker;
}
export function defaultValue(checker: string): any {
  if (checker === "manual") return null;
  if (checker === "word_range") return { min: 300, max: 500 };
  if (checker === "reference_recent")
    return { years: 5, minimum: 0.5, anchor_year: new Date().getFullYear() };
  if (checker === "format_value") return { key: "font_size", expected: 12 };
  if (
    ["required_terms", "forbidden_terms", "required_sections"].includes(checker)
  )
    return [];
  if (checker === "reference_foreign_ratio") return 0.2;
  return 1;
}
export default function RuleEditor({
  rule,
  pid,
  act,
  onClose,
  onSaved,
  executionOnly = false,
}: {
  rule: Row;
  pid: string;
  act: (fn: () => Promise<any>, message?: string) => Promise<any>;
  onClose: () => void;
  onSaved?: () => void;
  executionOnly?: boolean;
}) {
  const [draft, setDraft] = useState<Row>(() => ({
    ...rule,
    statement: rule.statement ?? "",
    modality: rule.modality ?? "mandatory",
    state: rule.state ?? "confirmed",
    category: rule.category ?? "content",
    scope: rule.scope ?? "global",
    checker: rule.checker ?? "manual",
    decision_note: rule.decision_note ?? "",
    value: decode(rule.value),
    operations: decode(rule.operations, Object.keys(operationLabels)),
  }));
  const [condition, setCondition] = useState(
    JSON.stringify(decode(rule.conditions, {}), null, 2),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [evidence,setEvidence]=useState<Row|null>(null);
  const [evidenceError,setEvidenceError]=useState("");
  useEffect(()=>{let alive=true;if(rule.id&&rule.source_id)api(`/rules/${rule.id}/evidence`).then(value=>{if(alive)setEvidence(value)}).catch(e=>{if(alive)setEvidenceError(e.message)});return()=>{alive=false}},[rule.id,rule.source_id]);
  const update = (key: string, value: any) =>
    setDraft((d) => ({ ...d, [key]: value }));
  const numeric = [
    "word_min",
    "word_max",
    "reference_min",
    "heading_min",
    "figure_min",
    "table_min",
    "rendered_page_min",
  ].includes(draft.checker);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      let conditions;
      try {
        conditions = JSON.parse(condition);
      } catch {
        throw new Error("附加适用条件不是有效的 JSON，请检查后重试");
      }
      if (!draft.operations.length) throw new Error("请至少选择一种写作操作");
      const normalizedValue = Array.isArray(draft.value)
        ? draft.value.map((v: string) => v.trim()).filter(Boolean)
        : draft.value;
      const payload = {
        ...draft,
        value: normalizedValue,
        statement: draft.statement.trim(),
        conditions,
        rule_key: ruleKey(draft.checker, normalizedValue),
      };
      const result = await act(async () => {
        try {
          return await send(
            rule.id ? "/rules/" + rule.id : "/projects/" + pid + "/rules",
            payload,
            rule.id ? "PATCH" : "POST",
          );
        } catch (e) {
          setError((e as Error).message);
          throw e;
        }
      }, "规则已保存；发布后用于后续写作与检查");
      if (result) {
        onSaved?.();
        onClose();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="overlay">
      <section
        className="modal wide rule-editor"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rule-editor-title"
      >
        <header>
          <h2 id="rule-editor-title">
            {executionOnly
              ? "规则适用范围与自动检查"
              : rule.id
                ? "核对规则条款"
                : "新增项目要求"}
          </h2>
          <button
            className="icon-button"
            aria-label="关闭规则编辑"
            disabled={busy}
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        <form className="modal-body" onSubmit={submit}>
          <fieldset disabled={busy} className="rule-fieldset">
            <label className="field">
              <span>具体要求</span>
              <textarea
                aria-label="具体要求"
                required
                rows={4}
                value={draft.statement}
                onChange={(e) => update("statement", e.target.value)}
              />
            </label>
            {evidence?.integrity?.needs_attention&&<p className="alert">{evidence.integrity.issue}。该片段暂不参与自动归并或确定审核。</p>}
            {evidence?.context&&<details className="rule-advanced" open={evidence.integrity?.needs_attention||undefined}><summary>完整原文与修复记录</summary><p className="muted">{evidence.source_file} · 第 {evidence.page_number} 页{evidence.source_method==="windows_ocr"?" · 本地 OCR 原文":evidence.source_method==="visual_transcription"?" · 原图核对转录":""}</p><label className="field"><span>完整原文上下文</span><textarea aria-label="完整原文上下文" rows={6} value={evidence.context} readOnly/></label>{evidence.visual_correction&&<p className="muted">已按原图校正 OCR 的范围连接符，原始 OCR 记录保留。</p>}{evidence.before_statement&&<p className="muted">已按原文补回遗漏内容，旧片段：{evidence.before_statement}</p>}</details>}
            {evidenceError&&<p className="alert">原文依据读取失败：{evidenceError}</p>}
            <div className="form-grid">
              <label className="field">
                <span>性质</span>
                <select
                  aria-label="性质"
                  value={draft.modality}
                  onChange={(e) => update("modality", e.target.value)}
                >
                  {[
                    "mandatory",
                    "recommended",
                    "pending",
                    "example",
                    "counterexample",
                  ].map((k) => (
                    <option key={k} value={k}>
                      {stateLabels[k]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>状态</span>
                <select
                  aria-label="状态"
                  value={draft.state}
                  onChange={(e) => update("state", e.target.value)}
                >
                  {["candidate", "confirmed", ...(rule.state === "machine_verified" ? ["machine_verified"] : []), "reference_only", "rejected"].map((k) => (
                    <option key={k} value={k}>
                      {stateLabels[k]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>类别</span>
                <select
                  aria-label="类别"
                  value={draft.category}
                  onChange={(e) => update("category", e.target.value)}
                >
                  {Object.entries({
                    content: "内容要求",
                    reference: "引用规范",
                    format: "排版规则",
                    structure: "结构要求",
                  }).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>适用章节类型或 ID</span>
                <input
                  aria-label="适用章节类型或 ID"
                  required
                  list="rule-scopes"
                  value={draft.scope}
                  onChange={(e) => update("scope", e.target.value)}
                />
                <datalist id="rule-scopes">
                  {Object.entries(scopeLabels).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </datalist>
                <small>global 为全文；也可填具体章节 ID。</small>
              </label>
            </div>
            {draft.state === "confirmed" &&
              ["pending", "example", "counterexample"].includes(
                draft.modality,
              ) && (
                <p className="alert">
                  此性质不会进入规则包。要用于写作，请先判断为强制要求或建议。
                </p>
              )}
            <div className="rule-editor-heading">
              <h3>执行方式</h3>
              {rule.id && (
                <button
                  className="text-button"
                  type="button"
                  onClick={async () => {
                    setError("");
                    try {
                      const d = await api("/rules/" + rule.id + "/infer", {
                        method: "POST",
                        body: JSON.stringify({
                          statement: draft.statement,
                          scope: draft.scope,
                        }),
                      });
                      setDraft((v) => ({
                        ...v,
                        checker: d.checker,
                        value: d.value,
                        scope: d.scope,
                      }));
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  从条款推断候选参数
                </button>
              )}
            </div>
            <label className="field">
              <span>检查器</span>
              <select
                aria-label="检查器"
                value={draft.checker}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    checker: e.target.value,
                    value: defaultValue(e.target.value),
                  }))
                }
              >
                {Object.entries(checkerLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            {draft.checker === "manual" && (
              <p className="muted mb">
                此要求将随规则包参与写作；检查时需要人工核对并记录依据。
              </p>
            )}
            {numeric && (
              <label className="field">
                <span>目标数值</span>
                <input
                  aria-label="目标数值"
                  type="number"
                  min={0}
                  step={1}
                  required
                  value={draft.value ?? ""}
                  onChange={(e) =>
                    update(
                      "value",
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                />
                {draft.checker === "rendered_page_min" && (
                  <small>实际分页仍需在导出后人工核对。</small>
                )}
              </label>
            )}
            {draft.checker === "word_range" && (
              <div className="form-grid">
                {[
                  ["min", "最少字数"],
                  ["max", "最多字数"],
                ].map(([k, v]) => (
                  <label className="field" key={k}>
                    <span>{v}</span>
                    <input
                      aria-label={v}
                      type="number"
                      min={0}
                      required
                      value={draft.value?.[k] ?? ""}
                      onChange={(e) =>
                        update("value", {
                          ...draft.value,
                          [k]: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                ))}
              </div>
            )}
            {[
              "required_terms",
              "forbidden_terms",
              "required_sections",
            ].includes(draft.checker) && (
              <label className="field">
                <span>
                  {draft.checker === "required_sections"
                    ? "必需章节（每行一个类型或标题）"
                    : "词项列表（每行一个）"}
                </span>
                <textarea
                  required
                  rows={4}
                  value={
                    Array.isArray(draft.value) ? draft.value.join("\n") : ""
                  }
                  onChange={(e) => update("value", e.target.value.split("\n"))}
                />
              </label>
            )}
            {draft.checker === "reference_foreign_ratio" && (
              <label className="field">
                <span>最低外文文献比例（0 至 1）</span>
                <input
                  aria-label="最低外文文献比例（0 至 1）"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  required
                  value={draft.value ?? ""}
                  onChange={(e) => update("value", Number(e.target.value))}
                />
              </label>
            )}
            {draft.checker === "reference_recent" && (
              <div className="form-grid">
                {[
                  ["years", "近多少年", 1, 100, 1],
                  ["minimum", "最低比例（0 至 1）", 0, 1, 0.01],
                  ["anchor_year", "基准年份", 1900, 2200, 1],
                ].map(([k, v, min, max, step]) => (
                  <label className="field" key={String(k)}>
                    <span>{v}</span>
                    <input
                      aria-label={String(v)}
                      type="number"
                      required
                      min={Number(min)}
                      max={Number(max)}
                      step={Number(step)}
                      value={draft.value?.[k] ?? ""}
                      onChange={(e) =>
                        update("value", {
                          ...draft.value,
                          [k]: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                ))}
              </div>
            )}
            {draft.checker === "format_value" && (
              <div className="form-grid">
                <label className="field">
                  <span>排版属性</span>
                  <select
                    aria-label="排版属性"
                    value={draft.value?.key ?? "font_size"}
                    onChange={(e) =>
                      update("value", {
                        key: e.target.value,
                        expected:
                          e.target.value === "reference_superscript"
                            ? true
                            : ["font", "latin_font", "heading_font"].includes(
                                  e.target.value,
                                )
                              ? "宋体"
                              : e.target.value === "font_size"
                                ? 12
                                : e.target.value === "line_spacing"
                                  ? 1.5
                                  : e.target.value === "first_line_chars"
                                    ? 2
                                    : 25,
                      })
                    }
                  >
                    {Object.entries(formatLabels).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>排版目标值</span>
                  {draft.value?.key === "reference_superscript" ? (
                    <select
                      aria-label="排版目标值"
                      value={String(draft.value.expected)}
                      onChange={(e) =>
                        update("value", {
                          ...draft.value,
                          expected: e.target.value === "true",
                        })
                      }
                    >
                      <option value="true">右上标</option>
                      <option value="false">正文行内</option>
                    </select>
                  ) : (
                    <input
                      aria-label="排版目标值"
                      required
                      type={
                        ["font", "latin_font", "heading_font"].includes(
                          draft.value?.key,
                        )
                          ? "text"
                          : "number"
                      }
                      step="any"
                      value={draft.value?.expected ?? ""}
                      onChange={(e) =>
                        update("value", {
                          ...draft.value,
                          expected: [
                            "font",
                            "latin_font",
                            "heading_font",
                          ].includes(draft.value?.key)
                            ? e.target.value
                            : Number(e.target.value),
                        })
                      }
                    />
                  )}
                </label>
              </div>
            )}
            <p className="muted mb">
              文献数量按正文实际引用去重统计；排版检查核对配置，实际版面需另行核对。
            </p>
            <fieldset className="rule-operations">
              <legend>适用写作操作</legend>
              {Object.entries(operationLabels).map(([k, v]) => (
                <label key={k} className="checkbox">
                  <input
                    type="checkbox"
                    checked={draft.operations.includes(k)}
                    onChange={(e) =>
                      update(
                        "operations",
                        e.target.checked
                          ? [...draft.operations, k]
                          : draft.operations.filter((x: string) => x !== k),
                      )
                    }
                  />
                  {v}
                </label>
              ))}
            </fieldset>
            <details className="rule-advanced">
              <summary>附加适用条件</summary>
              <label className="field">
                <span>附加适用条件（JSON，空对象表示不限）</span>
                <textarea
                  aria-label="附加适用条件（JSON，空对象表示不限）"
                  rows={4}
                  value={condition}
                  onChange={(e) => setCondition(e.target.value)}
                />
                <small>
                  {
                    '示例 {"field":"paper_type","op":"eq","value":"software_system"}'
                  }
                </small>
              </label>
            </details>
            <label className="field">
              <span>采用 / 排除依据</span>
              <textarea
                aria-label="采用 / 排除依据"
                rows={2}
                value={draft.decision_note}
                onChange={(e) => update("decision_note", e.target.value)}
                placeholder="记录适用条件、取舍原因或导师的明确说明"
              />
            </label>
            {error && (
              <p className="alert" role="alert">
                {error}
              </p>
            )}
            <div className="actions">
              <button className="btn primary" type="submit">
                {busy
                  ? "正在保存…"
                  : executionOnly
                    ? "保存执行配置"
                    : "保存规则"}
              </button>
              <button className="btn" type="button" onClick={onClose}>
                取消
              </button>
            </div>
          </fieldset>
        </form>
      </section>
    </div>
  );
}

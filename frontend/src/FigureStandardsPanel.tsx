import { useEffect, useState } from "react";
import { api, send } from "./client";
type Row = Record<string, any>;
export default function FigureStandardsPanel({
  pid,
  sectionId,
  onChange,
  disabled = false,
  onPolicyChange,
}: {
  pid: string;
  sectionId?: string;
  onChange: () => void;
  disabled?: boolean;
  onPolicyChange?: (policy: Row) => void;
}) {
  const [policy, setPolicy] = useState<Row | null>(null),
    [types, setTypes] = useState<Row>({}),
    [sections, setSections] = useState<Row[]>([]),
    [assets, setAssets] = useState<Row[]>([]);
  const [links, setLinks] = useState<Row>({ figures: [], paragraphs: [] }),
    [report, setReport] = useState<Row | null>(null),
    [figure, setFigure] = useState(""),
    [paragraph, setParagraph] = useState(""),
    [note, setNote] = useState("");
  const [planSection, setPlanSection] = useState(sectionId ?? ""),
    [planType, setPlanType] = useState("architecture"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const [purpose, setPurpose] = useState("design_schematic"),
    [caption, setCaption] = useState("");
  const load = async () => {
    const [settings, mapping, chapters, artifacts] = await Promise.all([
      api(`/projects/${pid}/figure-standards`),
      api(`/projects/${pid}/figure-links`),
      api(`/projects/${pid}/sections`),
      api(`/projects/${pid}/artifacts`),
    ]);
    setPolicy(settings.policy);
    onPolicyChange?.(settings.policy);
    setTypes(settings.diagram_types);
    setLinks(mapping);
    setSections(chapters.filter((s: Row) => s.state !== "archived"));
    setAssets(artifacts);
  };
  useEffect(() => {
    let alive = true;
    load().catch((e) => {
      if (alive) setError(e.message);
    });
    return () => {
      alive = false;
    };
  }, [pid, sectionId]);
  const perform = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const selected = (links.figures ?? []).find(
    (f: Row) => `${f.section_id}:${f.node_id}` === figure,
  );
  useEffect(() => {
    if (selected) {
      setPurpose(selected.purpose === "unknown" ? "" : selected.purpose);
      setCaption(selected.caption);
    }
  }, [figure, links]);
  const paragraphs = (links.paragraphs ?? []).filter(
    (p: Row) => p.section_id === selected?.section_id,
  );
  const revision = selected?.revision_id;
  return (
    <details className="pv-standards">
      <summary>图文关联与论文配图规范检查</summary>
      <p className="pv-source-note">
        请按学校或学院的正式模板设置配图要求。程序检查编号、图文关联和配图计划；图中业务含义与图片真实性仍需人工核对。
      </p>
      {error && (
        <p role="alert" className="pv-error">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="pv-message">
          {message}
        </p>
      )}
      {policy && (
        <fieldset disabled={busy || disabled}>
          <div className="form-grid">
            <label>
              图表编号方式
              <select
                aria-label="图表编号方式"
                value={policy.numbering}
                onChange={(e) =>
                  setPolicy({ ...policy, numbering: e.target.value })
                }
              >
                <option value="continuous">全文连续编号</option>
                <option value="chapter">按章编号，如图3-1</option>
              </select>
            </label>
            <label>
              AI 图片使用政策
              <select
                aria-label="AI 图片使用政策"
                value={policy.ai_image_policy}
                onChange={(e) =>
                  setPolicy({ ...policy, ai_image_policy: e.target.value })
                }
              >
                <option value="unconfirmed">
                  未确认：仅作草图，正式使用前核对
                </option>
                <option value="allowed_design">
                  已核对允许使用 AI 设计示意图
                </option>
                <option value="forbidden">本项目禁止 AI 生成或修改图片</option>
                <option value="research_method">AI 是研究设计的一部分</option>
              </select>
            </label>
          </div>
          <label className="pv-check">
            <input
              type="checkbox"
              checked={policy.caption_space}
              onChange={(e) =>
                setPolicy({ ...policy, caption_space: e.target.checked })
              }
            />
            图／表与编号之间留空格
          </label>
          <label className="pv-check">
            <input
              type="checkbox"
              checked={policy.include_directory}
              onChange={(e) =>
                setPolicy({ ...policy, include_directory: e.target.checked })
              }
            />
            Word 导出插图目录与表格目录
          </label>
          <label className="pv-check">
            <input
              type="checkbox"
              checked={policy.require_explanation}
              onChange={(e) =>
                setPolicy({ ...policy, require_explanation: e.target.checked })
              }
            />
            检查每张图的图后说明
          </label>
          <label className="pv-check">
            <input
              type="checkbox"
              checked={policy.require_requirement_link}
              onChange={(e) =>
                setPolicy({
                  ...policy,
                  require_requirement_link: e.target.checked,
                })
              }
            />
            检查需求与实现章节关联
          </label>
          <label className="pv-check">
            <input
              type="checkbox"
              checked={policy.blockchain}
              onChange={(e) =>
                setPolicy({ ...policy, blockchain: e.target.checked })
              }
            />
            本项目涉及区块链，要求区分链上与链下
          </label>
          {policy.ai_image_policy === "research_method" && (
            <>
              <label>
                AI 方法章节
                <select
                  aria-label="AI 方法章节"
                  value={policy.method_section_id ?? ""}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      method_section_id: e.target.value || null,
                    })
                  }
                >
                  <option value="">选择已保存的方法章节</option>
                  {sections.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                可复现方法说明
                <textarea
                  aria-label="可复现方法说明"
                  value={policy.method_statement}
                  onChange={(e) =>
                    setPolicy({ ...policy, method_statement: e.target.value })
                  }
                />
              </label>
              <label>
                可复现代码／数据材料
                <select
                  aria-label="可复现材料"
                  multiple
                  value={policy.reproducibility_artifact_ids}
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      reproducibility_artifact_ids: Array.from(
                        e.target.selectedOptions,
                        (x) => x.value,
                      ),
                    })
                  }
                >
                  {assets
                    .filter((a) => a.kind !== "figure" && a.kind !== "table")
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </label>
            </>
          )}
          <label>
            规范来源与核对记录
            <textarea
              aria-label="配图规范来源说明"
              value={policy.source_note}
              onChange={(e) =>
                setPolicy({ ...policy, source_note: e.target.value })
              }
            />
          </label>
          <button
            type="button"
            className="btn"
            onClick={() =>
              perform(async () => {
                const data = await send(
                  `/projects/${pid}/figure-standards`,
                  { policy },
                  "PUT",
                );
                setPolicy(data.policy);
                onPolicyChange?.(data.policy);
                onChange();
                setMessage("本项目配图设置已保存。");
              })
            }
          >
            保存配图规范设置
          </button>
          <h4>章节配图计划</h4>
          <div className="form-grid">
            <label>
              计划章节
              <select
                aria-label="计划配图章节"
                value={planSection}
                onChange={(e) => setPlanSection(e.target.value)}
              >
                <option value="">选择章节</option>
                {sections.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              所需图类型
              <select
                aria-label="计划配图类型"
                value={planType}
                onChange={(e) => setPlanType(e.target.value)}
              >
                {Object.entries(types).map(([key, label]) => (
                  <option key={key} value={key}>
                    {String(label)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <button
            type="button"
            className="btn"
            disabled={!planSection}
            onClick={() =>
              setPolicy({
                ...policy,
                required_diagrams: [
                  ...policy.required_diagrams.filter(
                    (p: Row) =>
                      p.section_id !== planSection || p.kind !== planType,
                  ),
                  { section_id: planSection, kind: planType },
                ],
              })
            }
          >
            加入配图计划（保存后生效）
          </button>
          <ul>
            {policy.required_diagrams.map((p: Row, i: number) => (
              <li key={`${p.section_id}:${p.kind}`}>
                {sections.find((s) => s.id === p.section_id)?.title ??
                  p.section_id}{" "}
                · {String(types[p.kind] ?? p.kind)}{" "}
                <button
                  type="button"
                  className="pv-text-button"
                  onClick={() =>
                    setPolicy({
                      ...policy,
                      required_diagrams: policy.required_diagrams.filter(
                        (_: Row, j: number) => j !== i,
                      ),
                    })
                  }
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      <h4>关联已有图片与说明段落</h4>
      <div className="form-grid">
        <label>
          正文图片
          <select
            aria-label="待关联正文图片"
            value={figure}
            disabled={busy || disabled}
            onChange={(e) => {
              setFigure(e.target.value);
              setParagraph("");
            }}
          >
            <option value="">选择已插入正文的图片</option>
            {(links.figures ?? [])
              .filter((f: Row) => f.kind === "figure")
              .map((f: Row) => (
                <option
                  key={`${f.section_id}:${f.node_id}`}
                  value={`${f.section_id}:${f.node_id}`}
                >
                  {f.section_title} · {f.label} {f.caption}
                </option>
              ))}
          </select>
        </label>
        <label>
          图后说明段落
          <select
            aria-label="图后说明段落"
            value={paragraph}
            disabled={busy || disabled || !selected}
            onChange={(e) => setParagraph(e.target.value)}
          >
            <option value="">选择当前图片所在章节的段落</option>
            {paragraphs.map((p: Row) => (
              <option key={p.node_id} value={p.node_id}>
                {p.text.slice(0, 100)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button
        type="button"
        className="btn"
        disabled={busy || disabled || !selected || !paragraph}
        onClick={() =>
          perform(async () => {
            await send(
              `/sections/${selected.section_id}/figure-links`,
              {
                base_revision_id: revision,
                figure_node_id: selected.node_id,
                paragraph_node_id: paragraph,
              },
              "PUT",
            );
            await load();
            onChange();
            setMessage("图文关联已保存为正文新版本。");
          })
        }
      >
        关联图后说明
      </button>
      {selected && (
        <>
          <div className="form-grid">
            <label>
              本版图题
              <input
                aria-label="本版图题"
                value={caption}
                maxLength={100}
                onChange={(e) => setCaption(e.target.value)}
              />
            </label>
            <label>
              本版图片用途
              <select
                aria-label="本版图片用途"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
              >
                <option value="">选择实际图片用途</option>
                <option value="design_schematic">设计示意图</option>
                <option value="original_result">原创结果图</option>
                <option value="experimental_result">实验结果图</option>
                <option value="interface_screenshot">真实界面截图</option>
                <option value="test_capture">真实测试日志</option>
                <option value="ai_method_output">AI 研究方法产物</option>
              </select>
            </label>
          </div>
          <button
            type="button"
            className="btn"
            disabled={busy || disabled}
            onClick={() =>
              perform(async () => {
                await send(
                  `/sections/${selected.section_id}/figures/${selected.node_id}/metadata`,
                  { revision_id: revision, figure_purpose: purpose, caption },
                  "PUT",
                );
                await load();
                onChange();
                setMessage("当前正文中的图题与用途已更新，原始图片文件保留。");
              })
            }
          >
            保存本版图题与用途
          </button>
        </>
      )}
      <label>
        本版图文核对依据
        <textarea
          aria-label="图文核对依据"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="核对元素、关系、说明、模块名称和实际来源；如为结果图，说明原始产物出处。"
        />
      </label>
      <button
        type="button"
        className="btn"
        disabled={busy || disabled || !selected || note.trim().length < 6}
        onClick={() =>
          perform(async () => {
            await send(
              `/sections/${selected.section_id}/figures/${selected.node_id}/review`,
              { revision_id: revision, note },
            );
            setReport(await api(`/projects/${pid}/figure-checks`));
            onChange();
            setMessage(
              "已记录用户对当前版本的图文核对；后续修改后需重新确认。",
            );
          })
        }
      >
        确认本版图文已核对
      </button>
      <div className="pv-actions">
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() =>
            perform(async () => {
              await load();
              setReport(await api(`/projects/${pid}/figure-checks`));
            })
          }
        >
          检查当前论文配图
        </button>
        <span>程序确定的缺口与待人工核对项分别显示。</span>
      </div>
      {report && (
        <div className="pv-check-report" role="status">
          <p>
            待完善 {report.summary.fail} 项 · 待核对 {report.summary.unknown} 项
            · 已核对 {report.summary.pass} 项
          </p>
          {report.items.length ? (
            <ul>
              {report.items.map((r: Row, i: number) => (
                <li key={i}>
                  <strong>
                    {r.label}{" "}
                    {r.result === "fail"
                      ? "待完善"
                      : r.result === "pass"
                        ? "已核对"
                        : "待人工"}
                  </strong>{" "}
                  · {r.message}
                </li>
              ))}
            </ul>
          ) : (
            <p>
              当前没有检测到图表缺口；这不自动证明图片内容真实或符合所有学校细则。
            </p>
          )}
        </div>
      )}
    </details>
  );
}

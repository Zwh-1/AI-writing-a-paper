import { useEffect, useState } from "react";
import { api, send } from "../client";
type Row = Record<string, any>;

export default function ProjectMemory({ pid, disabled = false, sectionId, sections }: { pid: string; disabled?: boolean; sectionId?: string; sections: { id: string; title: string }[] }) {
  const [data, setData] = useState<Row | null>(null), [skills, setSkills] = useState<Row | null>(null);
  const [kind, setKind] = useState("author_decision"), [subject, setSubject] = useState(""), [text, setText] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [maintenance, setMaintenance] = useState<Row | null>(null);
  const [preview, setPreview] = useState<Row | null>(null);
  const [contextSectionId, setContextSectionId] = useState(sectionId ?? ""), [bindToSection, setBindToSection] = useState(false);
  const [editing, setEditing] = useState<Row | null>(null), [supersedes, setSupersedes] = useState<string[]>([]);
  const [offset, setOffset] = useState(0);
  const [comparisonIds, setComparisonIds] = useState(""), [comparison, setComparison] = useState<Row | null>(null);
  const [experimentBudget, setExperimentBudget] = useState(""), [experimentInstruction, setExperimentInstruction] = useState("");
  const clearForm = () => { setEditing(null); setSupersedes([]); setSubject(""); setText(""); setBindToSection(false); };
  useEffect(() => { if (!sections.some(section => section.id === contextSectionId)) setContextSectionId(sectionId ?? sections[0]?.id ?? ""); }, [sections, sectionId, contextSectionId]);
  useEffect(() => setPreview(null), [contextSectionId]);
  const load = async () => { const [memory, catalogue] = await Promise.all([api(`/projects/${pid}/memory?offset=${offset}`), api(`/projects/${pid}/skills`)]); setData(memory); setSkills(catalogue); };
  useEffect(() => { let alive = true; setData(null); Promise.all([api(`/projects/${pid}/memory?offset=${offset}`), api(`/projects/${pid}/skills`)]).then(([memory, catalogue]) => { if (alive) { setData(memory); setSkills(catalogue); } }).catch(e => { if (alive) setError(e.message); }); return () => { alive = false; }; }, [pid, offset]);
  const act = async (fn: () => Promise<unknown>, message: string) => { if (busy) return; setBusy(true); setError(""); setNotice(""); try { await fn(); await load(); setNotice(message); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  const labels: Row = { author_decision: "作者决定", working_hypothesis: "工作假设", open_question: "待解问题", chapter_navigation: "章节导航" };
  return <details className="studio-card"><summary>项目记忆与技能</summary>
    <p>保存需要跨任务保留的决定和问题。章节导航关联正文版本，正文变化后会标为过期。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {data && <fieldset disabled={busy || disabled}>
      <label>上下文章节<select value={contextSectionId} onChange={e => setContextSectionId(e.target.value)}>{!sections.length && <option value="">尚无章节</option>}{sections.map(section => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label>
      <button className="btn" type="button" disabled={!contextSectionId} onClick={() => void act(async () => setPreview(await api(`/projects/${pid}/context-preview?section_id=${encodeURIComponent(contextSectionId)}`)), "上下文预览已更新")}>预览所选章上下文</button>
      {preview && <p role="status">完整读取 {preview.provided_section_ids.length} 章，其他 {preview.catalogue_section_ids.length} 章提供导航；载入 {preview.memory.items.length} 条记忆。衔接层输入保守估计 {preview.input_upper_bound.toLocaleString()}，实际写作还需加入规则和证据。</p>}
      {preview && !!preview.memory.deferred?.length && <p>有 {preview.memory.deferred.length} 条记忆因版本、来源权限或输入预算未载入，可核对记忆状态后重新准备任务。</p>}
      <label><input type="checkbox" checked={data.policy.enabled} onChange={e => void act(() => send(`/projects/${pid}/memory/policy`, { enabled: e.target.checked }, "PUT"), "记忆设置已保存，应用于新任务")} />新任务使用项目记忆</label>
      <div className="form-grid">
        <label>记忆输入上限（字节）<input type="number" min={1000} max={32000} value={data.policy.max_input_bytes} onChange={e => setData({ ...data, policy: { ...data.policy, max_input_bytes: Number(e.target.value) } })} /></label>
        <label>对话保留消息数<input type="number" min={4} max={80} value={data.policy.recent_messages} onChange={e => setData({ ...data, policy: { ...data.policy, recent_messages: Number(e.target.value) } })} /></label>
        <label>对话输入上限（字节）<input type="number" min={2000} max={64000} value={data.policy.history_input_bytes} onChange={e => setData({ ...data, policy: { ...data.policy, history_input_bytes: Number(e.target.value) } })} /></label>
      </div>
      <button className="btn" type="button" onClick={() => void act(() => send(`/projects/${pid}/memory/policy`, data.policy, "PUT"), "记忆设置已保存，应用于新任务")}>保存记忆设置</button>
      <button className="btn" type="button" onClick={() => void act(() => send(`/projects/${pid}/memory/rebuild-chapters`, {}), "章节导航已更新，未调用模型")}>重建章节导航</button>
      {!!data.conflicts.length && <p role="alert">有 {data.conflicts.length} 组作者决定主题相同，请核对并明确替代旧决定。</p>}
      <form onSubmit={e => { e.preventDefault(); void act(async () => { await send(`/projects/${pid}/memory`, { kind, subject, text, supersedes, ...(editing ? { id: editing.id, expected_revision: editing.revision } : bindToSection && contextSectionId ? { section_id: contextSectionId } : {}) }); clearForm(); }, editing ? "记忆新版本已保存，旧版本保留" : "项目记忆已保存"); }}>
        {editing && <p role="status">正在编辑「{editing.data.subject}」第 {editing.revision} 版，来源与章节版本关联继续保留。</p>}
        <label>记忆类型<select value={kind} onChange={e => { setKind(e.target.value); setSupersedes([]); }}>{Object.entries(labels).filter(([id]) => id !== "chapter_navigation").map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        <label>主题<input required maxLength={200} value={subject} onChange={e => { setSubject(e.target.value); setSupersedes([]); }} /></label>
        <label>内容<textarea required maxLength={6000} rows={3} value={text} onChange={e => setText(e.target.value)} /></label>
        {!editing && <label><input type="checkbox" disabled={!contextSectionId} checked={bindToSection} onChange={e => setBindToSection(e.target.checked)} />关联所选章节的当前正文版本</label>}
        {kind === "author_decision" && data.entries.filter((row: Row) => row.id !== editing?.id && row.state === "current" && row.data.memory_kind === kind && row.data.subject === subject.trim()).map((row: Row) => <label key={row.id}><input type="checkbox" checked={supersedes.includes(row.id)} onChange={e => setSupersedes(e.target.checked ? [...supersedes, row.id] : supersedes.filter(id => id !== row.id))} />明确替代旧决定：{row.data.text}</label>)}
        <button className="btn primary" type="submit">{editing ? "保存记忆新版本" : "保存项目记忆"}</button>
        {(editing || supersedes.length > 0) && <button className="btn" type="button" onClick={clearForm}>取消编辑</button>}
      </form>
      <ul>{data.entries.map((row: Row) => <li key={row.id}><strong>{row.data.subject}</strong> · {labels[row.data.memory_kind]} · {({ current: "当前", stale: "已过期", unavailable: "来源不可用", superseded: "已替代" } as Row)[row.state]}<p>{row.data.text}</p>{row.state === "current" && row.data.memory_kind !== "chapter_navigation" && <button type="button" className="btn" onClick={() => { setEditing(row); setKind(row.data.memory_kind); setSubject(row.data.subject); setText(row.data.text); setSupersedes([]); }}>编辑记忆</button>}<button type="button" className="btn" onClick={() => void act(async () => { await send(`/projects/${pid}/memory/${row.id}?expected_revision=${row.revision}`, {}, "DELETE"); if (editing?.id === row.id) clearForm(); }, "记忆已移除，历史版本保留")}>移除记忆</button></li>)}</ul>
      <p>共 {data.total} 条记忆，本页 {data.entries.length} 条；上下文检索会在全部有效记忆中选择。</p>
      <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - data.limit))}>上一页记忆</button>
      <button type="button" className="btn" disabled={offset + data.limit >= data.total} onClick={() => setOffset(offset + data.limit)}>下一页记忆</button>
    </fieldset>}
    {skills && <fieldset disabled={busy || disabled}><legend>任务技能</legend>
      <label><input type="checkbox" checked={skills.policy.enabled} onChange={e => setSkills({ ...skills, policy: { ...skills.policy, enabled: e.target.checked } })} />启用技能</label>
      <label><input type="checkbox" checked={skills.policy.automatic} onChange={e => setSkills({ ...skills, policy: { ...skills.policy, automatic: e.target.checked } })} />按操作自动选择</label>
      {skills.catalogue.map((skill: Row) => <label key={skill.id}><input type="checkbox" checked={skills.policy.selected_ids.includes(skill.id)} onChange={e => setSkills({ ...skills, policy: { ...skills.policy, selected_ids: e.target.checked ? [...skills.policy.selected_ids, skill.id] : skills.policy.selected_ids.filter((id: string) => id !== skill.id) } })} />{skill.description}</label>)}
      <button className="btn" type="button" onClick={() => void act(() => send(`/projects/${pid}/skills/policy`, skills.policy, "PUT"), "技能选择已保存，应用于新任务")}>保存技能设置</button>
    </fieldset>}
    <details><summary>全文与分层模式效果对照</summary><p>填入同一份正文、来源和记忆快照下生成的任务编号，每行一个。只读取已有记录，不调用模型；未记录的用量和人工质量指标保留为空。</p>
      <details><summary>发起真实模型配对实验</summary><p>针对所选章节分别生成全文和分层候选，使用项目已有预算，会产生模型费用。不会自动采用候选；正文或来源变化后停止，两个任务共同受本次费用上限约束。</p>
        <label>本次两个任务的总费用上限（项目币种）<input type="number" min="0.001" step="0.001" value={experimentBudget} onChange={e => setExperimentBudget(e.target.value)} /></label>
        <label>相同写作指令<textarea value={experimentInstruction} onChange={e => setExperimentInstruction(e.target.value)} /></label>
        <button className="btn" type="button" disabled={busy || disabled || !contextSectionId || !(Number(experimentBudget) > 0)} onClick={() => void act(async () => { const value = await send(`/ai-runtime/projects/${pid}/context-experiments`, { section_id: contextSectionId, budget_limit: Number(experimentBudget), instruction: experimentInstruction }); setComparisonIds(value.task_ids.join("\n")); }, "已创建两个受总预算约束的写作任务，完成后可读取对照")}>按此上限开始两个模型任务</button>
      </details>
      <label>对照任务编号<textarea rows={3} value={comparisonIds} onChange={e => setComparisonIds(e.target.value)} /></label>
      <button className="btn" type="button" disabled={busy || disabled} onClick={() => void act(async () => setComparison(await send(`/ai-runtime/projects/${pid}/compare`, { task_ids: comparisonIds.split(/[\s,，]+/).filter(Boolean), comparison_axis: "context_mode" })), "已读取上下文对照记录，未调用模型")}>对照已有任务</button>
      {comparison && <><p>{comparison.paired_input_groups.filter((row: Row) => row.ready_for_human_comparison).length} 组具备人工对照条件；来源、模型或设置不一致的任务不会配为一组。</p><table><thead><tr><th>任务</th><th>模式</th><th>接口上报输入 token</th><th>接口上报输出 token</th></tr></thead><tbody>{comparison.tasks.map((row: Row) => <tr key={row.task_id}><td>{row.task_id}</td><td>{({ full_manuscript: "全文", hierarchical_v1: "分层" } as Row)[row.group] ?? "未知"}</td><td>{row.context_observations.input_tokens ?? "未记录"}</td><td>{row.context_observations.output_tokens ?? "未记录"}</td></tr>)}</tbody></table><p>引用支持、遗漏和作者修改量需人工评定；输入减少不代表语义质量已经提高。</p></>}
      {comparison && <form onSubmit={e => { e.preventDefault(); const fields = new FormData(e.currentTarget); const row = comparison.tasks.find((item: Row) => item.task_id === fields.get("task_id")); if (!row) return; void act(async () => { await send(`/ai-runtime/projects/${pid}/context-assessments`, { task_id: row.task_id, result_hash: row.result_hash, assessor: fields.get("assessor"), supported_citations: Number(fields.get("supported_citations")), checked_citations: Number(fields.get("checked_citations")), omission_count: Number(fields.get("omission_count")), author_edit_characters: Number(fields.get("author_edit_characters")), notes: fields.get("notes") }); setComparison(await send(`/ai-runtime/projects/${pid}/compare`, { task_ids: comparison.tasks.map((item: Row) => item.task_id), comparison_axis: "context_mode" })); }, "人工评价已绑定当前候选版本保存"); }}>
        <label>人工评价任务<select name="task_id">{comparison.tasks.filter((row: Row) => row.status === "succeeded").map((row: Row) => <option key={row.task_id} value={row.task_id}>{row.task_id}</option>)}</select></label>
        <label>评价者<input name="assessor" required maxLength={200} /></label>
        <label>核对引用数<input name="checked_citations" type="number" min={0} required /></label><label>原文支持的引用数<input name="supported_citations" type="number" min={0} required /></label>
        <label>遗漏项数<input name="omission_count" type="number" min={0} required /></label><label>作者实际修改字符数<input name="author_edit_characters" type="number" min={0} required /></label>
        <label>评价说明<textarea name="notes" maxLength={8000} /></label><button className="btn" type="submit" disabled={busy || disabled}>保存人工评价</button>
        <ul>{comparison.tasks.filter((row: Row) => row.human_assessment).map((row: Row) => <li key={row.task_id}>{row.task_id}：引用支持率 {row.context_observations.human_citation_support_rate == null ? "无已核对引用" : `${Math.round(row.context_observations.human_citation_support_rate * 100)}%`}，遗漏 {row.context_observations.human_omission_count} 项，修改 {row.context_observations.human_edit_amount} 字符。</li>)}</ul>
      </form>}
    </details>
    <details><summary>上下文运行记录</summary><p>可归档至少 30 天前已完成且没有未核实请求的运行记录。项目记忆、正文、来源和费用账本继续保留。</p>
      <button className="btn" disabled={busy || disabled} type="button" onClick={() => void act(async () => setMaintenance(await api(`/projects/${pid}/context-maintenance`)), "已读取归档范围")}>查看可归档记录</button>
      {maintenance && <><p>可归档 {maintenance.task_ids.length} 个任务。</p><button className="btn" disabled={busy || disabled || !maintenance.task_ids.length} type="button" onClick={() => void act(async () => { await send(`/projects/${pid}/context-maintenance/archive`, { keep_days: 30 }); setMaintenance(await api(`/projects/${pid}/context-maintenance`)); }, "运行记录已归档，可随时恢复")}>归档已完成任务记录</button>
      <ul>{maintenance.archives.map((row: Row) => <li key={row.id}>{row.created_at} · {row.tasks} 个任务 <button className="btn" disabled={busy || disabled || row.restored} type="button" onClick={() => void act(async () => { await send(`/projects/${pid}/context-maintenance/restore/${row.id}`, {}); setMaintenance(await api(`/projects/${pid}/context-maintenance`)); }, "归档记录已恢复")}>{row.restored ? "已恢复" : "恢复记录"}</button></li>)}</ul></>}
    </details>
  </details>;
}

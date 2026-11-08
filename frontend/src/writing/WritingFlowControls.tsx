import { useEffect, useMemo, useState, type FormEvent } from "react";
import { GitBranch, LoaderCircle, RefreshCw, Save, Settings2 } from "lucide-react";
import { writingApi, type WritingArgumentCard, type WritingArgumentPlan, type WritingFlowSettings } from "../api/writing";
import "./evidenceTrace.css";
import ProjectMemory from "./ProjectMemory";

const numericSettings: { key: keyof WritingFlowSettings; label: string; min: number; max: number; step?: number }[] = [
  { key: "unit_target_words", label: "每个论证单元的目标字数", min: 400, max: 1800, step: 100 },
  { key: "unit_threshold_words", label: "超过多少字分单元写作", min: 1200, max: 20000, step: 100 },
  { key: "max_material_chars", label: "单轮取材字数上限", min: 2000, max: 100000, step: 1000 },
  { key: "max_revision_rounds", label: "定向修订轮数上限", min: 0, max: 2 },
  { key: "max_research_rounds", label: "补证调查轮数上限", min: 1, max: 6 },
  { key: "max_tool_calls", label: "补证读取次数上限", min: 1, max: 24 },
];
const cardRoles = [
  ["claim_and_analysis", "主张与分析"], ["evidence_and_interpretation", "证据与解释"],
  ["method_and_rationale", "方法与理由"], ["definition", "定义与范围"], ["limitations", "条件与局限"],
];

export default function WritingFlowControls({ pid, sections, disabled = false, onChanged }: {
  pid: string;
  sections: { id: string; title: string }[];
  disabled?: boolean;
  onChanged?: () => void;
}) {
  const service = useMemo(() => writingApi(pid), [pid]);
  const [open, setOpen] = useState(false), [tab, setTab] = useState<"settings" | "plan">("settings");
  const [settings, setSettings] = useState<WritingFlowSettings | null>(null), [settingsEpoch, setSettingsEpoch] = useState(0);
  const [settingsLoading, setSettingsLoading] = useState(false), [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState(""), [settingsNotice, setSettingsNotice] = useState("");
  const [sectionId, setSectionId] = useState(""), [plan, setPlan] = useState<WritingArgumentPlan | null>(null);
  const [cards, setCards] = useState<WritingArgumentCard[]>([]), [note, setNote] = useState("");
  const [planLoading, setPlanLoading] = useState(false), [planSaving, setPlanSaving] = useState(false), [planEpoch, setPlanEpoch] = useState(0);
  const [planError, setPlanError] = useState(""), [planNotice, setPlanNotice] = useState("");

  useEffect(() => {
    setSettings(null); setSettingsError(""); setSettingsNotice(""); setPlan(null); setCards([]); setNote(""); setPlanError(""); setPlanNotice("");
  }, [pid]);
  useEffect(() => {
    if (!sections.some((section) => section.id === sectionId)) setSectionId(sections[0]?.id ?? "");
  }, [sections, sectionId]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setSettingsLoading(true); setSettingsError("");
    service.settings().then((value) => { if (active) setSettings(value); })
      .catch((error) => { if (active) setSettingsError((error as Error).message); })
      .finally(() => { if (active) setSettingsLoading(false); });
    return () => { active = false; };
  }, [service, open, settingsEpoch]);
  useEffect(() => {
    if (!open || tab !== "plan" || !sectionId) return;
    let active = true;
    setPlanLoading(true); setPlanError(""); setPlanNotice(""); setPlan(null);
    service.plan(sectionId).then((value) => {
      if (!active) return;
      setPlan(value); setCards(value.paragraph_cards ?? []); setNote(value.author_note ?? "");
    }).catch((error) => { if (active) setPlanError((error as Error).message); })
      .finally(() => { if (active) setPlanLoading(false); });
    return () => { active = false; };
  }, [service, open, tab, sectionId, planEpoch]);

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!settings || settingsSaving || disabled) return;
    setSettingsSaving(true); setSettingsError(""); setSettingsNotice("");
    try {
      setSettings(await service.saveSettings(settings));
      setSettingsNotice("已保存。新任务将固定使用这组设置，暂停后续跑也沿用。"); onChanged?.();
    } catch (error) { setSettingsError((error as Error).message); }
    finally { setSettingsSaving(false); }
  };
  const savePlan = async (event: FormEvent) => {
    event.preventDefault();
    if (!plan || planSaving || disabled || !cards.length) return;
    setPlanSaving(true); setPlanError(""); setPlanNotice("");
    try {
      const updated = await service.savePlan(sectionId, { base_hash: plan.hash, paragraph_cards: cards, author_note: note });
      setPlan(updated); setCards(updated.paragraph_cards); setPlanNotice("本章论证计划已保存，起草时将按这些段落任务组织内容。"); onChanged?.();
    } catch (error) { setPlanError((error as Error).message); }
    finally { setPlanSaving(false); }
  };
  const updateCard = (index: number, patch: Partial<WritingArgumentCard>) => {
    setCards((current) => current.map((card, at) => at === index ? { ...card, ...patch } : card));
    setPlanNotice("");
  };
  return <details className="writing-flow-controls" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><Settings2 size={16} />写作流程与论证计划<span>高级设置</span></summary>
    <div className="writing-flow-body">
      <div className="writing-flow-tabs" role="tablist" aria-label="写作增强设置">
        <button className={tab === "settings" ? "active" : ""} role="tab" aria-selected={tab === "settings"} onClick={() => setTab("settings")} type="button"><Settings2 size={14} />流程设置</button>
        <button className={tab === "plan" ? "active" : ""} role="tab" aria-selected={tab === "plan"} onClick={() => setTab("plan")} type="button"><GitBranch size={14} />逐章论证计划</button>
      </div>
      {tab === "settings" ? <div role="tabpanel" aria-label="写作流程设置">
        {settingsLoading && <p className="writing-flow-loading" role="status"><LoaderCircle size={15} className="spin" />正在读取写作流程设置…</p>}
        {settingsError && <div className="writing-flow-error" role="alert"><p>{settingsError}</p><button type="button" className="btn" onClick={() => setSettingsEpoch((value) => value + 1)} disabled={settingsSaving}>重新载入设置</button></div>}
        {settings && !settingsLoading && <form onSubmit={(event) => void saveSettings(event)}>
          <fieldset disabled={disabled || settingsSaving}>
            <label className="writing-flow-toggle"><input type="checkbox" checked={settings.enabled} onChange={(event) => { setSettings({ ...settings, enabled: event.target.checked }); setSettingsNotice(""); }} /><span><strong>启用素材驱动写作</strong><small>按论证任务检索素材、起草并保留来源与待补范围。</small></span></label>
            <label className="writing-flow-toggle"><input type="checkbox" checked={settings.semantic_review} onChange={(event) => { setSettings({ ...settings, semantic_review: event.target.checked }); setSettingsNotice(""); }} /><span><strong>独立核对来源与论证</strong><small>生成后增加模型核验请求，检查具体主张与原文、条件和推理关系。</small></span></label>
            <label className="writing-flow-toggle"><input type="checkbox" checked={settings.originality_check} onChange={(event) => { setSettings({ ...settings, originality_check: event.target.checked }); setSettingsNotice(""); }} /><span><strong>检查重复与表达原创性</strong><small>标出无新增信息的重复和过近表述，保留必要术语与规范引用。</small></span></label>
            <label className="field"><span>论文上下文范围</span><select aria-label="论文上下文范围" value={settings.context_mode} onChange={(event) => { setSettings({ ...settings, context_mode: event.target.value as WritingFlowSettings["context_mode"] }); setSettingsNotice(""); }}><option value="full_manuscript">通读全部已保存正文</option><option value="argument_scoped">按本章论证依赖读取</option><option value="hierarchical_v1">分层上下文与项目记忆</option></select></label>
            <p className="writing-trace-note">{settings.context_mode !== "full_manuscript" ? "将完整读取本章依赖的正文，其他章节只保留导航信息；核验记录会明确实际读取范围。" : "全文模式保留全部已保存正文；超过接口上下文预算时会提示调整范围。"}</p>
            <details className="writing-flow-limits"><summary>篇幅、补证与修订上限</summary><div className="writing-flow-number-grid">{numericSettings.map((field) => <label className="field" key={field.key}><span>{field.label}</span><input type="number" required aria-label={field.label} min={field.min} max={field.max} step={field.step ?? 1} value={Number(settings[field.key])} onChange={(event) => { setSettings({ ...settings, [field.key]: Number(event.target.value) }); setSettingsNotice(""); }} /></label>)}</div></details>
            <p className="writing-trace-note">独立核验、补证和修订可能增加模型费用，仍受本次写作总预算限制。</p>
            <button className="btn primary" type="submit"><Save size={14} />{settingsSaving ? "正在保存…" : "保存写作流程设置"}</button>
          </fieldset>
          {settingsNotice && <p className="writing-flow-success" role="status">{settingsNotice}</p>}
        </form>}
      </div> : <div role="tabpanel" aria-label="逐章论证计划">
        <label className="field"><span>选择章节</span><select aria-label="论证计划章节" disabled={disabled || planSaving || !sections.length} value={sectionId} onChange={(event) => setSectionId(event.target.value)}>{!sections.length && <option value="">尚无章节</option>}{sections.map((section) => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label>
        {!sections.length && <p className="writing-trace-note">建立提纲后可逐章核对论证计划。</p>}
        {planLoading && <p className="writing-flow-loading" role="status"><LoaderCircle size={15} className="spin" />正在按当前任务与材料组织段落计划…</p>}
        {planError && <div className="writing-flow-error" role="alert"><p>{planError}</p><button type="button" className="btn" onClick={() => setPlanEpoch((value) => value + 1)} disabled={planSaving}><RefreshCw size={14} />重新载入本章计划</button></div>}
        {plan && !planLoading && <form onSubmit={(event) => void savePlan(event)}>
          <fieldset disabled={disabled || planSaving}>
            {(plan.research_question || plan.section_goal) && <p className="writing-flow-goal">{plan.research_question && <span>研究问题：{plan.research_question}</span>}{plan.section_goal && <span>本章目标：{plan.section_goal}</span>}</p>}
            <p className="writing-trace-note">逐段写清要回答的问题和论证职责。修改计划后仍会按当前原文核对依据，计划本身不证明结论。</p>
            {cards.length ? <ol className="writing-flow-card-editor">{cards.map((card, index) => <li key={card.id}><strong>段落任务 {index + 1}</strong><label className="field"><span>本段应回答的问题</span><textarea aria-label={`段落任务${index + 1}的问题`} rows={2} required minLength={1} maxLength={3000} value={card.question} onChange={(event) => updateCard(index, { question: event.target.value })} /></label><label className="field"><span>论证职责</span><select aria-label={`段落任务${index + 1}的职责`} value={card.role} onChange={(event) => updateCard(index, { role: event.target.value })}>{!cardRoles.some(([value]) => value === card.role) && <option value={card.role}>{card.role}</option>}{cardRoles.map(([value, text]) => <option value={value} key={value}>{text}</option>)}</select></label>{!!card.gaps?.length && <p className="writing-card-gap">{card.gaps.join("；")}</p>}</li>)}</ol> : <p className="writing-trace-note">当前章节尚未形成段落任务，请先完善章节目标与写作任务。</p>}
            <label className="field"><span>作者补充说明</span><textarea aria-label="论证计划补充说明" rows={3} maxLength={6000} value={note} onChange={(event) => { setNote(event.target.value); setPlanNotice(""); }} placeholder="说明论证顺序、要保留的判断与需要补充的依据。" /></label>
            <button type="submit" className="btn primary" disabled={!cards.length || cards.some((card) => !card.question.trim())}><Save size={14} />{planSaving ? "正在保存…" : "保存本章论证计划"}</button>
          </fieldset>
          {planNotice && <p className="writing-flow-success" role="status">{planNotice}</p>}
        </form>}
      </div>}
    </div>
    <ProjectMemory key={pid} pid={pid} disabled={disabled} sectionId={sectionId} sections={sections} />
  </details>;
}

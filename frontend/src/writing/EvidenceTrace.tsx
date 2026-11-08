import { BookOpen, CheckCircle2, FileSearch, GitBranch, ShieldCheck } from "lucide-react";
import ProductLink from "../products/ProductLink";
import type { Row } from "../workspace/types";
import "./evidenceTrace.css";

const array = (value: unknown): Row[] => Array.isArray(value) ? value.filter((row) => row && typeof row === "object") : [];
const groupNames: Record<string, string> = { primary_evidence: "论据原文", guidance: "规范与写作示例", derived_context: "笔记与派生材料", constraints: "适用论文规则" };
const purposes: Record<string, string> = {
  research_literature: "研究文献", course_guidance: "课程规范", grading_criteria: "评分要求",
  writing_example: "写作示例", writing_guidance: "写作指导", literature: "研究文献",
  code_observation: "源码观察", test_definition: "测试定义", test_run: "运行记录", deployment: "部署记录",
  data_result: "研究结果记录", author_statement: "作者说明", guidance_observation: "写作指导",
  material_observation: "资料原文", fact: "已登记研究事实", analysis: "统计分析", note: "研究笔记",
  asset: "研究资产", summary: "资料总结", execution: "执行产物", rule: "论文规则",
};
const roles: Record<string, string> = { claim_and_analysis: "提出主张并分析", evidence_and_interpretation: "说明证据与结果", limitations: "解释局限", definition: "界定范围", method_and_rationale: "说明方法与理由" };
const relations: Record<string, string> = { supports: "支持前段", requires: "以前段为前提", derives: "推导结论", contrasts: "对比前段", limits: "限定前段", explains: "继续解释" };
const statusLabel = (value: unknown) => ({ checked: "已完成辅助检查", needs_review: "仍需核对", incomplete: "检查未覆盖全部范围", not_checked: "尚未检查", not_applicable: "本阶段无适用主张", issues: "发现待核对项", failed: "检查未完成" }[String(value)] ?? "检查记录已保存");
const keyOf = (row: Row) => row.source_ref ? `${row.source_ref.type}:${row.source_ref.id}` : `${row.kind}:${row.id}`;

function locationText(locator: Row | undefined) {
  if (!locator) return "";
  const parts: string[] = [];
  if (locator.path || locator.relative_path) parts.push(String(locator.path || locator.relative_path));
  if (locator.page || locator.physical_page || locator.page_number) parts.push(`第 ${locator.page || locator.physical_page || locator.page_number} 页`);
  if (locator.slide_number) parts.push(`第 ${locator.slide_number} 张幻灯片`);
  if (locator.line_start) parts.push(`第 ${locator.line_start}${locator.line_end && locator.line_end !== locator.line_start ? `–${locator.line_end}` : ""} 行`);
  if (locator.sheet || locator.sheet_name) parts.push(String(locator.sheet || locator.sheet_name));
  if (locator.cell_range) parts.push(String(locator.cell_range));
  if (locator.char_start !== undefined && locator.char_end !== undefined) parts.push(`字符 ${locator.char_start}–${locator.char_end}`);
  return parts.join(" · ");
}

function usedEntry(row: Row, bundle: Row) {
  const use = bundle.usage;
  const ref = row.source_ref;
  if (!use || !ref) return false;
  return array(use.resource_refs).some((r) => r.type === ref.type && r.id === ref.id) ||
    (ref.type === "source" && (use.source_ids ?? []).includes(ref.id)) ||
    (ref.type === "unit" && (use.knowledge_unit_ids ?? []).includes(ref.id)) ||
    (ref.type === "evidence" && (use.evidence_ids ?? []).includes(ref.id)) ||
    (row.reference_ids ?? []).some((id: string) => (use.reference_ids ?? []).includes(id)) ||
    array(bundle.retrieval_trace).some((trace) => trace.actual_use && trace.source_ref?.type === ref.type && trace.source_ref?.id === ref.id);
}

function SourceEntry({ pid, sectionId, row, used, group }: { pid: string; sectionId?: string; row: Row; used: boolean; group: string }) {
  const ref = row.source_ref;
  const location = locationText(row.locator ?? ref?.locator);
  const purpose = purposes[row.material_use?.source_purpose] ?? purposes[row.kind] ?? (group === "derived_context" ? "派生材料，仍需核对" : groupNames[group]);
  return <article className="writing-source-entry">
    <div className="writing-source-top"><strong>{row.title || purpose}</strong><span className={used ? "writing-trace-tag used" : "writing-trace-tag"}>{used ? "已用于本稿" : group === "constraints" ? "写作约束" : "已读，未关联本稿"}</span></div>
    <p className="writing-source-location">{purpose}{location ? ` · ${location}` : ""}</p>
    {!!row.conditions?.length && <p className="writing-source-conditions">适用条件：{row.conditions.map((value: unknown) => typeof value === "string" ? value : JSON.stringify(value)).join("；")}</p>}
    <div className="writing-source-actions">
      {ref?.type && ref?.id && <ProductLink pid={pid} sectionId={sectionId} kind={ref.type} identity={ref.id} label="打开来源" />}
      {row.text && <details className="writing-source-text"><summary>查看本次读取的原文</summary><blockquote>{row.text}</blockquote></details>}
    </div>
  </article>;
}

export default function EvidenceTrace({ pid, sectionId, bundle, quality, argumentPlan, plan, coverage, compact = false }: {
  pid: string;
  sectionId?: string;
  bundle?: Row | null;
  quality?: Row | null;
  argumentPlan?: Row | null;
  plan?: Row | null;
  coverage?: Row | null;
  compact?: boolean;
}) {
  argumentPlan = argumentPlan ?? plan;
  if (!bundle && !quality && !argumentPlan) return null;
  const entries = bundle ? ["primary_evidence", "guidance", "derived_context"].flatMap((group) => array(bundle[group])) : [];
  const read = new Set(entries.map(keyOf)).size;
  const used = bundle ? new Set(entries.filter((row) => usedEntry(row, bundle)).map(keyOf)).size : 0;
  const usageRecorded = bundle?.usage && bundle.usage.recorded !== false;
  const searches = array(bundle?.retrieval_trace).filter((row) => row.action === "search");
  const hits = searches.length ? Math.max(...searches.map((row) => Number(row.candidates ?? row.total_candidates ?? row.source_refs?.length ?? 0))) : undefined;
  const gaps = Array.isArray(bundle?.gaps) ? bundle.gaps : [];
  const findings = array(quality?.findings);
  const repairs = array(quality?.repair_rounds);
  const cards = array(argumentPlan?.paragraph_cards);
  const obligations = array(quality?.obligations?.obligations);
  const supportRecords = array(quality?.claim_support_view?.items);
  const supportLabels: Record<string, string> = { supports: '支持', qualifies: '部分支持或有限定', contradicts: '存在矛盾', unresolved: '尚未确认' };
  const contextCoverage = coverage ?? quality?.context_coverage;
  const sourceGroups = bundle ? Object.keys(groupNames).filter((key) => array(bundle[key]).length) : [];
  return <details className={`writing-evidence-trace${compact ? " compact" : ""}`} aria-label="写作素材与核验轨迹">
    <summary><span><FileSearch size={16} />素材与写作核验</span><span className="writing-trace-caption">{bundle ? `已读 ${read} 项 · ${usageRecorded ? `已用 ${used} 项` : "使用情况尚未登记"}` : statusLabel(quality?.status)}{gaps.length ? ` · 待补 ${gaps.length} 项` : ""}</span></summary>
    <div className="writing-trace-body">
      {bundle && <>
        <dl className="writing-trace-stats" aria-label="素材检索与使用统计">
          <div><dt>检索命中</dt><dd>{hits ?? "—"}<small>项候选</small></dd></div>
          <div><dt>实际读取</dt><dd>{read}<small>项素材</small></dd></div>
          <div><dt>用于本稿</dt><dd>{usageRecorded ? used : "—"}<small>项关联</small></dd></div>
          <div><dt>待补与核对</dt><dd>{gaps.length}<small>项缺口</small></dd></div>
        </dl>
        {bundle.query && <p className="writing-trace-question"><strong>本次取材问题</strong>{bundle.query}</p>}
        <p className="writing-trace-note">命中项须读取原文后使用。已读素材仍需核对具体主张、条件和归属；规范、示例与笔记保留各自用途。</p>
      </>}
      {(quality || contextCoverage) && <section className="writing-trace-quality" aria-label="写作质量核验">
        <strong><ShieldCheck size={15} />写作核验</strong>
        {quality && <>
          <p>{statusLabel(quality.status)} · 来源映射：{statusLabel(quality.mapping?.status)} · 语义核验：{statusLabel(quality.semantic?.status)}</p>
          <p>执行修订 {repairs.length} 轮，采用 {repairs.filter((row) => row.accepted).length} 轮；保留 {findings.length} 项待核对问题。</p>
          <p className="writing-trace-note">{quality.coverage_complete ? "已覆盖本次计划核验范围，仍需人工审阅。" : "本次检查存在未覆盖或未解决的范围，保留待核对状态。"}</p>
        </>}
        {contextCoverage && <p><BookOpen size={14} />{contextCoverage.mode === "full_manuscript" ? "完整正文上下文" : "按论证依赖读取上下文"} · {contextCoverage.complete ? "本次范围已完整提供" : `${contextCoverage.navigation_only_sections?.length ?? 0} 个章节仅提供导航信息`}</p>}
        {!!findings.length && <ul>{findings.map((finding, index) => <li key={`${finding.kind}-${index}`}>{Number.isInteger(finding.paragraph_index) ? `第 ${finding.paragraph_index + 1} 段：` : ""}{finding.message}</li>)}</ul>}
      </section>}
      {!!obligations.length && <details className="writing-trace-plan" aria-label="章节内容责任核验">
        <summary><ShieldCheck size={15} />内容责任核验：{quality?.obligations?.verified_count ?? 0} / {quality?.obligations?.required_count ?? 0} 项必需内容已核对</summary>
        <p className="writing-trace-note">核验覆盖来自本次候选和原文检查，仍需人工审阅；未完成项继续保留。</p>
        <ol>{obligations.map((row, index) => <li key={row.obligation_id ?? index}><span className="writing-card-order">{index + 1}</span><div>
          <strong>{row.question || cards.find(card => (card.point_ids ?? []).includes(row.obligation_id))?.question || `内容要求 ${index + 1}`}</strong>
          <p>{row.required ? '必需内容' : '可选内容'} · {row.status === 'verified' ? '已完成辅助核对' : '仍需核对'}{row.review?.reason ? ` · ${row.review.reason}` : ''}</p>
        </div></li>)}</ol>
      </details>}
      {!!supportRecords.length && <details className="writing-trace-plan" aria-label="主张与原文支持记录">
        <summary><BookOpen size={15} />主张与原文支持记录（{supportRecords.length} 项）</summary>
        <p className="writing-trace-note">以下为模型辅助核验与原文定位记录，不能代替人工确认。</p>
        <ol>{supportRecords.map((row, index) => <li key={row.claim_id ?? index}><span className="writing-card-order">{index + 1}</span><div>
          <strong>{row.claim_text}</strong><p>第 {Number(row.paragraph_index ?? 0) + 1} 段 · {supportLabels[row.relation] || '尚未确认'}</p>
          {!!array(row.source_quotes).length && <details className="writing-source-text"><summary>查看对应原文摘录</summary>{array(row.source_quotes).map((quote, quoteIndex) => <blockquote key={quoteIndex}>{quote.quote}</blockquote>)}</details>}
        </div></li>)}</ol>
      </details>}
      {!!cards.length && <details className="writing-trace-plan"><summary><GitBranch size={15} />论证段落计划（{cards.length} 项）</summary>
        <ol>{cards.map((card, index) => <li key={card.id ?? index}><span className="writing-card-order">{index + 1}</span><div><strong>{card.question}</strong><p>{roles[card.role] ?? "回答本段问题"}{card.relation_to_previous ? ` · ${relations[card.relation_to_previous.relation] ?? "承接前段"}` : ""}</p>{card.analysis_goal && <p>{card.analysis_goal}</p>}{!!card.gaps?.length && <p className="writing-card-gap">{card.gaps.join("；")}</p>}</div></li>)}</ol>
      </details>}
      {sourceGroups.map((group) => <details className="writing-trace-group" key={group}><summary>{groupNames[group]}（{array(bundle![group]).length} 项）</summary><div>{array(bundle![group]).map((row, index) => <SourceEntry key={`${keyOf(row)}-${row.rule_id ?? index}`} pid={pid} sectionId={sectionId} row={row} group={group} used={usedEntry(row, bundle!)} />)}</div></details>)}
      {!!gaps.length && <section className="writing-trace-gaps" aria-label="素材缺口"><strong>待补材料与阅读范围</strong><ul>{gaps.map((gap: Row | string, index: number) => <li key={index}>{typeof gap === "string" ? gap : gap.reason ?? gap.message ?? "需继续核对原文"}</li>)}</ul></section>}
      {bundle && !gaps.length && <p className="writing-trace-note"><CheckCircle2 size={14} />本次未记录取材缺口；来源是否支持具体结论仍以核验和人工审阅为准。</p>}
    </div>
  </details>;
}

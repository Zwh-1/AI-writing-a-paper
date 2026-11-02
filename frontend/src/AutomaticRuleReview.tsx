import { useEffect, useState } from "react";
import { api, send } from "./client";
import { useSourceBillingGate } from './workspace/SourceWorkspace';
type Row = Record<string, any>;
export default function AutomaticRuleReview({pid,data,busy,run}:{pid:string;data:Row|null;busy:boolean;run:(key:string,fn:()=>Promise<any>,message:string)=>Promise<any>}) {
  const gate=useSourceBillingGate();
  const [policy,setPolicy]=useState<Record<string,string>>({});
  const [result,setResult]=useState<Row|null>(null);
  const [reviewModel,setReviewModel]=useState("primary");
  const [reviewBudget,setReviewBudget]=useState(2);
  const [integrity,setIntegrity]=useState<Row|null>(null);
  useEffect(()=>{let alive=true;api(`/projects/${pid}/rules/source-policy`).then(d=>{if(alive)setPolicy(d.files)}).catch(()=>{});return()=>{alive=false}},[pid]);
  useEffect(()=>{let alive=true;api(`/projects/${pid}/rules/review-integrity`).then(d=>{if(alive)setIntegrity(d)}).catch(()=>{});return()=>{alive=false}},[pid,data]);
  const files=data?.readiness?.files??[];
  const decisions=(data?.rules??[]).filter((r:Row)=>r.machine_review);
  const conditional=decisions.filter((r:Row)=>r.machine_review.outcome==="conditional_reference").length;
  const groups=new Map<string,Row[]>();
  decisions.filter((r:Row)=>["needs_resolution","conflict"].includes(r.machine_review.outcome)).forEach((r:Row)=>{const key=r.source_file||"其他来源";groups.set(key,[...(groups.get(key)??[]),r])});
  return <section className="panel mb" aria-label="自动规则审核">
    <div className="section-heading"><h2>自动审核与来源政策</h2><span>已有候选可直接处理，无需重新提取</span></div>
    <p className="muted mb">系统检查原文依据、条款性质和适用范围，归并重复项。课程资料默认按建议启用；机器审核与人工确认分别记录。</p>
    <details className="rule-review-history"><summary>历史审核与完整性记录</summary>
    {(integrity?.findings?.length??0)>0&&<p className="alert">历史响应回放发现 {integrity?.findings?.length} 条排除结论缺少原文依据；已撤回 {integrity?.restored_rule_ids?.length??0} 条自动归档。未发起新的模型调用，原有人工确认保留。</p>}
    {(integrity?.legacy_positive_findings?.length??0)>0&&<p className="muted">另有 {integrity?.legacy_positive_findings?.length} 条历史正面结论被旧关键词筛选归档，原文与判定已集中留存；其中的评分等级和适用条件须保留，尚未自动启用。</p>}
    {(integrity?.text_integrity?.repaired_rule_ids?.length??0)>0&&<p className="alert">完整性检查已按原文补全 {integrity?.text_integrity.repaired_rule_ids.length} 条残缺条款；已撤回 {integrity?.text_integrity.quarantined_rule_ids.length} 条旧机器审核结论，补全内容保留为候选。原文和修复记录可在“核对条款”中查看。</p>}
    </details>
    {(integrity?.text_integrity?.published_affected_rule_ids?.length??0)>0&&<p className="alert">当前生效的旧规则包含 {integrity?.text_integrity.published_affected_rule_ids.length} 条已撤回的残缺条款，请更新规则包后继续写作。</p>}
    {conditional>0&&<p className="muted">另有 {conditional} 条有条件规范，保留其等级或适用条件，未判为无效要求。</p>}
    <div className="actions"><button className="btn primary" disabled={busy} onClick={()=>run("auto-review",async()=>{const value=await send(`/projects/${pid}/rules/auto-review`,{publish:true});setResult(value);return value},"自动审核已完成，通过的规则已尝试发布")}>自动审核并应用可用规则</button></div>
    <p className="muted">需要语义复核的候选会在上方自动整理任务中按总预算继续处理。只复核现有候选也可单独启动，不必重新提取材料。</p>
    <div className="actions"><label className="field"><span>审核模型</span><select aria-label="疑难规则审核模型" value={reviewModel} onChange={e=>setReviewModel(e.target.value)}><option value="primary">当前主模型</option><option value="gpt">GPT 备选（明确指定）</option></select></label><label className="field"><span>审核预算上限（USD）</span><input aria-label="疑难规则审核预算" type="number" min={0} step={0.01} value={reviewBudget} onChange={e=>setReviewBudget(Number(e.target.value))}/></label><button className="btn" disabled={busy||gate.blocked||!Number.isFinite(reviewBudget)||reviewBudget<0} onClick={()=>run("semantic-review",()=>send(`/projects/${pid}/rules/ai-extract`,{automatic:true,review_existing_only:true,provider_profile:reviewModel,budget_limit:reviewBudget,allow_gpt:reviewModel==="gpt",gpt_budget:reviewModel==="gpt"?reviewBudget:0}),"疑难条款审核已排队；实际模型、Token和费用归属会在任务中分别展示")}>复核现有疑难条款（计费）</button></div>
    {result&&<p className="muted">机器审核通过 {result.machine_verified} 条，自动归并 {result.merged??0} 条。{result.rule_package?`已发布规则包 V${result.rule_package.version}。`:result.publication_blockers?.map((b:Row)=>b.message).join("；")}</p>}
    <details className="rule-advanced"><summary>材料权威性设置（{files.length} 份）</summary><p className="muted">只需按文件设置。学校正式要求可设为约束依据；一般课件保留为课程建议。</p>
      {files.map((f:Row)=><label key={f.id} className="field"><span>{f.name}</span><select aria-label={`来源政策：${f.name}`} value={policy[f.id]??"course"} onChange={e=>setPolicy({...policy,[f.id]:e.target.value})}><option value="course">课程资料：作为建议</option><option value="binding">明确适用的正式要求：作为约束依据</option><option value="reference">知识参考：不作为论文约束</option></select></label>)}
      <button className="btn" disabled={busy} onClick={()=>run("source-policy",()=>send(`/projects/${pid}/rules/source-policy`,{files:policy},"PUT"),"来源政策已保存，再次自动审核即可应用")}>保存来源政策</button>
    </details>
    {groups.size>0&&<details className="rule-advanced"><summary>自动复核摘要（{groups.size} 个来源组）</summary>{[...groups].map(([name,items])=><p key={name}>{name}：{items.length} 条保留为待复核；{items[0].machine_review.reasons.join("；")}</p>)}</details>}
    {decisions.some((r:Row)=>r.state==="machine_verified")&&<details className="rule-advanced"><summary>撤回自动审核</summary>{decisions.filter((r:Row)=>r.state==="machine_verified").map((r:Row)=><div className="actions" key={r.id}><span>{r.statement.slice(0,80)}</span><button className="btn" disabled={busy} onClick={()=>run("restore-review",()=>send(`/projects/${pid}/rules/decisions/${r.machine_review.id}/restore`,{}),"已撤回该机器审核结果，重新发布后更新生效内容")}>撤回</button></div>)}</details>}
  </section>
}

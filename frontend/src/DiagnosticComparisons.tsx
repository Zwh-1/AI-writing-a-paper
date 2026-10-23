import {useEffect,useState} from 'react';
import {get,post,label,useResource,Row} from './writingShared';
export default function DiagnosticComparisons({pid,epoch,onChange,onError}:{pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void}){
  const runs=useResource(pid,'/diagnostic-comparisons',epoch),profiles=useResource(pid,'/writing-profiles',epoch);
  const [factor,F]=useState('workflow'),[profile,P]=useState(''),[budget,B]=useState(2),[repeat,R]=useState(3),[selected,S]=useState(''),[detail,D]=useState<Row|null>(null),[busy,U]=useState(false);
  async function load(){if(selected)D(await get(pid,`/diagnostic-comparisons/${selected}`));}
  useEffect(()=>{if(!selected)return;void load().catch(e=>onError(e.message));const timer=setInterval(()=>void load().catch(()=>{}),4000);return()=>clearInterval(timer);},[pid,selected,epoch]);
  async function act(fn:()=>Promise<any>){U(true);try{await fn();await load();onChange();}catch(e){onError((e as Error).message);}finally{U(false);}}
  return <section className="wa-card"><h3>可比对照与位置偏好</h3><p>每组使用相同原始题目、材料、模型配置与重复次数；记录新增阶段的全部费用。语义结论需另行审阅。</p>
    <form onSubmit={e=>{e.preventDefault();void act(async()=>{const r=await post(pid,'/diagnostic-comparisons',{factor,profile_id:profile,repetitions:repeat,budget_limit:budget});S(r.id);});}}>
      <label>对照因素<select value={factor} onChange={e=>F(e.target.value)}><option value="workflow">旧写作基线 / 先判定后正文</option><option value="revision_state">连续修改 / 当前状态重建</option><option value="annotation">混合标注 / 冻结正文后标注</option></select></label>
      <label>共同配置<select value={profile} onChange={e=>P(e.target.value)} required><option value="">请选择</option>{(profiles.data??[]).filter((p:Row)=>p.configuration_current).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model}</option>)}</select></label>
      <div className="wa-row"><label>每变体重复次数<input type="number" min={1} max={3} value={repeat} onChange={e=>R(Number(e.target.value))}/></label><label>两组及评审总预算<input type="number" min={0.01} step={0.01} value={budget} onChange={e=>B(Number(e.target.value))}/></label></div><button disabled={busy||!profile}>创建对照（调用模型）</button>
    </form>
    <label>对照记录<select value={selected} onChange={e=>S(e.target.value)}><option value="">请选择</option>{(runs.data??[]).map((r:Row)=><option key={r.id} value={r.id}>{r.created_at} · {r.factor}</option>)}</select></label>
    {detail&&<><p>{detail.all_generated?'输出生成完成':'仍有样本未完成'}；{detail.human_acceptance_complete?'已登记全部人工判定':'人工质量验收未完成'}。</p>{Object.entries(detail.arms??{}).map(([arm,row])=>{const r=row as Row;return <article key={arm}><h4>{arm}</h4><p>完成 {r.summary.completed}/{r.summary.total}；人工通过 {r.summary.human_passed}；请求 {r.billing.requests} 次，费用/预留 {Number(r.billing.charged_or_reserved).toFixed(4)}。</p></article>;})}
      {detail.paired_samples.filter((p:Row)=>Object.values(p.arms).length===2).slice(0,12).map((pair:Row,index:number)=>{const ids=Object.values(pair.arms).map((r:any)=>r.item_id);return <details key={index}><summary>{pair.case_id} · {pair.variant_id} · 第{pair.repetition+1}次</summary><p>原始输入{pair.original_inputs_match?'一致':'存在差异，请检查'}。</p><button disabled={busy||!profile||Object.values(pair.arms).some((r:any)=>r.status!=='completed')} onClick={()=>void act(()=>post(pid,`/diagnostic-comparisons/${selected}/position-swap`,{profile_id:profile,item_ids:ids}))}>交换左右位置核对评审偏好（2次模型请求）</button></details>;})}
      {detail.position_swap_checks.map((r:Row)=><p key={r.id}>位置交换：{r.result.position_consistent?'两次判断一致':'存在位置敏感或无法判断'} · {label(r.method)}</p>)}</>}
  </section>;
}

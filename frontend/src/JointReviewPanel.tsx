import {useEffect,useRef,useState} from 'react';
import {api} from './client';
import {get,post,path,useResource,Row,label} from './writingShared';
const text=(node:Row):string=>node.type==='text'?node.text??'':(node.content??[]).map(text).join(node.type==='doc'?'\n\n':'');
export default function JointReviewPanel({pid,epoch,onChange,onError}:{pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void}){
  const candidates=useResource(pid,'/writing-joint-candidates',epoch),profiles=useResource(pid,'/writing-profiles',epoch);
  const [selected,S]=useState<string[]>([]),[profile,P]=useState(''),[reviewId,J]=useState(''),[review,R]=useState<Row|null>(null),[note,N]=useState(''),[readAll,A]=useState(false),[busy,B]=useState(false);const keys=useRef<Record<string,string>>({});
  async function load(){if(reviewId)R(await get(pid,`/writing-joint-reviews/${reviewId}`));}
  useEffect(()=>{if(!reviewId)return;void load().catch(e=>onError(e.message));const t=setInterval(()=>void load().catch(()=>{}),3500);return()=>clearInterval(t);},[pid,reviewId,epoch]);
  async function act(fn:()=>Promise<any>){B(true);try{await fn();await load();onChange();}catch(e){onError((e as Error).message);}finally{B(false);}}
  return <section className="wa-card"><h3>跨章节联合复核与采用</h3><p>选择相互影响的完整候选。各节先完成逐句核对，再通读联合候选；采用时所有基础版本同时检查并一次提交。</p>
    {(candidates.data??[]).map((c:Row)=><label key={c.candidate_id} className="wa-check"><input type="checkbox" checked={selected.includes(c.candidate_id)} onChange={e=>S(e.target.checked?[...selected,c.candidate_id]:selected.filter(x=>x!==c.candidate_id))}/>{c.title} · {label(c.analysis_state)}</label>)}
    <label>联合复核配置<select value={profile} onChange={e=>P(e.target.value)}><option value="">请选择</option>{(profiles.data??[]).filter((p:Row)=>p.configuration_current).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model}</option>)}</select></label>
    <button disabled={busy||selected.length<2||!profile} onClick={()=>void act(async()=>{const all=await get(pid,'/writing-joint-candidates');const items=selected.map(id=>{const c=all.find((r:Row)=>r.candidate_id===id);if(!c)throw new Error('候选已变化，请重新选择');return {candidate_id:id,body:{base_revision_id:c.base_revision_id,accept_policy:'strict',placement:'replace_section'}};});const result=await post(pid,'/writing-joint-reviews',{items,profile_id:profile});J(result.id);A(false);})}>建立联合候选并复核（调用模型）</button>
    {review&&<div><p>联合状态：{label(review.status)}</p>{review.input.sections.map((s:Row)=><article key={s.section_id}><h4>{(candidates.data??[]).find((c:Row)=>c.candidate_id===s.candidate_id)?.title??'联合候选章节'}</h4><pre>{text(s.candidate_document)}</pre></article>)}<p>{review.result?.summary}</p>{review.result?.findings?.map((f:Row,i:number)=><p key={i}>{f.message}</p>)}
      <label>联合核对依据<textarea value={note} onChange={e=>N(e.target.value)} placeholder="核对互相引用的前提、条件、结论与全部未决问题"/></label><label className="wa-check"><input type="checkbox" checked={readAll} onChange={e=>A(e.target.checked)}/>我已通读全部联合候选并核对相互影响的前提</label>
      <button disabled={busy||!readAll||note.trim().length<20||!review.result?.coverage_complete} onClick={()=>void act(()=>post(pid,`/writing-joint-reviews/${reviewId}/review`,{decision:'approved',reviewed_input_hash:review.input_hash,all_candidates_reviewed:true,note}))}>登记本版本的人工联合复核</button>
      <button disabled={busy||review.status!=='human_reviewed'} onClick={()=>void act(async()=>{keys.current[reviewId]??=crypto.randomUUID();await api(path(pid,'/writing-adoptions'),{method:'POST',headers:{'Idempotency-Key':keys.current[reviewId]},body:JSON.stringify({items:review.input.items,joint_review_id:reviewId})});})}>严格采用全部联合候选</button></div>}
  </section>;
}

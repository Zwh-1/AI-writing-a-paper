import {useState} from 'react';
import {post,useResource,Row} from './writingShared';
export default function StaleReasonsPanel({pid,epoch,onChange,onError,sections}:{pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void;sections:Row[]}){
  const reasons=useResource(pid,'/writing-stale-reasons',epoch);const [selected,S]=useState<string[]>([]),[note,N]=useState(''),[busy,B]=useState(false);
  async function resolve(s:Row){B(true);try{const ids=(reasons.data??[]).filter((r:Row)=>r.section_id===s.section_id&&selected.includes(r.id)).map((r:Row)=>r.id);await post(pid,`/sections/${s.section_id}/resolve-stale`,{reason_ids:ids,reviewed_revision_id:s.revision_id,note});S(x=>x.filter(id=>!ids.includes(id)));onChange();}catch(e){onError((e as Error).message);}finally{B(false);}}
  return <details className="wa-card"><summary>逐项处理待复查原因</summary><p>来源、规则、章节前提等原因分别保留。只有核对并处理的原因才会清除。</p><label>本次处理依据<textarea value={note} onChange={e=>N(e.target.value)} placeholder="填写对照了哪些当前正文与来源，怎样处理这些变化"/></label>
    {sections.map(s=>{const rows=(reasons.data??[]).filter((r:Row)=>r.section_id===s.section_id&&r.status==='open');return rows.length?<fieldset key={s.section_id}><legend>{s.title}</legend>{rows.map((r:Row)=><label className="wa-check" key={r.id}><input type="checkbox" checked={selected.includes(r.id)} onChange={e=>S(e.target.checked?[...selected,r.id]:selected.filter(x=>x!==r.id))}/>{r.message}</label>)}<button disabled={busy||note.trim().length<12||!rows.some((r:Row)=>selected.includes(r.id))} onClick={()=>void resolve(s)}>登记所选原因已核对处理</button></fieldset>:null;})}
  </details>;
}

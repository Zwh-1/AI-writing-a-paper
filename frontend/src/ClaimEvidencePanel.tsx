import {useState} from 'react';
import {get,label,parse,Row} from './writingShared';
export default function ClaimEvidencePanel({pid,analysis}:{pid:string;analysis:Row|null}){
  const [view,V]=useState<Row|null>(null),[error,E]=useState('');
  if(!analysis)return <p>正文尚未完成逐句分析。</p>;
  return <section className="wa-card"><h3>句子、论断与依据</h3><p>共 {analysis.coverage.total} 句；未分类 {analysis.coverage.unclassified} 句；有关联论断 {analysis.coverage.with_claims} 句。模型辅助判断仍需核对。</p>
    {analysis.sentences.map((s:Row)=><details key={s.id}><summary><span className="wa-badge">{label(s.role)}</span>{s.text}</summary><p>字符位置 {s.start_cp}–{s.end_cp}（Unicode码点）；节点 {s.node_id.slice(0,8)}</p>{s.claims.length?s.claims.map((c:Row)=><div key={c.id}><p>{parse(c.proposition_json).statement}</p><button onClick={async()=>{try{V(await get(pid,`/claims/${c.id}`));E('');}catch(e){E((e as Error).message);}}}>查看该论断证据</button></div>):<p>本句尚无独立论断关联。</p>}</details>)}
    {view&&<aside><h4>{view.proposition?.statement}</h4><p>条件：{(view.proposition?.conditions??[]).join('；')||'未登记额外条件'}</p>{(view.evidence??[]).map((e:Row)=><button key={e.evidence_id} onClick={async()=>{try{const detail=await get(pid,`/evidence/${e.evidence_id}`);V({...view,source:detail});}catch(x){E((x as Error).message);}}}>打开依据 {e.evidence_id.slice(0,8)}</button>)}{view.source&&<div><p>{label(view.source.kind)} · {label(view.source.validity)}</p><pre>{view.source.payload?.text??JSON.stringify(view.source.payload,null,2)}</pre></div>}</aside>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}

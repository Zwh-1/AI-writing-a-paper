import {useState} from 'react';
import {get,post,parse,label,useResource,Row} from './writingShared';

export default function ThesisCore({pid,epoch,onChange,onError}:{pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void}){
  const core=useResource(pid,'/thesis-core',epoch),sections=useResource(pid,'/sections',epoch),ee=useResource(pid,'/evidence',epoch),pp=useResource(pid,'/writing-profiles',epoch);
  const [question,Q]=useState(''),[scope,S]=useState(''),[method,M]=useState(''),[limits,L]=useState(''),[sid,SS]=useState(''),[goal,G]=useState(''),[ids,IDs]=useState<string[]>([]),[route,R]=useState('primary'),[thinking,TH]=useState(false),[busy,B]=useState(false);
  async function act(fn:()=>Promise<any>){B(true);try{await fn();onChange();}catch(e){onError((e as Error).message);}finally{B(false);}}
  const head=core.data?.state;const published=core.data?.versions?.find((v:Row)=>v.id===head?.core_version_id);
  return <div className="wa-stack"><section className="wa-card"><h3>论文核心与结论边界</h3>
    {published&&<details open><summary>当前已发布内容</summary><dl>{Object.entries(parse(published.content_json)).map(([k,v])=><div key={k}><dt>{({research_question:'研究问题',scope:'研究范围',method:'方法',limitations:'局限'} as Record<string,string>)[k]??k}</dt><dd>{typeof v==='string'?v:JSON.stringify(v)}</dd></div>)}</dl></details>}
    <form onSubmit={e=>{e.preventDefault();void act(async()=>{const current=await get(pid,'/writing-state');const v=await post(pid,'/thesis-core',{content:{research_question:question,scope,method,limitations:limits.split('\n').filter(Boolean)},parent_id:current.core_version_id});await post(pid,`/thesis-core/${v.id}/publish`,{expected_revision:current.revision});});}}>
      <label>研究问题<textarea value={question} onChange={e=>Q(e.target.value)} required placeholder="论文具体回答什么问题？"/></label><label>研究对象与范围<textarea value={scope} onChange={e=>S(e.target.value)} required/></label><label>方法与依据<textarea value={method} onChange={e=>M(e.target.value)}/></label><label>结论限制（每行一项）<textarea value={limits} onChange={e=>L(e.target.value)}/></label><button className="wa-button" disabled={busy}>保存并发布新版本</button>
    </form></section>
    <section className="wa-card"><h3>章节写作任务</h3><p>指定本节要回答的问题和允许使用的证据。来源不足时，工作流将保留缺口。</p>
      <form onSubmit={e=>{e.preventDefault();void act(async()=>{const h=await get(pid,'/writing-state');await post(pid,`/section-contracts/${sid}`,{expected_revision:h.revision,content:{goal,evidence_ids:ids,depends_on_sections:[]}});});}}>
        <label>章节<select value={sid} onChange={e=>SS(e.target.value)} required><option value="">请选择已有章节</option>{(Array.isArray(sections.data)?sections.data:[]).map((s:Row)=><option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
        <label>本节要解释或论证什么<textarea value={goal} onChange={e=>G(e.target.value)} required/></label>
        <fieldset><legend>本节证据</legend>{(ee.data??[]).map((e:Row)=><label className="wa-check" key={e.id}><input type="checkbox" checked={ids.includes(e.id)} disabled={e.validity==='revoked'} onChange={event=>IDs(event.target.checked?[...ids,e.id]:ids.filter(x=>x!==e.id))}/>{label(e.kind)} · {e.payload?.title||parse(e.locator_json).path||e.id.slice(0,8)}</label>)}</fieldset>
        <button disabled={!sid||busy}>发布章节任务</button>
      </form>
    </section>
    <section className="wa-card"><h3>阶段模型配置</h3><p>使用接口设置中已有的模型及凭据。配置可用与论文质量已验证分别记录。</p>
      <form className="wa-row" onSubmit={e=>{e.preventDefault();void act(()=>post(pid,'/writing-profiles',{role:'draft',configuration:{route,tool_mode:'json',thinking_enabled:thinking,reconcile_billing:true}}));}}><label>路线<select value={route} onChange={e=>R(e.target.value)}><option value="primary">当前主模型</option><option value="gpt">已配置的GPT备选</option></select></label><label className="wa-check"><input type="checkbox" checked={thinking} onChange={e=>TH(e.target.checked)}/>请求接口支持的思考模式</label><button disabled={busy}>建立配置</button></form>
      {(pp.data??[]).map((p:Row)=><div className="wa-row" key={p.id}><span>{p.actual_config?.model} · {p.actual_config?.protocol}</span><span>{label(p.qualification)} · {p.configuration_current?'当前配置匹配':'配置已变化'}</span><code>{p.id.slice(0,8)}</code></div>)}
    </section>
  </div>;
}

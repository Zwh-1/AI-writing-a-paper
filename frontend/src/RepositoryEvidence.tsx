import {useState} from 'react';
import {get,post,label,parse,useResource,Row} from './writingShared';

export default function RepositoryEvidence({pid,epoch,onChange,onError}:{pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void}){
  const repos=useResource(pid,'/repositories',epoch),snaps=useResource(pid,'/snapshots',epoch),evidence=useResource(pid,'/evidence',epoch),state=useResource(pid,'/writing-state',epoch);
  const [root,setRoot]=useState('D:\\桌面\\edu-sbt-platform'),[allow,setAllow]=useState(false),[selected,setSelected]=useState(''),[query,setQuery]=useState(''),[hits,setHits]=useState<Row[]>([]),[view,setView]=useState<Row|null>(null),[busy,setBusy]=useState(false),[author,setAuthor]=useState('');
  const sid=selected||state.data?.target_snapshot_id||snaps.data?.[0]?.id;
  async function act(fn:()=>Promise<any>,changed=true){setBusy(true);try{await fn();if(changed)onChange();}catch(e){onError((e as Error).message);}finally{setBusy(false);}}
  return <div className="wa-stack">
    <section className="wa-card"><h3>研究项目来源</h3><p>选择本机项目目录。冻结后的代码有独立版本，工作目录变化不会改写已有证据。</p>
      <form onSubmit={e=>{e.preventDefault();void act(async()=>{const r=await post(pid,'/repositories',{root,egress:allow?'selected_content':'local_only'});await post(pid,`/repositories/${r.id}/snapshots`,{});});}}>
        <label>项目目录<input aria-label="项目代码目录" value={root} onChange={e=>setRoot(e.target.value)} required/></label>
        <label className="wa-check"><input type="checkbox" checked={allow} onChange={e=>setAllow(e.target.checked)}/>允许将选定代码证据发送到已配置的模型接口</label>
        <button disabled={busy} className="wa-button" type="submit">绑定并冻结来源</button>
      </form>
      {(repos.data??[]).map((r:Row)=><div className="wa-row" key={r.id}><span className="wa-wrap">{r.root_path_local}</span><span>{r.egress_policy==='local_only'?'仅本机':'允许选定内容外发'}</span><button disabled={busy} onClick={()=>void act(()=>post(pid,`/repositories/${r.id}/snapshots`,{}))}>更新快照</button><button disabled={busy} onClick={()=>void act(()=>post(pid,`/repositories/${r.id}`,{egress:r.egress_policy==='local_only'?'selected_content':'local_only'},'PATCH'))}>{r.egress_policy==='local_only'?'允许选定证据外发':'禁止外发'}</button></div>)}
      {repos.error&&<p role="alert">{repos.error}</p>}
    </section>
    <section className="wa-card"><h3>冻结版本与覆盖</h3>
      <select aria-label="来源快照" value={sid??''} onChange={e=>{setSelected(e.target.value);setHits([]);}}><option value="">请选择</option>{(snaps.data??[]).map((s:Row)=><option key={s.id} value={s.id}>{s.created_at} · {label(s.state)} · {s.id.slice(0,8)}</option>)}</select>
      <div className="wa-actions"><button disabled={!sid||busy} onClick={()=>void act(async()=>{setView(await get(pid,`/snapshots/${sid}`));})}>查看覆盖清单</button><button disabled={!sid||busy} onClick={()=>void act(async()=>{const s=await get(pid,'/writing-state');await post(pid,'/writing-state',{target_snapshot_id:sid,expected_revision:s.revision},'PATCH');})}>设为研究基线</button></div>
      <form className="wa-row" onSubmit={e=>{e.preventDefault();void act(async()=>{setHits(await get(pid,`/snapshots/${sid}/search?q=${encodeURIComponent(query)}`));},false);}}><input aria-label="代码检索" placeholder="函数、条件、功能或关键词" value={query} onChange={e=>setQuery(e.target.value)} required/><button disabled={!sid||busy}>检索冻结代码</button></form>
      {hits.map((h:Row,i)=><article className="wa-source" key={h.path+String(i)}><strong>{h.path}:{h.line_start}–{h.line_end}</strong>{h.text_warnings?.length>0&&<p className="wa-notice">{h.text_warnings.join("；")}</p>}<pre>{h.text}</pre><button disabled={busy} onClick={()=>void act(async()=>{await post(pid,'/evidence',{kind:h.path.includes('/test')?'test_definition':'code_observation',origin_type:'code',origin_id:sid,locator:{path:h.path,line_start:h.line_start,line_end:h.line_end}});})}>登记为本项目依据</button></article>)}
      {view&&<details open><summary>快照清单</summary><p>纳入 {view.manifest?.files?.length??0} 个文件；覆盖：{view.manifest?.coverage==='complete'?'当前纳入范围完整':'存在缺口'}；失败/限制 {view.manifest?.gaps?.length??0} 项。</p><p>原始内容质量提示：{(view.manifest?.files??[]).filter((f:Row)=>f.text_warnings?.length).length}个文件；原文已含替换字符时会保留原样，不猜测修复。</p><pre>{JSON.stringify(view.manifest?.gaps??[],null,2)}</pre><p>规则文件：{(view.manifest?.rules??[]).map((r:Row)=>r.path).join('、')||'未发现；不能假定已加载额外规则'}</p></details>}
    </section>
    <section className="wa-card"><h3>已有证据</h3><p>源码观察、测试定义与实际运行结果分别保存；登记来源不会自动确认论文结论。</p>
      {(evidence.data??[]).map((e:Row)=><details key={e.id}><summary>{label(e.kind)} · {e.payload?.title||parse(e.locator_json).path||e.id.slice(0,8)} · {label(e.validity)}</summary><p>证据编号：{e.id}</p>{e.payload?.text_warnings?.length>0&&<p className="wa-notice">{e.payload.text_warnings.join("；")}</p>}<pre>{e.payload?.text??JSON.stringify(e.payload,null,2)}</pre><p>条件：{(e.conditions??[]).join('；')||'尚未补充额外条件'}</p><button onClick={()=>void act(()=>post(pid,`/evidence/${e.id}`,{validity:e.validity==='revoked'?'current':'revoked'},'PATCH'))}>{e.validity==='revoked'?'恢复此依据':'撤回此依据'}</button></details>)}
      <form onSubmit={e=>{e.preventDefault();void act(async()=>{await post(pid,'/evidence',{kind:'author_statement',origin_type:'author',text:author});setAuthor('');});}}><label>研究意图或作者说明<textarea value={author} onChange={e=>setAuthor(e.target.value)} placeholder="只用于记录研究问题、意图或作者声明，不自动视为测试或部署结果" required/></label><button disabled={busy}>登记作者说明</button></form>
    </section>
  </div>;
}

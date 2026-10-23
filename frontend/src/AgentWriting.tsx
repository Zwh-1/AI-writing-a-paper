import {useEffect,useState} from 'react';
import {get,post,label,useResource,Row} from './writingShared';
import RepositoryEvidence from './RepositoryEvidence';
import ThesisCore from './ThesisCore';
import ModelDiagnostics from './ModelDiagnostics';
import WritingSessionPanel from './WritingSessionPanel';
import './writingAgent.css';
import JointReviewPanel from './JointReviewPanel';
import StaleReasonsPanel from './StaleReasonsPanel';

export default function AgentWriting({pid,tick}:{pid:string;tick:number}){
  const [tab,T]=useState('setup'),[epoch,E]=useState(0),[error,ERR]=useState(''),[busy,B]=useState(false),[sid,SID]=useState(''),[profile,P]=useState(''),[session,SE]=useState(''),[budget,BU]=useState(12),[report,R]=useState<Row|null>(null);
  const [paperNote,PN]=useState(''),[readAll,RA]=useState(false),[verifyProfile,VP]=useState('');
  const refresh=()=>E(x=>x+1),version=epoch+tick;
  const sections=useResource(pid,'/sections',version),profiles=useResource(pid,'/writing-profiles',version),sessions=useResource(pid,'/writing-sessions',version),state=useResource(pid,'/writing-state',version),coverage=useResource(pid,tab==='paper'?'/writing-coverage':null,version);
  useEffect(()=>{SE('');R(null);ERR('');},[pid]);
  async function act(fn:()=>Promise<any>){B(true);try{await fn();ERR('');refresh();}catch(e){ERR((e as Error).message);}finally{B(false);}}
  const props={pid,epoch:version,onChange:refresh,onError:ERR};
  return <div className="wa-page"><header><h2>有据写作</h2><p>以冻结的项目代码和资料为依据，分别核对判断、正文含义与篇章关系。当前正文只在明确采用后更新。</p></header>
    <nav className="wa-tabs" aria-label="有据写作功能">{[['setup','论文与任务'],['sources','代码与证据'],['writing','写作与核验'],['diagnostics','模型诊断'],['paper','全文与交付']].map(([id,name])=><button key={id} className={tab===id?'active':''} onClick={()=>T(id)}>{name}</button>)}</nav>
    {error&&<div className="wa-error" role="alert">{error}<button aria-label="关闭错误" onClick={()=>ERR('')}>×</button></div>}
    {tab==='setup'&&<ThesisCore {...props}/>}
    {tab==='sources'&&<RepositoryEvidence {...props}/>}
    {tab==='diagnostics'&&<ModelDiagnostics {...props}/>}
    {tab==='writing'&&<><section className="wa-card"><h3>建立完整论证单元</h3><form onSubmit={e=>{e.preventDefault();void act(async()=>{const head=await get(pid,'/writing-state');const all=await get(pid,'/sections');const s=all.find((x:Row)=>x.id===sid);const contract=head.contracts[sid];if(!contract)throw new Error('请先为当前章节发布写作任务');const result=await post(pid,'/writing-sessions',{section_id:sid,operation:'generate_unit',base_revision_id:s.current_revision_id,expected_project_state_revision:head.revision,contract_version_id:contract,profile_id:profile,policy:{budget_limit:budget,...(verifyProfile?{stage_profiles:Object.fromEntries(['judgment_check','annotation','semantic','coherence','regression'].map(stage=>[stage,verifyProfile]))}:{})}});SE(result.id);});}}>
      <div className="wa-row"><label>章节<select value={sid} onChange={e=>SID(e.target.value)} required><option value="">请选择</option>{(Array.isArray(sections.data)?sections.data:[]).map((s:Row)=><option key={s.id} value={s.id}>{s.title}{state.data?.contracts?.[s.id]?' · 已有任务':''}</option>)}</select></label><label>阶段配置<select value={profile} onChange={e=>P(e.target.value)} required><option value="">请选择</option>{(profiles.data??[]).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model} · {label(p.qualification)}</option>)}</select></label><label>核验阶段配置（可选）<select value={verifyProfile} onChange={e=>VP(e.target.value)}><option value="">沿用生成配置</option>{(profiles.data??[]).filter((p:Row)=>p.configuration_current).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model} · 输出上限 {p.actual_config.max_output_tokens}</option>)}</select></label><label>会话预算上限<input type="number" min={0.01} step={0.01} value={budget} onChange={e=>BU(Number(e.target.value))}/></label></div><button className="wa-button" disabled={!sid||!profile||busy}>调查并生成候选（调用模型）</button>
    </form></section><section className="wa-card"><label>会话<select aria-label="写作会话" value={session} onChange={e=>SE(e.target.value)}><option value="">请选择</option>{(sessions.data??[]).map((s:Row)=><option key={s.id} value={s.id}>{s.created_at} · {label(s.status)} · {s.id.slice(0,8)}</option>)}</select></label></section><WritingSessionPanel pid={pid} sessionId={session} epoch={version} onChange={refresh} onError={ERR}/></>}
    {tab==='paper'&&<div className="wa-stack"><section className="wa-card"><h3>全文覆盖与论证复核</h3><div className="wa-table-wrap"><table><thead><tr><th>章节</th><th>正文</th><th>句子分析</th><th>待复查</th></tr></thead><tbody>{(coverage.data?.sections??[]).map((s:Row)=><tr key={s.section_id}><td>{s.title}</td><td>{s.revision_id?'已有版本':'尚未写作'}</td><td>{label(s.analysis_state)}{s.coverage?`（${s.coverage.total}句，${s.coverage.unclassified}句未分类）`:''}</td><td>{s.stale?'需要复核':'未标记过期'}</td></tr>)}</tbody></table></div>
      <label>复核模型<select value={profile} onChange={e=>P(e.target.value)}><option value="">请选择</option>{(profiles.data??[]).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model}</option>)}</select></label><button disabled={!profile||busy} onClick={()=>void act(()=>post(pid,'/writing-reviews',{profile_id:profile}))}>排队检查当前全文论证（调用模型）</button>
      <div className="wa-actions"><button disabled={busy} onClick={()=>void act(async()=>R(await get(pid,'/writing-export-report')))}>查看草稿交付缺口</button><button disabled={busy} onClick={()=>void act(async()=>R(await get(pid,'/writing-export-report?strict=true')))}>核对严格交付条件</button></div>
      {report&&<div><h4>{report.unresolved.length?'仍有未决项':'当前检查未列未决项'}</h4><ul>{report.unresolved.map((p:Row,i:number)=><li key={i}>{p.message}</li>)}</ul>{(report.paper_reviews??[]).map((r:Row)=><details key={r.id}><summary>全文复核 · {label(r.status)}</summary><p>{r.result.summary}</p><ul>{r.result.findings.map((f:Row,i:number)=><li key={i}>{f.message}</li>)}</ul></details>)}</div>}
      <details><summary>人工通读与跨范围复核</summary><p>模型无法可靠覆盖的关系可以人工核对；此记录只代表明确的人工声明。</p><label>核对依据与处理过的问题<textarea value={paperNote} onChange={e=>PN(e.target.value)} placeholder="写明核对了研究问题与结论、实现与测试、章节条件，以及哪些模型疑点已处理"/></label><label className="wa-check"><input type="checkbox" checked={readAll} onChange={e=>RA(e.target.checked)}/>我已通读当前全部章节，并核对跨章论证与未决问题</label><button disabled={busy||!readAll||paperNote.trim().length<20} onClick={()=>void act(()=>post(pid,'/writing-reviews/human',{all_sections_reviewed:true,note:paperNote,reviewed_revisions:Object.fromEntries((coverage.data?.sections??[]).filter((s:Row)=>s.revision_id).map((s:Row)=>[s.section_id,s.revision_id]))}))}>登记当前版本的人工全文复核</button></details>
      <p>文件生成请使用原有“排版与交付”；导出包会附上当前证据与核验报告。</p>
    </section><StaleReasonsPanel {...props} sections={coverage.data?.sections??[]}/><JointReviewPanel {...props}/></div>}
  </div>;
}

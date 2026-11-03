import {useEffect,useState,type ReactNode} from 'react';
import {get,post,label,useResource,Row} from './writingShared';
import RepositoryEvidence from './RepositoryEvidence';
import ThesisCore from './ThesisCore';
import ModelDiagnostics from './ModelDiagnostics';
import WritingSessionPanel from './WritingSessionPanel';
import './writingAgent.css';
import JointReviewPanel from './JointReviewPanel';
import StaleReasonsPanel from './StaleReasonsPanel';
import './workspace/workspace.css';

export default function AgentWriting({pid,tick,onOpenEditor,onOpenResearch,onOpenProject,onOpenSettings,onOpenRules,navigation}:{pid:string;tick:number;onOpenEditor?:(sid:string)=>void;onOpenResearch?:()=>void;onOpenProject?:()=>void;onOpenSettings?:()=>void;onOpenRules?:()=>void;navigation?:ReactNode}){
  const [tab,T]=useState('setup'),[epoch,E]=useState(0),[error,ERR]=useState(''),[busy,B]=useState(false),[sid,SID]=useState(localStorage.getItem('paper_section')??''),[profile,P]=useState(''),[session,SE]=useState(''),[budget,BU]=useState(12),[report,R]=useState<Row|null>(null);
  const [paperNote,PN]=useState(''),[readAll,RA]=useState(false),[verifyProfile,VP]=useState('');
  const [budgetEdited,BE]=useState(false);
  const refresh=()=>E(x=>x+1),version=epoch+tick;
  const sections=useResource(pid,'/sections',version),profiles=useResource(pid,'/writing-profiles',version),sessions=useResource(pid,'/writing-sessions',version),state=useResource(pid,'/writing-state',version),link=useResource(pid,'/writing-link',version),coverage=useResource(pid,tab==='paper'?'/writing-coverage':null,version);
  const currentProfiles:Row[]=(profiles.data??[]).filter((p:Row)=>p.configuration_current);
  const preflight=useResource(pid,tab==='writing'&&sid?'/writing-preflight?section_id='+encodeURIComponent(sid)+(profile?'&profile_id='+encodeURIComponent(profile):'')+(budgetEdited?'&budget_limit='+encodeURIComponent(budget):''):null,version);
  const isEnglishAbstract=(Array.isArray(sections.data)?sections.data:[]).find((s:Row)=>s.id===sid)?.kind==='abstract_en';
  const chapterSessions:Row[]=(sessions.data??[]).filter((s:Row)=>s.section_id===sid);
  const activeChapterSession=chapterSessions.find(s=>['queued','running'].includes(s.status));
  const visibleSession=chapterSessions.find(s=>s.id===session)??chapterSessions[0];
  const candidateCount=Number(visibleSession?.candidate_count??0);
  useEffect(()=>{SID(localStorage.getItem('paper_section')??'');P('');VP('');SE('');BE(false);R(null);ERR('');},[pid]);
  useEffect(()=>{if(!budgetEdited&&preflight.data?.default_budget!==undefined)BU(preflight.data.default_budget);},[preflight.data,budgetEdited]);
  useEffect(()=>{if(sid)localStorage.setItem('paper_section',sid);},[sid]);
  useEffect(()=>{if(Array.isArray(sections.data)&&!sections.data.some((s:Row)=>s.id===sid))SID(sections.data[0]?.id??'');},[sid,sections.data]);
  useEffect(()=>{if(!profiles.data)return;if(profile&&!currentProfiles.some(p=>p.id===profile))P('');if(verifyProfile&&!currentProfiles.some(p=>p.id===verifyProfile))VP('');},[profiles.data,profile,verifyProfile]);
  useEffect(()=>{if(session||!sid||!Array.isArray(sessions.data))return;const latest=sessions.data.find((s:Row)=>s.section_id===sid);if(latest)SE(latest.id);},[sessions.data,sid,session]);
  async function act(fn:()=>Promise<any>){B(true);try{await fn();ERR('');refresh();}catch(e){ERR((e as Error).message);}finally{B(false);}}
  const props={pid,epoch:version,onChange:refresh,onError:ERR,onOpenResearch,onSelectSection:(sectionId:string)=>{SID(sectionId);SE('');T('writing');},modelReady:link.data?Boolean(link.data.model?.configured??currentProfiles.length>0):undefined,modelError:link.error,onConfigureModel:()=>T('writing')};
  return <div className="wa-page"><header className="workspace-heading"><div><h1>有据写作</h1><p>以研究事实、冻结代码和资料为依据，先建立章节任务，再生成候选，最后回到正文核验。</p></div><div className="wa-actions actions"><button onClick={onOpenResearch}>查看研究与成果</button>{sid&&<button onClick={()=>onOpenEditor?.(sid)}>打开当前章节正文</button>}</div></header>
    {navigation}
    <nav className="wa-tabs" aria-label="有据写作功能">{[['setup','当前项目'],['sources','项目代码与证据'],['writing','本节写作与核验'],['diagnostics','模型诊断'],['paper','全文与交付']].map(([id,name])=><button key={id} className={tab===id?'active':''} onClick={()=>T(id)}>{name}</button>)}</nav>
    {error&&<div className="wa-error" role="alert">{error}<button aria-label="关闭错误" onClick={()=>ERR('')}>×</button></div>}
    {tab==='setup'&&<ThesisCore {...props}/>}
    {tab==='sources'&&<RepositoryEvidence {...props}/>}
    {tab==='diagnostics'&&<ModelDiagnostics {...props}/>}
    {tab==='writing'&&<>
      <section className="wa-card" aria-label="开始本节写作">
        <h3>开始本节写作</h3>
        <p>按已确认的章节目标，读取项目资料、文献和代码证据，生成可审阅的正文候选。</p>
        {visibleSession&&<div className="wa-notice" role="status" aria-label="本节写作进度"><strong>{candidateCount?`已生成 ${candidateCount} 份候选稿`:`当前写作任务：${label(visibleSession.status)}`}</strong><p>当前会话：{label(visibleSession.status)}。{candidateCount?(['queued','running'].includes(visibleSession.status)?'候选已保存，核验正在进行；可先查看正文与已返回的记录。':'候选已保存，正文采用需明确审阅；核验疑点不会自动视为通过。'):'查看下方会话进度和实际阻塞原因。'}</p><button type="button" onClick={()=>{const panel=document.getElementById('writing-candidate-review')??document.getElementById('writing-session-review');panel?.scrollIntoView({behavior:'smooth',block:'start'});panel?.focus({preventScroll:true});}}>查看候选与核验</button></div>}
        <form onSubmit={e=>{e.preventDefault();if(busy||activeChapterSession||(!isEnglishAbstract&&!preflight.data?.can_start))return;void act(async()=>{
          const [head,all]=await Promise.all([get(pid,'/writing-state'),get(pid,'/sections')]);
          const selected=all.find((s:Row)=>s.id===sid);
          if(!selected)throw new Error('当前章节已变化，请重新选择章节');
          if(selected.kind==='abstract_en'){onOpenEditor?.(sid);return;}
          const result=await post(pid,'/writing-start',{
            section_id:sid,base_revision_id:selected.current_revision_id,
            expected_project_state_revision:head.revision,...(budgetEdited?{budget_limit:budget}:{}),
            ...(profile?{profile_id:profile}:{}),
            ...(verifyProfile?{verification_profile_id:verifyProfile}:{}),
          });
          await sessions.reload();SE(result.id);
        });}}>
          <div className="wa-row">
            <label>章节<select aria-label="章节" value={sid} onChange={e=>{SID(e.target.value);SE('');}} required>
              <option value="">请选择章节</option>
              {(Array.isArray(sections.data)?sections.data:[]).map((s:Row)=><option key={s.id} value={s.id}>{s.title}{s.current_revision_id?' · 已有正文':''}</option>)}
            </select></label>
            <div className="wa-wrap"><strong>写作模型</strong><p>{link.data?.model?.name||currentProfiles[0]?.actual_config.model||'读取当前接口设置'}</p><small>自动使用“接口与设置”中的配置。</small></div>
          </div>
          <p className="wa-notice">{link.data?.sections?.find((s:Row)=>s.section_id===sid)?.contract?.goal||'开始时自动从当前项目建立章节写作任务。'}</p>
          {preflight.data?.budget&&<p aria-label="写作预算">项目预算 {Number(preflight.data.budget.limit).toFixed(4)} · 预算占用 {Number(preflight.data.budget.used).toFixed(4)} · 可用 {Number(preflight.data.budget.available).toFixed(4)} {preflight.data.budget.currency}</p>}
          {preflight.error&&<p className="wa-error" role="alert">写作条件读取失败：{preflight.error}</p>}
          {!isEnglishAbstract&&preflight.data?.blockers?.length>0&&<div className="wa-notice" role="alert"><strong>当前章节尚不能启动写作</strong><ul>{preflight.data.blockers.map((item:Row)=><li key={item.code}>{item.message}</li>)}</ul><div className="wa-actions">{onOpenProject&&<button type="button" onClick={onOpenProject}>查看项目预算</button>}{onOpenSettings&&<button type="button" onClick={onOpenSettings}>查看接口设置</button>}{onOpenRules&&<button type="button" onClick={onOpenRules}>查看规则适用范围</button>}<button type="button" onClick={()=>void preflight.reload()}>重新检查写作条件</button></div></div>}
          {preflight.data?.context&&<details><summary>本节输入规模与费用预留</summary><p>首个请求保守估算 {Number(preflight.data.context.input_estimate).toLocaleString()} token，预留输出 {Number(preflight.data.context.reserved_output).toLocaleString()}，上下文上限 {Number(preflight.data.context.limit).toLocaleString()}。本节加载 {preflight.data.context.rule_count} 条规则、{preflight.data.context.evidence_count} 项证据。</p><div className="wa-table-wrap"><table><thead><tr><th>输入项</th><th>保守估算 token</th></tr></thead><tbody>{[['rules','适用规则'],['evidence','证据原文'],['references','文献信息'],['contract','章节任务'],['thesis_core','研究目标'],['related_sections','前置章节']].map(([key,name])=><tr key={key}><td>{name}</td><td>{Number(preflight.data.context.field_estimates?.[key]??0).toLocaleString()}</td></tr>)}</tbody></table></div>{(preflight.data.warnings??[]).map((warning:string)=><p key={warning}>{warning}</p>)}</details>}
          <details>
            <summary>高级设置：阶段配置与预算</summary>
            <div className="wa-row">
              <label>阶段配置<select aria-label="阶段配置" value={profile} onChange={e=>P(e.target.value)}>
                <option value="">当前接口设置（自动建立配置）</option>
                {(profiles.data??[]).map((p:Row)=><option key={p.id} value={p.id} disabled={!p.configuration_current}>{p.actual_config.model} · {p.configuration_current?label(p.qualification):'配置已过期，请更新'}</option>)}
              </select></label>
              <label>核验阶段配置（可选）<select aria-label="核验阶段配置（可选）" value={verifyProfile} onChange={e=>VP(e.target.value)}>
                <option value="">沿用生成配置</option>
                {currentProfiles.map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model} · 输出上限 {p.actual_config.max_output_tokens}</option>)}
              </select></label>
              <label>会话预算上限<input aria-label="会话预算上限" type="number" min={0.01} step={0.01} value={budget} onChange={e=>{BE(true);BU(Number(e.target.value));}}/></label>
            </div>
            {profiles.error&&<p role="alert">{profiles.error}；默认写作仍会直接读取当前接口设置。</p>}
            <button type="button" disabled={busy} onClick={()=>void act(async()=>{const p=await post(pid,'/writing-profiles',{role:'draft',configuration:{route:'primary'}});await profiles.reload();P(p.id);})}>使用当前接口设置建立写作配置</button>
            <p className="wa-muted">此操作只保存配置。模型核验资格以实际评估记录为准。</p>
          </details>
          <p>任务会依次调查依据、形成判定、生成正文并进行核验。完成后在下方查看候选，再选择采用。</p>
          <button className="wa-button" disabled={!sid||!state.data||busy||Boolean(activeChapterSession)||(!isEnglishAbstract&&!preflight.data?.can_start)}>{busy?'正在建立写作任务…':isEnglishAbstract?'打开英文摘要翻译':activeChapterSession?'本节写作任务正在进行':candidateCount?'另建候选稿（调用模型）':'开始本节写作（调用模型）'}</button>
        </form>
      </section>
      <section className="wa-card"><details><summary>本节将使用的项目上下文</summary>
        <p>章节任务会读取项目规则、资料片段、文献原文、研究事实、研究产物和已保存正文。</p>
        <div className="wa-row"><span className="wa-badge">规则 {link.data?.rule_package?.rule_count??0} 条</span><span className="wa-badge">资料 {link.data?.materials?.length??0} 份</span><span className="wa-badge">文献 {link.data?.references?.length??0} 条</span><span className="wa-badge">事实 {(link.data?.facts??[]).filter((f:Row)=>f.used_by?.some((s:Row)=>s.id===sid)).length} 项</span></div>
        <p>尚未登记研究事实时，可以依据资料和代码生成候选，缺少的实测结果会列为待补材料。</p>
        {(link.data?.facts??[]).filter((f:Row)=>f.used_by?.some((s:Row)=>s.id===sid)).map((f:Row)=><span className="wa-badge" key={f.id}>{f.label} · {label(f.status)}</span>)}
      </details></section>
      <section className="wa-card"><label>会话<select aria-label="写作会话" value={session} onChange={e=>SE(e.target.value)}><option value="">选择已有写作会话</option>{(sessions.data??[]).filter((s:Row)=>s.section_id===sid).map((s:Row)=><option key={s.id} value={s.id}>{s.created_at} · {label(s.status)} · {s.id.slice(0,8)}</option>)}</select></label></section>
      <div id="writing-session-review" tabIndex={-1}><WritingSessionPanel key={pid+':'+session} pid={pid} sessionId={session} epoch={version} onChange={refresh} onError={ERR} onOpenEditor={onOpenEditor}/></div>
    </>}
    {tab==='paper'&&<div className="wa-stack"><section className="wa-card"><h3>全文覆盖与论证复核</h3><div className="wa-table-wrap"><table><thead><tr><th>章节</th><th>正文</th><th>句子分析</th><th>待复查</th></tr></thead><tbody>{(coverage.data?.sections??[]).map((s:Row)=><tr key={s.section_id}><td>{s.title}</td><td>{s.revision_id?'已有版本':'尚未写作'}</td><td>{label(s.analysis_state)}{s.coverage?`（${s.coverage.total}句，${s.coverage.unclassified}句未分类）`:''}</td><td>{s.stale?'需要复核':'未标记过期'}</td></tr>)}</tbody></table></div>
      <label>复核模型<select value={profile} onChange={e=>P(e.target.value)}><option value="">请选择</option>{(profiles.data??[]).map((p:Row)=><option key={p.id} value={p.id}>{p.actual_config.model}</option>)}</select></label><button disabled={!profile||busy} onClick={()=>void act(()=>post(pid,'/writing-reviews',{profile_id:profile}))}>排队检查当前全文论证（调用模型）</button>
      <div className="wa-actions"><button disabled={busy} onClick={()=>void act(async()=>R(await get(pid,'/writing-export-report')))}>查看草稿交付缺口</button><button disabled={busy} onClick={()=>void act(async()=>R(await get(pid,'/writing-export-report?strict=true')))}>核对严格交付条件</button></div>
      {report&&<div><h4>{report.unresolved.length?'仍有未决项':'当前检查未列未决项'}</h4><ul>{report.unresolved.map((p:Row,i:number)=><li key={i}>{p.message}</li>)}</ul>{(report.paper_reviews??[]).map((r:Row)=><details key={r.id}><summary>全文复核 · {label(r.status)}</summary><p>{r.result.summary}</p><ul>{r.result.findings.map((f:Row,i:number)=><li key={i}>{f.message}</li>)}</ul></details>)}</div>}
      <details><summary>人工通读与跨范围复核</summary><p>模型无法可靠覆盖的关系可以人工核对；此记录只代表明确的人工声明。</p><label>核对依据与处理过的问题<textarea value={paperNote} onChange={e=>PN(e.target.value)} placeholder="写明核对了研究问题与结论、实现与测试、章节条件，以及哪些模型疑点已处理"/></label><label className="wa-check"><input type="checkbox" checked={readAll} onChange={e=>RA(e.target.checked)}/>我已通读当前全部章节，并核对跨章论证与未决问题</label><button disabled={busy||!readAll||paperNote.trim().length<20} onClick={()=>void act(()=>post(pid,'/writing-reviews/human',{all_sections_reviewed:true,note:paperNote,reviewed_revisions:Object.fromEntries((coverage.data?.sections??[]).filter((s:Row)=>s.revision_id).map((s:Row)=>[s.section_id,s.revision_id]))}))}>登记当前版本的人工全文复核</button></details>
      <p>文件生成请使用原有“排版与交付”；导出包会附上当前证据与核验报告。</p>
    </section><StaleReasonsPanel {...props} sections={coverage.data?.sections??[]}/><JointReviewPanel {...props}/></div>}
  </div>;
}

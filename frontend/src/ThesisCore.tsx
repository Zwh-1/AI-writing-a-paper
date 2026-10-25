import {useState} from 'react';
import {post,useResource,label,Row} from './writingShared';

type Props={pid:string;epoch:number;onChange:()=>void;onError:(s:string)=>void;onOpenResearch?:()=>void;onSelectSection?:(sid:string)=>void};

export default function ThesisCore({pid,epoch,onChange,onError,onOpenResearch,onSelectSection}:Props){
  const link=useResource(pid,'/writing-link',epoch);const [busy,setBusy]=useState(false);
  const data=link.data;const readiness=data?.readiness??{};const stats=data?.stats??{};const sections=(data?.sections??[]) as Row[];const facts=(data?.facts??[]) as Row[];const artifacts=(data?.artifacts??[]) as Row[];
  async function bootstrap(){setBusy(true);try{await post(pid,'/writing-bootstrap');onChange();}catch(e){onError((e as Error).message);}finally{setBusy(false);}}
  const status=(ready:boolean,good:string,empty:string)=>ready?<span className="wa-status wa-status-ready">{good}</span>:<span className="wa-status">{empty}</span>;
  return <div className="wa-stack">
    <section className="wa-card wa-project-hero"><div><span className="wa-eyebrow">当前项目</span><h3>{data?.project?.title||'正在读取项目…'}</h3><p>{data?.research_question||'项目还没有明确研究问题，请先在项目概览中补充研究简报。'}</p><small>{data?.project?.paper_type||''} · {stats.sections??0} 个章节 · {stats.files??0} 份资料</small></div><div className="wa-actions"><button className="wa-button" disabled={busy||!sections.length} onClick={()=>void bootstrap()}>{busy?'正在读取项目…':readiness.core_ready?'同步当前项目写作基线':'从当前项目建立写作基线'}</button><button onClick={onOpenResearch}>补充研究成果</button></div></section>
    <section className="wa-card"><div className="wa-row"><div><h3>项目写作准备度</h3><p>状态来自当前项目数据，不需要在这里重复填写论文信息。</p></div>{readiness.blockers?.length?<span className="wa-status">有待补充项</span>:<span className="wa-status wa-status-ready">可以开始组织写作</span>}</div>
      <div className="wa-readiness-grid">
        <div><strong>研究简报</strong>{status(Boolean(data?.project?.brief?.question||data?.research_question),data?.project?.brief?.question?'已读取项目研究问题':'已根据项目提纲形成写作目标','项目缺少写作目标')}</div>
        <div><strong>论文提纲</strong>{status(Boolean(sections.length),'已读取章节结构','尚未建立提纲')}</div>
        <div><strong>资料依据</strong>{status(Boolean(stats.files),'已读取项目资料','尚未导入资料')}</div>
        <div><strong>研究事实</strong>{status(Boolean(facts.length),'已登记 '+facts.length+' 项事实','尚未登记研究事实')}</div>
        <div><strong>研究产物</strong>{status(Boolean(artifacts.length),'已登记 '+artifacts.length+' 项产物','尚未登记研究产物')}</div>
        <div><strong>代码快照</strong>{status(Boolean(readiness.snapshot||data?.state?.target_snapshot_id),'已有可引用快照','尚未冻结代码快照')}</div>
        <div><strong>论文规则</strong>{status(Boolean(data?.project?.rule_package_id),'已加载论文规则','尚未发布论文规则')}</div>
      </div>
      {readiness.blockers?.length>0&&<ul className="wa-project-blockers">{readiness.blockers.map((item:string)=><li key={item}>{item}</li>)}</ul>}
    </section>
    <section className="wa-card"><div className="wa-row"><div><h3>项目资料、规则与文献已接入</h3><p>这些内容会随章节任务进入写作上下文，写作不再使用独立于项目之外的空白证据。</p></div><span className="wa-status wa-status-ready">已连接项目数据</span></div><div className="wa-project-evidence"><div><strong>资料库</strong><p>{data?.materials?.length??0} 份项目资料 · {(data?.materials??[]).reduce((n:number,item:Row)=>n+(item.source_count??0),0)} 个原文片段</p><details><summary>查看资料清单</summary><ul>{(data?.materials??[]).slice(0,8).map((item:Row)=><li key={item.id}>{item.name}<small>{item.source_count??0} 个片段 · {item.status}</small></li>)}</ul></details></div><div><strong>论文规则</strong><p>{data?.rules?.length??0} 条规则 · {data?.rule_package?`已发布第 ${data.rule_package.version} 版规则包`:'尚未发布规则包'}</p><details><summary>查看当前规则</summary><ul>{(data?.rules??[]).slice(0,8).map((item:Row)=><li key={item.id}>{item.statement}<small>{label(item.modality)} · {item.scope}</small></li>)}</ul></details></div><div><strong>文献与术语</strong><p>{data?.references?.length??0} 条文献记录</p><details><summary>查看文献清单</summary><ul>{(data?.references??[]).slice(0,8).map((item:Row)=><li key={item.id}>{item.metadata?.title||'未命名文献'}<small>{item.source_count??0} 个原文片段</small></li>)}</ul></details></div></div></section>
    <section className="wa-card"><div className="wa-row"><div><h3>当前项目章节</h3><p>每个章节的目标、正文、事实和产物均从当前项目读取；选择章节后直接进入写作与核验。</p></div><span>{readiness.contracts_ready??0}/{sections.length} 个章节已有写作基线</span></div>
      {sections.length?<div className="wa-table-wrap"><table><thead><tr><th>章节</th><th>项目中的章节目标</th><th>正文</th><th>项目依据</th><th>操作</th></tr></thead><tbody>{sections.map(s=><tr key={s.section_id}><td><strong>{s.title}</strong></td><td>{s.goal||'尚未填写章节目标'}</td><td>{s.revision_id?'已有正文':'待写作'}{s.stale&&<span className="wa-status">待复查</span>}</td><td>{s.fact_ids?.length??0} 项事实 · {s.artifact_ids?.length??0} 项产物 · {s.source_ids?.length??0} 项证据</td><td><button onClick={()=>onSelectSection?.(s.section_id)}>进入本节写作</button></td></tr>)}</tbody></table></div>:<div className="wa-empty-project"><strong>当前项目还没有章节</strong><p>先在“提纲与计划”建立项目章节，再回来进行有据写作。</p></div>}
    </section>
    <section className="wa-card"><h3>项目依据摘要</h3><div className="wa-project-evidence"><div><strong>研究事实</strong>{facts.length?<ul>{facts.slice(0,8).map(f=><li key={f.id}>{f.label}<small>{label(f.status)} · {label(f.verification)}</small></li>)}</ul>:<p>项目中还没有登记研究事实。</p>}</div><div><strong>研究产物</strong>{artifacts.length?<ul>{artifacts.slice(0,8).map(a=><li key={a.id}>{a.name}<small>{a.kind}</small></li>)}</ul>:<p>项目中还没有登记研究产物。</p>}</div></div></section>
  </div>;
}

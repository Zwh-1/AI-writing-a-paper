import { useEffect, useState } from "react";
import { FileText, LoaderCircle, Search } from "lucide-react";
import { api } from "./client";
import SourceCard from './workspace/SourceCard';
import ProductLink from './products/ProductLink';

type Row = Record<string, any>;
const roles: Record<string, string> = {
  rules: "论文规则", example: "写作示例", research: "研究文献",
  data: "研究数据", draft: "已有草稿", supervisor: "导师意见",
};

export default function WritingSources({ pid, sid, tick, onOpen, onMaterials, location }: {
  pid: string; sid: string; tick: number;
  onOpen: (file: Row) => void; onMaterials: () => void;
  location: (source: Row) => string;
}) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ items: Row[]; mode: string; products?: Row[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setResult(null);
    if(!pid||!sid){setLoading(false);return()=>controller.abort();}
    api(`/projects/${pid}/sections/${sid}/materials?q=${encodeURIComponent(query)}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setResult(data); })
      .catch(reason => { if (!controller.signal.aborted) setError((reason as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [pid, sid, tick, query, attempt]);
  const search = (value: string) => { setQuery(value.trim()); setAttempt(value => value + 1); };
  return <section aria-label="本节资料依据" aria-busy={loading}>
    <p className="muted small">自动加载本节已关联原文，也可检索项目资料。检索相关不等同于论断已核实。</p>
    <form className="mini-search" role="search" onSubmit={event => { event.preventDefault(); search(input); }}>
      <input aria-label="资料关键词" value={input} onChange={event => setInput(event.target.value)} placeholder="关键词或研究问题" />
      <button className="icon-button" type="submit" aria-label="检索资料"><Search size={16} /></button>
    </form>
    {query && <button className="text-button" onClick={() => { setInput(""); search(""); }}>返回本节资料</button>}
    {loading ? <div className="small-empty" role="status"><LoaderCircle className="spin" size={20} /><p>正在加载资料依据…</p></div>
      : error ? <div className="small-empty" role="alert"><p>资料加载失败：{error}</p><button className="btn" onClick={() => setAttempt(value => value + 1)}>重新加载资料</button></div>
      : <>
        {!!result?.items.length && <p className="muted small" role="status">{result.mode === "linked" ? "本节已关联原文" : result.mode === "search" ? "检索结果" : "最近导入的原文"} · {result.items.length} 项</p>}
        <div className="writing-source-results">{result?.items.map(source => <details className="evidence-card" key={source.id}>
          <summary><FileText size={15} />{source.name}<small>{roles[source.role] ?? "项目资料"} · {location(source)}{source.linked ? " · 本节已关联" : ""}</small></summary>
          <p>{source.text}</p>
          <button className="btn" onClick={() => onOpen({ id: source.file_id, name: source.name, extension: source.extension, role: source.role, project_id: pid, initialPage: source.page_number })}>查看原文</button>
          <button className="btn" onClick={() => window.dispatchEvent(new CustomEvent("paper-attach-source", { detail: { sectionId: sid, sourceId: source.id } }))}>关联当前段落</button>
          <small>来源 ID：{source.id}</small>
        </details>)}</div>
        {!!result?.products?.length&&<div className="writing-source-results" aria-label="本章互通材料">{result.products.map(item=><details className="evidence-card" key={item.id}><summary>{item.data.title}<small>{item.source_state==='stale'?'来源已变化，请更新':'已关联本章写作 · 内容仍需核对'}</small></summary><p>{item.data.text}</p>{item.source_refs.map((ref:any)=><SourceCard key={ref.type+ref.id} pid={pid} source={ref}/>)}</details>)}</div>}
        {!result?.items.length && <div className="small-empty"><Search size={24} /><p>{query ? "未找到匹配原文，请换用关键词检索。" : "本节暂无可用原文，请在资料中心导入或核对解析结果。"}</p></div>}
      </>}
    <button className="text-button" onClick={onMaterials}>前往资料中心</button>
    <ProductLink pid={pid} sectionId={sid} label="从产物库关联本章资料"/>
  </section>;
}

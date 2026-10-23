import {useEffect,useState} from 'react';
import {get,Row} from './writingShared';
import {sentenceSelection} from './editorSentenceRanges';
import ClaimEvidencePanel from './ClaimEvidencePanel';

function canonical(node:any):string{
  if(node.isText)return node.text??'';
  if(node.type.name==='hardBreak')return '\n';
  if(['citation','formula','crossReference','footnote'].includes(node.type.name))return '\ufffc';
  let value='';node.forEach((child:any)=>{value+=canonical(child);});return value;
}
export default function SentenceLocator({pid,revisionId,editor,disabled}:{pid:string;revisionId:string|null;editor:any;disabled:boolean}){
  const [data,D]=useState<Row|null>(null),[error,E]=useState(''),[open,O]=useState(false);
  useEffect(()=>{D(null);E('');O(false);},[pid,revisionId]);
  async function load(){try{D(await get(pid,`/revisions/${revisionId}/analysis`));O(true);E('');}catch(e){E((e as Error).message);}}
  function locate(s:Row){
    if(disabled||!editor)return;
    let found=false;
    editor.state.doc.descendants((node:any,pos:number)=>{
      if(found||node.attrs.nodeId!==s.node_id)return;
      const text=canonical(node),range=sentenceSelection(text,s.start_cp,s.end_cp);
      if(range.to>node.content.size){E('句子与当前编辑器版本不匹配，请保存并重新分析。');return;}
      editor.chain().focus().setTextSelection({from:pos+1+range.from,to:pos+1+range.to}).scrollIntoView().run();found=true;
    });
    if(!found)E('当前正文中无法定位该节点，请核对版本。');
  }
  return <section style={{padding:'10px 20px',borderBottom:'1px solid #e2e6eb'}}><button disabled={disabled||!revisionId} onClick={()=>void load()}>查看并定位当前版本的逐句核验</button>{disabled&&<span> 请先保存正文，避免使用旧位置。</span>}{error&&<p role="alert">{error}</p>}{open&&data&&<details open><summary>逐句依据（所加载正文版本）</summary><div style={{display:'flex',gap:6,flexWrap:'wrap',margin:'10px 0'}}>{data.analysis.sentences.map((s:Row,i:number)=><button key={s.id} title={s.text} disabled={disabled} onClick={()=>locate(s)}>定位第{i+1}句</button>)}</div><ClaimEvidencePanel pid={pid} analysis={data.analysis}/></details>}</section>;
}

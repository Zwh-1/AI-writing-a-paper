import {useRef,useState} from 'react';
import {Row} from './writingShared';

export default function DiagnosticReviewForm({dimensions,onSubmit,busy=false,defaultEvaluator=''}:{dimensions:Row;onSubmit:(body:Row)=>Promise<void>;busy?:boolean;defaultEvaluator?:string}){
  const [scores,S]=useState<Record<string,string>>({}),[rationales,R]=useState<Record<string,string>>({}),[note,N]=useState(''),[evaluator,E]=useState(defaultEvaluator),[decision,D]=useState('');
  const start=useRef(Date.now());
  const ready=Object.keys(dimensions).length>0&&!!decision&&!!evaluator.trim()&&note.trim().length>=8&&Object.keys(dimensions).every(key=>scores[key]!==undefined&&scores[key]!==''&&(rationales[key]??'').trim().length>=4);
  return <form onSubmit={e=>{e.preventDefault();if(!ready)return;void onSubmit({decision,evaluator_id:evaluator.trim(),note,duration_seconds:Math.max(0,(Date.now()-start.current)/1000),dimensions:Object.fromEntries(Object.keys(dimensions).map(key=>[key,{score:scores[key]==='na'?null:Number(scores[key]),applicable:scores[key]!=='na',rationale:rationales[key]}]))});}}>
    <label>评审人标识<input value={evaluator} onChange={e=>E(e.target.value)} placeholder="填写你自己的评审标识" required/></label>
    <div className="wa-stack">{Object.entries(dimensions).map(([key,title])=><fieldset key={key}><legend>{String(title)}</legend><label>评分<select aria-label={`${title}评分`} value={scores[key]??''} onChange={e=>S({...scores,[key]:e.target.value})}><option value="">请选择</option><option value="0">0 · 错误或未满足</option><option value="1">1 · 部分满足</option><option value="2">2 · 给定范围内满足</option><option value="na">不适用</option></select></label><label>具体依据<input value={rationales[key]??''} onChange={e=>R({...rationales,[key]:e.target.value})} placeholder="指出原句、条件或不适用的理由"/></label></fieldset>)}</div>
    <label>总体判断<select aria-label="总体判断" value={decision} onChange={e=>D(e.target.value)}><option value="">请选择</option><option value="pass">通过</option><option value="fail">不通过</option><option value="unknown">无法判断</option></select></label>
    <label>本次核对依据<textarea value={note} onChange={e=>N(e.target.value)}/></label><button disabled={busy||!ready}>保存我的分项审阅</button>
  </form>;
}

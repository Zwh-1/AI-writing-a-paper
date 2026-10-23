import {useCallback,useEffect,useRef,useState} from 'react';
import {api,send} from './client';
export type Row=Record<string,any>;
export const parse=(v:any,f:any={})=>{try{return typeof v==='string'?JSON.parse(v):(v??f);}catch{return f;}};
export const labels:Record<string,string>={queued:'排队中',running:'处理中',needs_review:'等待审阅',needs_evidence:'待补证据',budget_blocked:'预算不足',outcome_unknown:'远端结果未知',completed:'已完成',cancelled:'已取消',failed:'失败',stale:'需要复核',ready:'已冻结',unstable:'来源变化',current:'当前有效',revoked:'已撤回',historical:'历史版本',unverified:'未核验',model_reviewed:'模型辅助检查',human_reviewed:'人工已核对',incomplete:'覆盖不完整',unknown:'待判断',partial:'部分支持',supported:'有支持依据',unsupported:'不支持',contradicted:'存在矛盾',unassessed:'未评估',qualified_for_policy:'特定策略下已评估',code_observation:'源码观察',test_definition:'测试定义',test_run:'运行记录',deployment:'部署记录',literature:'文献',author_statement:'作者说明',investigate:'调查来源',judgment:'形成判定',judgment_check:'核对判定',draft:'生成正文',annotation:'逐句关联',semantic:'检查含义',coherence:'检查衔接'};
export const label=(v:string)=>labels[v]??v;
Object.assign(labels,{fact:'事实描述',purpose:'研究目的',inference:'推论',transition:'结构过渡',abandoned_unknown:'已保留原未知请求',model_assisted:'模型辅助',regression:'旧稿与新稿回归',needs_revision:'需要修改'});
export const path=(pid:string,p:string)=>'/projects/'+pid+p;
export const get=(pid:string,p:string)=>api(path(pid,p));
export const post=(pid:string,p:string,b:unknown={},method='POST')=>send(path(pid,p),b,method);
export function useResource(pid:string,p:string|null,epoch=0){
  const [data,setData]=useState<any>(null),[error,setError]=useState('');
  const serial=useRef(0);
  const reload=useCallback(async()=>{if(!pid||!p)return;const n=++serial.current;try{const d=await get(pid,p);if(n===serial.current){setData(d);setError('');}}catch(e){if(n===serial.current)setError((e as Error).message);}},[pid,p]);
  useEffect(()=>{serial.current++;setData(null);},[pid,p]);
  useEffect(()=>{void reload();},[reload,epoch]);
  return {data,error,reload};
}

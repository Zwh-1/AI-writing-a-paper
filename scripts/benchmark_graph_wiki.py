"""Small, explicitly scoped latency/RSS probe; not a semantic accuracy test."""
import argparse,json,runpy,time,threading,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,knowledge_router
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project');parser.add_argument('--rounds',type=int,default=2);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--without-originals',action='store_true');args=parser.parse_args()
    if not 1<=args.rounds<=5:raise ValueError('rounds must be 1..5')
    pid=args.project or db.one('SELECT id FROM projects WHERE archived=0')['id'];queries=['资料有哪些明确的适用条件与限制？','这些研究采用了哪些方法和比较对象？'];samples=[];done=threading.Event();memory=[]
    try:
        import psutil
        proc=psutil.Process()
        def watch():
            while not done.wait(.1):memory.append(proc.memory_info().rss)
        thread=threading.Thread(target=watch,daemon=True);thread.start()
    except ImportError:thread=None
    try:
        for round_id in range(args.rounds):
            for query in queries:
                start=time.perf_counter();result=knowledge_router.query(pid,{'query':query,'namespace':'literature','include_originals':not args.without_originals})
                sample={'round':round_id,'query':query,'seconds':time.perf_counter()-start,'reading_ranges':len(result['reading_plan']),'reading_documents':len({p.get('file_id') or p.get('evidence_id') for p in result['reading_plan']}),'wiki_pages':len(result['wiki']),'graph_claims':len(result['graph']['assertions']),'timings_ms':result.get('timings_ms',{}),'returned_rerank_cache_hits':sum(bool(r.get('rerank_cache_hit')) for r in result.get('originals',[]))}
                samples.append(sample);print(json.dumps({'sample':sample},ensure_ascii=False),flush=True)
    finally:
        done.set()
        if thread:thread.join(timeout=1)
    values=sorted(s['seconds'] for s in samples);p95=values[min(len(values)-1,__import__('math').ceil(.95*len(values))-1)]
    result={'samples':samples,'n':len(samples),'p50_seconds':statistics.median(values),'p95_seconds':p95,'query_process_peak_rss_bytes':max(memory) if memory else None,
            'scope':'native_graph_wiki_and_original_retriever' if not args.without_originals else 'native_graph_wiki_only','semantic_quality_verified':False,'note':'小样本进程内冷/热探针；不含远端模型生成，不是全库性能或95%语义准确率保证'}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='samples'},ensure_ascii=False))
if __name__=='__main__':main()

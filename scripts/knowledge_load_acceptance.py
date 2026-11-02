"""Bounded real local retrieval load probe; no model generation or data edits."""
import argparse,concurrent.futures,json,math,os,runpy,statistics,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,knowledge_router

def summarize(samples,errors,seconds,workers,elapsed,state,include_originals=False,question_count=2):
    values=sorted(s['seconds'] for s in samples)
    windows=[0]*math.ceil(seconds/60)
    for sample in samples:
        index=int(sample.get('finished_seconds',0)//60)
        if index<len(windows):windows[index]+=1
    completed_windows=min(len(windows),int(elapsed//60))
    timeline=[0,*sorted(s.get('finished_seconds',0) for s in samples),elapsed]
    gap=max((b-a for a,b in zip(timeline,timeline[1:])),default=elapsed)
    continuous=bool(samples) and gap<=120 and all(n>0 for n in windows[:completed_windows])
    return {'state':state,'pid':os.getpid(),'updated_at':db.now(),'seconds_requested':seconds,'elapsed_seconds':elapsed,
        'workers':workers,'queries':len(samples),'errors':errors,'question_count':question_count,
        'local_thread_limits':{k:os.environ.get(k) for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS')},
        'distinct_questions_executed':len({s.get('question_index') for s in samples}),
        'p50':statistics.median(values) if values else None,'p95':values[min(len(values)-1,math.ceil(.95*len(values))-1)] if values else None,
        'scope':'graph_wiki_plus_independent_neural_original_retrieval' if include_originals else 'native_graph_wiki_without_independent_neural_retrieval',
        'successful_queries_per_minute':windows[:completed_windows],'maximum_success_gap_seconds':gap,
        'continuous_load_observed':continuous,'paid_model_calls':0,
        'long_term_24h_accepted':state=='completed' and seconds>=86400 and elapsed>=86400 and continuous and not errors,
        'clean_machine_accepted':False}


def checkpoint(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def main():
    p=argparse.ArgumentParser();p.add_argument('--project');p.add_argument('--seconds',type=int,default=60);p.add_argument('--workers',type=int,default=2);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--include-originals',action='store_true');p.add_argument('--queries-from-cases',action='store_true');a=p.parse_args()
    if not 10<=a.seconds<=86400 or not 1<=a.workers<=8:raise ValueError('seconds=10..86400, workers=1..8')
    pid=a.project or db.one('SELECT id FROM projects WHERE archived=0')['id'];started=time.monotonic();deadline=started+a.seconds;samples=[];lock=threading.Lock();errors=[];last_checkpoint=started
    stop_file=a.output.with_suffix('.stop');failure_stop=threading.Event()
    questions=['资料有哪些明确的适用条件与限制？','这些研究采用了哪些方法和比较对象？']
    if a.queries_from_cases:
        questions=list(dict.fromkeys(r['question'] for r in db.rows('SELECT question FROM knowledge_evaluation_cases WHERE project_id=? ORDER BY id',(pid,)) if r['question'].strip()))
        if not questions:raise ValueError('项目中没有负载输入问题；负载请求不代表人工准确率评估')
    def save(state):
        result=summarize(samples,errors,a.seconds,a.workers,time.monotonic()-started,state,a.include_originals,len(questions))
        result.update(project_id=pid,workload_source='unlabelled_evaluation_case_questions' if a.queries_from_cases else 'two_fixed_retrieval_queries',
                      human_accuracy_evaluated=False,stop_file=str(stop_file))
        checkpoint(a.output,result)
        return result
    save('running')
    def work(index):
        nonlocal last_checkpoint
        i=index
        while time.monotonic()<deadline and not stop_file.exists() and not failure_stop.is_set():
            request_started=time.perf_counter()
            try:
                result=knowledge_router.query(pid,{'query':questions[i%len(questions)],'namespace':'literature','include_originals':a.include_originals},remote=True)
                record={'worker':index,'seconds':time.perf_counter()-request_started,'finished_seconds':time.monotonic()-started,
                        'question_index':i%len(questions),'ranges':len(result['reading_plan'])}
                with lock:samples.append(record)
            except Exception as error:
                with lock:errors.append({'worker':index,'error_type':type(error).__name__})
                failure_stop.set()
            with lock:
                if time.monotonic()-last_checkpoint>=30:
                    save('running');last_checkpoint=time.monotonic()
            i+=a.workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as executor:list(executor.map(work,range(a.workers)))
    result=save('cancelled' if stop_file.exists() else 'failed' if failure_stop.is_set() else 'completed');print(json.dumps(result,ensure_ascii=False))
    return int(bool(errors) or not samples or result['state']!='completed' or a.seconds>=86400 and not result['long_term_24h_accepted'])
if __name__=='__main__':raise SystemExit(main())

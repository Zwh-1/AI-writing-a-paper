"""Private actual-material acceptance, with all reviews left untouched."""
from pathlib import Path
import os,sys,json,sqlite3,shutil,datetime,time
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data';stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d-%H%M%S');OUT=SOURCE/'v030-acceptance'/stamp;CLONE=OUT/'isolated-data';CLONE.mkdir(parents=True)
src=sqlite3.connect(SOURCE/'workbench.sqlite3');dst=sqlite3.connect(CLONE/'workbench.sqlite3');src.backup(dst);dst.close();src.close();shutil.copytree(SOURCE/'objects',CLONE/'objects')
os.environ['PAPER_DATA_DIR']=str(CLONE);os.environ.pop('PAPER_API_KEY',None);os.environ.pop('PAPER_API_KEY_FILE',None);sys.path[:0]=[str(ROOT/'.runtime/advanced-deps'),str(ROOT/'.runtime/python-deps'),str(ROOT/'backend')]
from app import db,retrieval,office_renderer
from app.model_install import status

def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
db.init();project=db.one('SELECT p.*,COUNT(f.id) n FROM projects p LEFT JOIN files f ON f.project_id=p.id GROUP BY p.id ORDER BY n DESC LIMIT 1');pid=project['id'];cfg=retrieval.config(pid);db.set_setting('retrieval:'+pid,{**cfg,'enabled':True,'cross_encoder':'BAAI/bge-reranker-base','rerank':'cosine'})
print('Indexing real material clone...',flush=True);start=time.monotonic();indexed=retrieval.build_index(pid,lambda done,total:print(f'index {done}/{total}',flush=True) if done%160==0 else None);indexed['elapsed_seconds']=round(time.monotonic()-start,3);save('real-index.json',indexed)
dataset=json.loads((SOURCE/'acceptance-2026-10-21/run-100803/retrieval-candidates.json').read_text(encoding='utf-8'));cases=dataset['cases'];valid=[];invalid=[]
for c in cases:
 if all(db.one('SELECT id FROM sources WHERE id=?',(sid,)) for sid in c['expected_source_ids']):valid.append(c)
 else:invalid.append(c['id'])
for mode in ('cosine','cross_encoder'):
 db.set_setting('retrieval:'+pid,{**retrieval.config(pid),'rerank':mode});print('Benchmark '+mode,flush=True);start=time.monotonic();r=retrieval.evaluate(pid,valid,progress=lambda i,n:print(f'{mode} {i+1}/{n}',flush=True));r.update({'candidate_cases':len(cases),'invalid_original_source_labels':invalid,'human_confirmed_cases':0,'label_origin':'assistant_prepared_pending_human_confirmation','elapsed_seconds':round(time.monotonic()-start,3)});save('real-benchmark-'+mode+'.json',r)
 print(mode,json.dumps(r['summary']),flush=True)
files=db.rows("SELECT * FROM files WHERE project_id=? AND extension='.pptx' AND active=1 ORDER BY pages LIMIT 1",(pid,))
if files:
 try:
  print('Rendering actual PPT locally: '+files[0]['name'],flush=True);receipt=office_renderer.render_file(files[0]);save('actual-ppt-render.json',{'filename':files[0]['name'],**receipt});image=office_renderer.page_image(files[0],1);shutil.copyfile(image,OUT/'actual-ppt-page-1.png')
 except Exception as error:save('actual-ppt-render.json',{'filename':files[0]['name'],'passed':False,'error':str(error)})
save('models.json',[status(retrieval.DEFAULT_MODEL),status('BAAI/bge-reranker-base')]);save('scope.json',{'original_data_directory':str(SOURCE),'isolated_data_directory':str(CLONE),'original_data_not_modified':True,'original_credentials_copied':False,'model_api_requests':0,'human_reviews_added':0,'full_thesis_acceptance':False});print(str(OUT),flush=True)

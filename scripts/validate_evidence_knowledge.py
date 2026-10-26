"""Audit real originals in a database copy. Never writes the source data directory."""
import argparse,hashlib,json,os,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--source-data',type=Path,default=ROOT/'data')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--vectors',action='store_true',help='Run installed local ONNX models; never downloads or calls a paid API')
args=parser.parse_args();source=args.source_data.resolve();output=args.output.resolve()
if output==source or source in output.parents:
    raise SystemExit('Audit output must be outside the source data directory.')
output.mkdir(parents=True,exist_ok=False)
os.environ['PAPER_DATA_DIR']=str(output)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
with sqlite3.connect((source/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True) as original:
    copy=sqlite3.connect(output/'workbench.sqlite3')
    try:original.backup(copy)
    finally:copy.close()
from app import db,security,model_install
from app import knowledge_units as units,knowledge_service as service
from app.knowledge_evaluation import samples,evaluate
original_safe=security.safe_object
def safe(sha):
    local=original_safe(sha)
    return local if local.exists() else source/'objects'/sha
def put(data):
    sha=hashlib.sha256(data).hexdigest();path=original_safe(sha)
    if not path.exists():path.write_bytes(data)
    return sha
security.safe_object=safe;security.put_blob=put
db.init()
if not args.vectors:
    original_status=model_install.status
    model_install.status=lambda name:{**original_status(name),'ready':False}
report={'source_database':str(source/'workbench.sqlite3'),'database_copy':str(output/'workbench.sqlite3'),'vectors_enabled':args.vectors,'external_requests':False,'human_semantic_acceptance':False,'projects':[]}
for project in db.rows('SELECT id FROM projects WHERE archived=0'):
    pid=project['id'];before={table:units.digest(db.rows('SELECT * FROM '+table+' ORDER BY id')) for table in ('files','sources','bibliography','revisions','citations')}
    def progress(phase,percent):print(json.dumps({'project':pid,'phase':phase,'progress':percent},ensure_ascii=False),flush=True)
    started=time.perf_counter();result=service.build_project(pid,{},progress)
    seed=samples(pid)
    comparison=evaluate(pid,{'cases':seed['cases'][:20]}) if seed['cases'] else None
    after={table:units.digest(db.rows('SELECT * FROM '+table+' ORDER BY id')) for table in before}
    report['projects'].append({'project_id':pid,'build':result,'seconds':round(time.perf_counter()-started,3),'overview':units.overview(pid),
        'original_records_unchanged':before==after,'record_fingerprints_before':before,'record_fingerprints_after':after,
        'comparison':comparison,'evaluation_annotation':'machine_seed_needs_human_question_and_label'})
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
if any(not p['original_records_unchanged'] or p['build']['failures'] for p in report['projects']):
    raise SystemExit('Audit recorded extraction failures or changed canonical records; see report.json')
print(json.dumps({'report':str(output/'report.json'),'projects':len(report['projects']),'human_semantic_acceptance':False},ensure_ascii=False),flush=True)

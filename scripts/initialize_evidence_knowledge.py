"""Back up the live local database, then incrementally build evidence indexes."""
import argparse,hashlib,json,os,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--data-directory',type=Path,default=ROOT/'data')
parser.add_argument('--report',type=Path,default=ROOT/'.runtime/evidence-knowledge-live-init.json')
args=parser.parse_args();data=args.data_directory.resolve();os.environ['PAPER_DATA_DIR']=str(data)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
backup_dir=data/'backups';backup_dir.mkdir(parents=True,exist_ok=True)
backup_path=backup_dir/('before-evidence-knowledge-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite3')
with sqlite3.connect((data/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True) as source:
    destination=sqlite3.connect(backup_path)
    try:source.backup(destination)
    finally:destination.close()
from app import db,tasks
from app.knowledge_service import build_project
from app.knowledge_units import overview
db.init()
def fingerprint(table):
    value=hashlib.sha256()
    with db.connect() as connection:
        for row in connection.execute('SELECT * FROM '+table+' ORDER BY id'):
            value.update(db.dumps(dict(row)).encode('utf-8'));value.update(b'\n')
    return value.hexdigest()
tables=('files','sources','bibliography','revisions','citations')
before={table:fingerprint(table) for table in tables}
report={'backup':str(backup_path),'external_requests':False,'human_semantic_acceptance':False,'projects':[]}
for project in db.rows('SELECT id FROM projects WHERE archived=0'):
    pid=project['id'];job=tasks.enqueue(pid,'evidence_knowledge_build',{'preserve_head':True,'initialization':True})
    db.execute("UPDATE tasks SET status='running',attempt=attempt+1,heartbeat=?,lease_until=? WHERE id=?",(time.time(),time.time()+120,job['id']))
    def progress(phase,percent):
        tasks.event(job['id'],phase,percent)
        print(json.dumps({'project_id':pid,'phase':phase,'progress':percent},ensure_ascii=False),flush=True)
    try:
        result=build_project(pid,{'preserve_head':True},progress)
        status='succeeded' if not result['failures'] else 'incomplete'
        db.execute('UPDATE tasks SET status=?,result=?,phase=?,progress=100,updated_at=? WHERE id=?',(status,db.dumps(result),'结构化原文及本地索引构建结束',db.now(),job['id']))
        report['projects'].append({'project_id':pid,'task_id':job['id'],'status':status,'result':result,'overview':overview(pid)})
    except Exception as error:
        status='cancelled' if isinstance(error,InterruptedError) else 'failed'
        db.execute('UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?',(status,str(error)[:2000],db.now(),job['id']))
        report['projects'].append({'project_id':pid,'task_id':job['id'],'status':status,'error':str(error)})
        raise
    finally:
        report['canonical_fingerprints_before']=before
        report['canonical_fingerprints_after']={table:fingerprint(table) for table in tables}
        report['original_records_unchanged']=report['canonical_fingerprints_before']==report['canonical_fingerprints_after']
        args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(args.report),'backup':str(backup_path),'original_records_unchanged':report.get('original_records_unchanged')},ensure_ascii=False),flush=True)

"""Correct document purposes with a backup and immutable-record checks."""
import argparse,hashlib,json,os,sqlite3,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--apply',action='store_true')
parser.add_argument('--data-directory',type=Path,default=ROOT/'data')
parser.add_argument('--inventory',type=Path,default=ROOT/'.runtime/corpus-reading-audit-20261026/inventory.json')
parser.add_argument('--report',type=Path,default=ROOT/'.runtime/document-reading-live-init.json')
args=parser.parse_args();data=args.data_directory.resolve();database=data/'workbench.sqlite3'
inventory=json.loads(args.inventory.read_text(encoding='utf-8'))
connection=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
if connection.execute("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1").fetchone():raise RuntimeError('请等待已有任务结束后再初始化')
plans=[]
for material in inventory['materials']:
    path=Path(inventory['material_directory'])/material['name']
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    matches=[dict(r) for r in connection.execute('SELECT * FROM files WHERE sha256=? AND active=1',(digest,))]
    if not matches:raise RuntimeError('原件尚未登记：'+path.name)
    kind='course_guidance' if path.suffix.lower()=='.pptx' else 'grading_criteria' if path.name=='智评综合等级划分说明.pdf' else 'writing_sample'
    for original in matches:
        additions=['grading'] if kind=='grading_criteria' else ['writing_guidance'] if kind=='course_guidance' else []
        plans.append({'file_id':original['id'],'project_id':original['project_id'],'name':original['name'],'source_kind':kind,'additional_usages':additions})
sbt=[dict(r) for r in connection.execute('SELECT * FROM files WHERE id=? AND sha256=? AND active=1',('a9065698848a4300a33c6f04f37ef68c','bf115f906774dacbbcad8d925dc208b85d57301c364e10f8ee53ca5c06aa9d35'))]
if len(sbt)!=1:raise RuntimeError('SBTCert原件校验不一致')
original=sbt[0];plans.append({'file_id':original['id'],'project_id':original['project_id'],'name':original['name'],'source_kind':'research_publication','additional_usages':['research']})
for paper in inventory['literature']:
    if not connection.execute('SELECT id FROM files WHERE project_id=? AND sha256=? AND active=1',(original['project_id'],paper['sha256'])).fetchone():raise RuntimeError('缺少文献原件：'+paper['path'])
connection.close()
if not args.apply:
    print(json.dumps({'plan':plans,'write_performed':False},ensure_ascii=False,indent=2));raise SystemExit(0)
backup=data/'backups'/('before-document-reading-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite3');backup.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as source:
    destination=sqlite3.connect(backup)
    try:source.backup(destination)
    finally:destination.close()
os.environ['PAPER_DATA_DIR']=str(data);os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
from app import db,document_reading as reader,material_policy as policy,knowledge_units as units,knowledge_service as service
db.init()

def fingerprint(table):
    digest=hashlib.sha256()
    for row in db.rows('SELECT * FROM '+table+' ORDER BY id'):digest.update(db.dumps(row).encode());digest.update(b'\n')
    return digest.hexdigest()

tables=('files','sources','bibliography','revisions','citations','rules','rule_packages','facts','artifacts')
before={table:fingerprint(table) for table in tables}
old_heads=db.rows('SELECT * FROM knowledge_heads ORDER BY file_id')
vector_hash=hashlib.sha256()
old_vectors={}
for row in db.rows('SELECT chunk_id,model,text_hash,vector FROM knowledge_unit_vectors ORDER BY chunk_id,model'):
    vector_hash.update(row['chunk_id'].encode());vector_hash.update(row['text_hash'].encode());vector_hash.update(row['vector'])
    old_vectors[(row['chunk_id'],row['model'])]=hashlib.sha256(row['text_hash'].encode()+row['vector']).hexdigest()
old_vector_hash=vector_hash.hexdigest()
report={'backup':str(backup),'plans':plans,'external_requests':False,'human_semantic_acceptance':False,'original_fingerprints_before':before,'existing_heads':old_heads,'existing_vectors_hash':old_vector_hash,'structures':[]}
try:
    for plan in plans:
        current=db.one('SELECT * FROM files WHERE id=?',(plan['file_id'],))
        usages=sorted(set(policy.usages(current))|set(plan['additional_usages']))
        policy.set_usages(plan['project_id'],plan['file_id'],{'usages':usages,'source_kind':plan['source_kind'],'note':'依据用户提供的文献包和格式资料核对来源类型；保留主用途、原件、来源及历史引用，未作语义通过判定'})
    for pid in sorted({p['project_id'] for p in plans}):
        built=service.build_project(pid,{'preserve_head':True},lambda phase,value:print(json.dumps({'phase':phase,'progress':value},ensure_ascii=False),flush=True))
        if built['failures']:raise RuntimeError(db.dumps(built['failures']))
        report.setdefault('research_builds',[]).append(built)
    for plan in plans:
        if plan['source_kind']=='research_publication':continue
        directory=reader.outline(plan['project_id'],plan['file_id'],'guidance',ensure=True)
        report['structures'].append({'file_id':plan['file_id'],'kind':plan['source_kind'],'parse_id':directory['parse_id'],'units':directory['unit_count']})
        print(json.dumps({'phase':'指导资料结构保留','file':plan['name'],'units':directory['unit_count']},ensure_ascii=False),flush=True)
    pid=original['project_id'];research=reader.catalog(pid,'research',limit=200);guidance=reader.catalog(pid,'guidance',limit=200)
    present={r['file_id'] for r in research['documents']}
    report['research_total']=research['total'];report['guidance_total']=guidance['total']
    report['all_31_archive_papers_admitted']=all(any(r['id'] in present for r in db.rows('SELECT id FROM files WHERE project_id=? AND sha256=?',(pid,p['sha256']))) for p in inventory['literature'])
    report['sbtcert_primary_role']=db.one('SELECT role FROM files WHERE id=?',(sbt[0]['id'],))['role']
    report['sbtcert_usages']=policy.usages(db.one('SELECT * FROM files WHERE id=?',(sbt[0]['id'],)))
    report['existing_heads_preserved']=all(db.one('SELECT parse_id FROM knowledge_heads WHERE file_id=?',(h['file_id'],))['parse_id']==h['parse_id'] for h in old_heads)
    current_vectors={(r['chunk_id'],r['model']):hashlib.sha256(r['text_hash'].encode()+r['vector']).hexdigest() for r in db.rows('SELECT chunk_id,model,text_hash,vector FROM knowledge_unit_vectors')}
    report['existing_vectors_preserved']=all(current_vectors.get(key)==value for key,value in old_vectors.items())
    slide=db.one("SELECT u.id FROM knowledge_units u JOIN knowledge_heads h ON h.parse_id=u.parse_id WHERE h.file_id=? AND json_extract(u.locator,'$.slide_number')=6",('a346aa5f45de4ec1a455ccad22d35db2',))
    checked=units.get(pid,slide['id'],intent='guidance') if slide else None
    report['real_teaching_example_scope_preserved']=bool(checked and '正确示例' in checked['text'] and '实现了证书哈希上链与链上验证功能' in checked['text'] and checked['content_roles']['contains_teaching_examples'] and not checked['material_use']['can_prove_project_implementation'])
    report['original_fingerprints_after']={table:fingerprint(table) for table in tables}
    report['original_records_preserved']=before==report['original_fingerprints_after']
    with db.connect() as c:
        report['integrity_check']=c.execute('PRAGMA integrity_check').fetchone()[0]
        report['foreign_key_violations']=[list(r) for r in c.execute('PRAGMA foreign_key_check')]
    assert report['all_31_archive_papers_admitted'] and report['existing_heads_preserved'] and report['existing_vectors_preserved'] and report['real_teaching_example_scope_preserved'] and report['original_records_preserved'] and report['integrity_check']=='ok' and not report['foreign_key_violations']
finally:
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(args.report),'backup':str(backup),'research_total':report['research_total'],'original_records_preserved':report['original_records_preserved']},ensure_ascii=False),flush=True)
